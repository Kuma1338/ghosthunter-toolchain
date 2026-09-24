r"""xxq.py - the ONE queue client for every Python tool.

Why this file exists (2026-09-22):
  xxstar.py / attrfire.py / xxfire*.py each carried their own copy of the submit
  protocol, and every copy shared the same two defects:

    1. None of them read Cmd.result. So "no effect" could not be told apart from
       "never drained" (result=0) or "faulted" (result=0xDEAD). Every experiment
       run through them was therefore uninterpretable - including the 8-shot
       ServerAddAttribute matrix, whose "no effect" verdict is NOT trustworthy.
       HANDOFF S3.7 says outright: "always read result first". Nobody did.

    2. `pending` was bumped with a plain read-modify-write, while the DLL does an
       InterlockedDecrement and the overlay an InterlockedIncrement on the same
       field -> lost updates, on a queue whose own header calls itself SPSC.
       ctypes cannot reach InterlockedIncrement (x64 intrinsic, not a kernel32
       export - verified on this box), so the claim+bump sequence is serialized
       with a named mutex here. NOTE: this only serializes Python producers
       against each other. To also close the Python-vs-overlay race, the overlay
       must take this same mutex (MUTEX_NAME) around its own claim+bump in
       xixing.cpp - that C++ half is NOT done yet. Until then, do not run a
       command-sending tool while the overlay is also firing (hotkeys/M4).

Layout authority: overlay/src/xixing_dll.cpp (struct Shared / struct Cmd).
Do not re-derive these offsets anywhere else - import them from here.
"""
import ctypes
import os
import struct
import subprocess
import sys
import time
from ctypes import wintypes as wt

# ---------------------------------------------------------------- constants
BUILD_DIR = r"D:\gh_tools\overlay\build"
DLL_POINTER = os.path.join(BUILD_DIR, "CURRENT_DLL.txt")
DLL_FALLBACK = "xixing7.dll"

MAGIC = 0x58495847        # 'XIXG'
RPC_MAGIC = 0x58495852    # 'XIXR'
SHARED_NAME = "XIXING_SHARED_V1"
RPC_NAME = "XIXING_RPC_V1"
MUTEX_NAME = "XIXING_PRODUCER_V1"

SHARED_SIZE = 21160
RPC_SIZE = 0x18 + 512 * 144

# struct Shared
OFF_PENDING, OFF_DONESEQ, OFF_HOOKOK, OFF_PUMP = 8, 12, 16, 20
CMD_OFF, CMD_STRIDE, CMD_SLOTS = 40, 40, 16
CMD_RESULT = 32           # Cmd.result, relative to slot start
PARAM_BYTES = 320

# struct RpcShared
RPC_OFF_COUNT, RPC_OFF_FILTER, RPC_OFF_LOG, RPC_STRIDE = 8, 16, 0x18, 144

RES_NEVER_DRAINED, RES_CALLED, RES_SEH = 0, 1, 0xDEAD

MEM_COMMIT_RESERVE, PAGE_RW, MEM_RELEASE = 0x3000, 0x04, 0x8000
ALL_ACCESS, SYNCHRONIZE = 0x1F0FFF, 0x00100000
FILE_MAP_ALL_ACCESS = 0xF001F   # for OpenFileMapping/MapViewOfFile (NOT 0x1F0FFF)

