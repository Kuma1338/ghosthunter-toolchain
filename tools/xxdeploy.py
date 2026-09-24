r"""One-shot deploy: find live game pid -> inject xixing3.dll -> arm RPC logger."""
import ctypes
import ctypes.wintypes as wt
import struct
import subprocess
import sys
import time

k32 = ctypes.WinDLL("kernel32")
psapi = ctypes.WinDLL("psapi")
k32.OpenProcess.restype = wt.HANDLE
k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
psapi.EnumProcesses.argtypes = [ctypes.POINTER(wt.DWORD), wt.DWORD, ctypes.POINTER(wt.DWORD)]
psapi.EnumProcessModulesEx.restype = wt.BOOL
psapi.EnumProcessModulesEx.argtypes = [wt.HANDLE, ctypes.POINTER(wt.HMODULE), wt.DWORD, ctypes.POINTER(wt.DWORD), wt.DWORD]
psapi.GetModuleFileNameExW.restype = wt.DWORD
psapi.GetModuleFileNameExW.argtypes = [wt.HANDLE, wt.HMODULE, wt.LPWSTR, wt.DWORD]

pid = None
pids = (wt.DWORD * 1024)()
needed = wt.DWORD()
psapi.EnumProcesses(pids, ctypes.sizeof(pids), ctypes.byref(needed))
for i in range(needed.value // 4):
    h = k32.OpenProcess(0x0410, False, pids[i])
    if not h:
        continue
    mods = (wt.HMODULE * 4)()
    cb = wt.DWORD()
    if psapi.EnumProcessModulesEx(h, mods, ctypes.sizeof(mods), ctypes.byref(cb), 3):
        name = ctypes.create_unicode_buffer(260)
        psapi.GetModuleFileNameExW(h, mods[0], name, 260)
        if "GhostHunterClientSteam-Win64" in name.value:
            pid = pids[i]
    k32.CloseHandle(h)
    if pid:
        break
if not pid:
    print("game not running")
    sys.exit(1)
print(f"game pid: {pid}")

r = subprocess.run([sys.executable, r"D:\gh_tools\tools\xxinject.py", str(pid),
                    "D:/gh_tools/overlay/build/xixing4.dll"], capture_output=True, text=True)
print(r.stdout.strip())
if "SUCCESS" not in r.stdout:
    sys.exit(1)
time.sleep(4)

sys.path.insert(0, r"D:\gh_tools\tools")
import time as _t
import uemem
uemem.FORCE_PID = pid
from uemem import UE
ue = UE()
pawn = ue.pawn()
# WAIT for the player pawn: deploying in the lobby arms the RPC logger with
# filter=0 and the calibration F press is silently lost. Wait until in-match.
_wait = 0
while not pawn and _wait < 120:
    _t.sleep(2)
    _wait += 2
    try:
        pawn = ue.pawn()
    except Exception:
        pawn = 0
    if _wait % 10 == 0:
        print(f"waiting for player pawn... ({_wait}s)")
if not pawn:
    print("NO PAWN after 120s - not in a match? Logger NOT armed.")
    print("Re-run this script after entering the match.")
    sys.exit(1)
asc = ue.rq(pawn + 0xC38)

P = ctypes.c_void_p
k32.OpenFileMappingW.restype = P
k32.OpenFileMappingW.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_wchar_p]
k32.MapViewOfFile.restype = P
k32.MapViewOfFile.argtypes = [P, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_size_t]
h = k32.OpenFileMappingW(0xF001F, 0, "XIXING_RPC_V1")
assert h, "RPC section missing - DLL init failed"
m = k32.MapViewOfFile(h, 0xF001F, 0, 0, 8 + 512 * 144)
buf = (ctypes.c_char * (8 + 512 * 144)).from_address(m)
magic, ver, count = struct.unpack("<III", bytes(buf[:12]))
assert magic == 0x58495852, "bad RPC section"
buf[8:12] = struct.pack("<I", 0)
buf[16:24] = struct.pack("<Q", asc)

# CRITICAL: flush any stale commands left by a crashed session - draining
# them later faults on dead actor pointers (crash cause #7).
stale_pend, stale_done = struct.unpack("<II", bytes(buf[8:16]))
if stale_pend > 0:
    print(f"flushing {stale_pend} stale queued commands (crash guard)")
    buf[8:12] = struct.pack("<I", 0)
    buf[12:16] = struct.pack("<I", stale_done + stale_pend)
print(f"DEPLOYED: pawn={pawn:X} ASC={asc:X} - RPC logger ARMED, queue clean")
print("next: press F on one box, then run xxcalib.py")