# ---------------------------------------------------------------- win32
P = ctypes.c_void_p
k32 = ctypes.WinDLL("kernel32", use_last_error=True)
for _n, _r, _a in [
    ("OpenFileMappingW", P, [wt.DWORD, wt.BOOL, wt.LPCWSTR]),
    ("MapViewOfFile", P, [P, wt.DWORD, wt.DWORD, wt.DWORD, ctypes.c_size_t]),
    ("UnmapViewOfFile", wt.BOOL, [P]),
    ("OpenProcess", P, [wt.DWORD, wt.BOOL, wt.DWORD]),
    ("CloseHandle", wt.BOOL, [P]),
    ("VirtualAllocEx", P, [P, P, ctypes.c_size_t, wt.DWORD, wt.DWORD]),
    ("VirtualFreeEx", wt.BOOL, [P, P, ctypes.c_size_t, wt.DWORD]),
    ("WriteProcessMemory", wt.BOOL,
     [P, P, P, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]),
    ("CreateMutexW", P, [P, wt.BOOL, wt.LPCWSTR]),
    ("WaitForSingleObject", wt.DWORD, [P, wt.DWORD]),
    ("ReleaseMutex", wt.BOOL, [P]),
]:
    getattr(k32, _n).restype = _r
    getattr(k32, _n).argtypes = _a


def current_dll():
    """The DLL the build script last produced. Single source of truth.

    Three tools used to hardcode three DIFFERENT generations (overlay ->
    xixing4, xxdeploy -> xixing4, xxinject default -> xixing.dll) while the
    source had moved on. That is how two DLL generations ended up hooked into
    the same process. Never hardcode a DLL name again - call this.
    """
    try:
        with open(DLL_POINTER, encoding="utf-8") as f:
            name = f.read().strip()
        if name:
            return os.path.join(BUILD_DIR, name)
    except OSError:
        pass
    print(f"[xxq] WARNING: {DLL_POINTER} missing, falling back to {DLL_FALLBACK}",
          file=sys.stderr)
    return os.path.join(BUILD_DIR, DLL_FALLBACK)


def find_game():
    """pid of the game BODY (never the 28MB launcher of the same name)."""
    out = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq GhostHunterClientSteam-Win64-Shipping.exe"],
        capture_output=True, text=True, encoding="gbk", errors="ignore").stdout
    for line in out.split("\n"):
        if "GhostHunter" in line:
            try:
                return int(line.split()[1])
            except (IndexError, ValueError):
                return None
    return None


def describe(result):
    return {RES_NEVER_DRAINED: "NEVER DRAINED (queue is broken - the command "
                               "was never executed)",
            RES_CALLED: "CALLED (the RPC really ran on the game thread)",
            RES_SEH: "SEH FAULT (0xDEAD - it ran and crashed inside)",
            }.get(result, f"UNKNOWN 0x{result:X}")


class Queue:
    """Command queue client. Always use `with Queue(pid) as q:`."""

    def __init__(self, pid):
        self.pid = pid
        self._buf = None
        self._map = None
        self._rpc = None
        self._rpcmap = None
        self._proc = None
        self._mutex = None
        self._leaked = []

        hm = k32.OpenFileMappingW(FILE_MAP_ALL_ACCESS, False, SHARED_NAME)
        if not hm:
            raise RuntimeError(f"{SHARED_NAME} not found - DLL not injected? "
                               f"(deploy with xxdeploy.py)")
        self._map = k32.MapViewOfFile(hm, FILE_MAP_ALL_ACCESS, 0, 0, SHARED_SIZE)
        k32.CloseHandle(hm)
        if not self._map:
            raise RuntimeError("MapViewOfFile(shared) failed")
        self._buf = (ctypes.c_char * SHARED_SIZE).from_address(self._map)

        hr = k32.OpenFileMappingW(FILE_MAP_ALL_ACCESS, False, RPC_NAME)
        if hr:
            self._rpcmap = k32.MapViewOfFile(hr, FILE_MAP_ALL_ACCESS, 0, 0, RPC_SIZE)
            k32.CloseHandle(hr)
            if self._rpcmap:
                self._rpc = (ctypes.c_char * RPC_SIZE).from_address(self._rpcmap)

        self._proc = k32.OpenProcess(ALL_ACCESS, False, pid)
        if not self._proc:
            raise RuntimeError(f"OpenProcess({pid}) failed "
                               f"err={ctypes.get_last_error()}")
        # not owned: both producers just wait on it
        self._mutex = k32.CreateMutexW(None, False, MUTEX_NAME)

    # ------------------------------------------------------------- internals
    def _u32(self, off):
        return struct.unpack_from("<I", self._buf, off)[0]

    def _u64(self, off):
        return struct.unpack_from("<Q", self._buf, off)[0]

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        if self._proc:
            # self._leaked is deliberately NOT freed here: an undrained command
            # can still execute later and would dereference a released buffer.
            if self._leaked:
                print(f"[xxq] {len(self._leaked)} params buffer(s) left mapped "
                      f"in the game (undrained commands)", file=sys.stderr)
            k32.CloseHandle(self._proc)
            self._proc = None
        if self._mutex:
            k32.CloseHandle(self._mutex)
            self._mutex = None
        for view in (self._map, self._rpcmap):
            if view:
                k32.UnmapViewOfFile(view)
        self._map = self._rpcmap = None
        self._buf = self._rpc = None

    # ------------------------------------------------------------- public
    def health(self):
        """Everything you need before believing any experiment result."""
        pump0 = self._u32(OFF_PUMP)
        time.sleep(0.4)
        pump1 = self._u32(OFF_PUMP)
        return {
            "magic_ok": self._u32(0) == MAGIC,
            "version": self._u32(4),
            "hookOk": self._u32(OFF_HOOKOK) == MAGIC,
            "pump_advancing": pump1 != pump0,
            "pump": pump1,
            "pending": self._u32(OFF_PENDING),
            "doneSeq": self._u32(OFF_DONESEQ),
            "rpc_section": self._rpc is not None
                           and struct.unpack_from("<I", self._rpc, 0)[0] == RPC_MAGIC,
        }

    def assert_healthy(self):
        h = self.health()
        if not h["magic_ok"]:
            raise RuntimeError("shared block magic bad - wrong/no DLL")
        if not h["hookOk"]:
            raise RuntimeError("hookOk NOT armed - the DLL refused to hook "
                               "(signature mismatch? another DLL generation "
                               "already owns ProcessEvent). Restart the game.")
        if not h["pump_advancing"]:
            raise RuntimeError("pump is not advancing - the detour is not "
                               "executing. Do NOT queue anything.")
        return h

    def flush_stale(self):
        """Drop commands a crashed session left queued (crash cause #7/#9).

        xxdeploy.py used to 'do' this against the RPC section by mistake, after
        zeroing the very dword it then read - so the branch was both aimed at
        the wrong mapping and unreachable. It never once fired.
        """
        pend = self._u32(OFF_PENDING)
        if not pend:
            return 0
        done = self._u32(OFF_DONESEQ)
        struct.pack_into("<I", self._buf, OFF_PENDING, 0)
        struct.pack_into("<I", self._buf, OFF_DONESEQ, done + pend)
        for slot in range(CMD_SLOTS):        # disarm every slot as well
            off = CMD_OFF + slot * CMD_STRIDE
            struct.pack_into("<QQQ", self._buf, off + 8, 0, 0, 0)
            struct.pack_into("<I", self._buf, off, 0)
        return pend

    def fire(self, this, func, parms, wait=6.0):
        """Queue one ProcessEvent call. Returns Cmd.result (see describe()).

        Blocks until the DLL reports an outcome, or `wait` seconds elapse.
        """
        if not func or func < 0x10000:
            raise ValueError(f"bad UFunction pointer 0x{func:X}")
        if len(parms) > PARAM_BYTES:
            raise ValueError(f"parms {len(parms)}B > PARAM_BYTES {PARAM_BYTES}")

        params = k32.VirtualAllocEx(self._proc, None, PARAM_BYTES,
                                    MEM_COMMIT_RESERVE, PAGE_RW)
        if not params:
            raise RuntimeError(f"VirtualAllocEx failed "
                               f"err={ctypes.get_last_error()}")
        written = ctypes.c_size_t()
        if parms and not k32.WriteProcessMemory(
                self._proc, params, parms, len(parms), ctypes.byref(written)):
            k32.VirtualFreeEx(self._proc, params, 0, MEM_RELEASE)
            raise RuntimeError(f"WriteProcessMemory failed "
                               f"err={ctypes.get_last_error()}")

        # claim slot + bump pending as one critical section (see module docstring)
        if self._mutex:
            k32.WaitForSingleObject(self._mutex, 5000)
        try:
            pend = self._u32(OFF_PENDING)
            done = self._u32(OFF_DONESEQ)
            if pend >= CMD_SLOTS - 2:
                raise RuntimeError(f"queue nearly full (pending={pend}) - "
                                   f"the DLL is not draining")
            slot = (done + pend) % CMD_SLOTS
            off = CMD_OFF + slot * CMD_STRIDE
            struct.pack_into("<IIQQQQ", self._buf, off,
                             1, 0, this, func, params, 0)
            struct.pack_into("<I", self._buf, OFF_PENDING, pend + 1)
        finally:
            if self._mutex:
                k32.ReleaseMutex(self._mutex)

        deadline = time.time() + wait
        while time.time() < deadline:
            time.sleep(0.05)
            result = struct.unpack_from("<Q", self._buf, off + CMD_RESULT)[0]
            if result:
                # the DLL sets result only AFTER ProcessEvent returned, so the
                # game is provably done with the buffer -> safe to reclaim
                k32.VirtualFreeEx(self._proc, params, 0, MEM_RELEASE)
                return result
        # never drained: the command may still fire later, so the buffer must
        # stay mapped. Deliberate leak, reported rather than hidden.
        self._leaked.append(params)
        print(f"[xxq] command never drained within {wait}s - params buffer "
              f"0x{params:X} intentionally leaked (it may still execute)",
              file=sys.stderr)
        return RES_NEVER_DRAINED

    # ------------------------------------------------------------- rpc ring
    def rpc_arm(self, this_ptr=0):
        """Point the RPC logger at `this_ptr` (0 = record everything)."""
        if self._rpc is None:
            raise RuntimeError("RPC section not mapped")
        struct.pack_into("<I", self._rpc, RPC_OFF_COUNT, 0)
        struct.pack_into("<Q", self._rpc, RPC_OFF_FILTER, this_ptr)

    def rpc_count(self):
        if self._rpc is None:
            return 0
        return struct.unpack_from("<I", self._rpc, RPC_OFF_COUNT)[0]

    def rpc_entry(self, i):
        off = RPC_OFF_LOG + i * RPC_STRIDE
        func, this, tick = struct.unpack_from("<QQI", self._rpc, off)
        return func, this, tick, bytes(self._rpc[off + 20:off + RPC_STRIDE])


def wait_for_match(timeout=0):
    """Block until the game has an in-match pawn. Returns (ue, pid, pawn)."""
    sys.path.insert(0, r"D:\gh_tools\tools")
    started = time.time()
    pid = ue = None
    while True:
        if timeout and time.time() - started > timeout:
            raise TimeoutError("no in-match pawn appeared")
        p = find_game()
        if not p:
            time.sleep(3)
            continue
        if p != pid:
            pid, ue = p, None
            print(f"[xxq] game pid {pid}")
        if ue is None:
            # FORCE_PID is a module global read inside UE.__init__ -> set it,
            # then construct. No reload needed.
            import uemem
            uemem.FORCE_PID = pid
            try:
                ue = uemem.UE()
            except Exception as e:
                print(f"[xxq] UE() not ready ({e}) - retrying")
                ue = None
                time.sleep(2)
                continue
        try:
            pawn = ue.pawn()
            if pawn and pawn > 0x10000:
                cls = ue.obj_name(ue.class_of(pawn)) or ""
                # the main-city pawn is a DIFFERENT class and carries none of
                # the backdoor RPCs - treating it as in-match wastes a run
                if "MainCity" not in cls:
                    return ue, pid, pawn
        except Exception:
            pass
        time.sleep(2)
