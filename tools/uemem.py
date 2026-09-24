r"""Pure-Python UE4 reader for GhostHunterClientSteam (no CE dependency).

All layouts verified live (see HANDOFF.md). Uses ReadProcessMemory only.
"""
import ctypes
import struct
import sys
from ctypes import wintypes

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
psapi = ctypes.WinDLL("psapi")

kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.ReadProcessMemory.restype = wintypes.BOOL
kernel32.ReadProcessMemory.argtypes = [wintypes.HANDLE, wintypes.LPCVOID, wintypes.LPVOID,
                                       ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
psapi.EnumProcesses.argtypes = [ctypes.POINTER(wintypes.DWORD), wintypes.DWORD,
                                ctypes.POINTER(wintypes.DWORD)]
psapi.EnumProcessModulesEx.restype = wintypes.BOOL
psapi.EnumProcessModulesEx.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.HMODULE),
                                       wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.DWORD]
psapi.GetModuleFileNameExW.restype = wintypes.DWORD
psapi.GetModuleFileNameExW.argtypes = [wintypes.HANDLE, wintypes.HMODULE, wintypes.LPWSTR, wintypes.DWORD]

GAME_NAME = "GhostHunterClientSteam-Win64-Shipping.exe"

# module-relative offsets (verified)
OFF_GNAMES = 0xAFCAF40
OFF_GWORLD = 0xB11BB68
OFF_GWORLD2 = 0xB118F20
OFF_GENGINE = 0xB11EB40
# object layout
OBJ_CLASS = 0x10
OBJ_NAME = 0x18
OBJ_OUTER = 0x20
# struct layout
STRUCT_SUPER = 0x40
STRUCT_CHILDREN = 0x48
STRUCT_CHILDPROPS = 0x50
# field layout
FIELD_NEXT = 0x18
FIELD_NAME = 0x20
FIELD_OFFSET = 0x44
UFIELD_NEXT = 0x28  # UField::Next (children chain, verified)
# world
WORLD_PERSISTENT = 0x30
WORLD_STREAMING = 0x90
WORLD_GAMEINSTANCE = 0x228
STREAMING_LOADED = 0x190
LEVEL_ACTORS = 0xA0
# pawn
PAWN_COM = 0x1320       # BP_BeInteractCom
PAWN_IS = 0x1328        # IS_Interact
IS_PRE = 0x230          # PreBeInteractComponent
IS_CURSTART = 0x238     # CurStartInteractComponent
IS_TAG = 0x2A0          # TraceDebugTypeTag (FName idx)
PAWN_BINTERACT = 0x1A50
# actor
ACTOR_ROOT = 0x1B8
ROOT_BOUNDS = 0x110     # 3 x double world pos
# boxes
TB_STATE = 0x2B8
TB_COM = 0x338
TOOL_COM = 0x2C8
DROP_PICKED = 0x740


FORCE_PID = None  # set by tools to pin the live game process


def find_game():
    if FORCE_PID:
        h = kernel32.OpenProcess(0x0410, False, FORCE_PID)
        if h:
            return h, FORCE_PID
    pids = (wintypes.DWORD * 1024)()
    needed = wintypes.DWORD()
    if not psapi.EnumProcesses(pids, ctypes.sizeof(pids), ctypes.byref(needed)):
        return None
    count = needed.value // 4
    for i in range(count):
        pid = pids[i]
        h = kernel32.OpenProcess(0x0410, False, pid)  # QUERY_INFO | VM_READ
        if not h:
            continue
        mods = (wintypes.HMODULE * 8)()
        cb = wintypes.DWORD()
        if psapi.EnumProcessModulesEx(h, mods, ctypes.sizeof(mods), ctypes.byref(cb), 3):
            name = ctypes.create_unicode_buffer(260)
            psapi.GetModuleFileNameExW(h, mods[0], name, 260)
            if GAME_NAME in name.value:
                return h, pid
        kernel32.CloseHandle(h)
    return None


class UE:
    def __init__(self):
        r = find_game()
        if not r:
            raise RuntimeError("game not running")
        self.h, self.pid = r
        # module base: first module IS the exe
        mods = (wintypes.HMODULE * 8)()
        cb = wintypes.DWORD()
        psapi.EnumProcessModulesEx(self.h, mods, ctypes.sizeof(mods), ctypes.byref(cb), 3)
        self.base = mods[0]
        self.gnames = self.base + OFF_GNAMES

    # ---- raw reads ----
    def read(self, addr, size):
        buf = ctypes.create_string_buffer(size)
        got = ctypes.c_size_t()
        if not kernel32.ReadProcessMemory(self.h, ctypes.c_void_p(addr), buf, size, ctypes.byref(got)):
            return None
        return buf.raw[:got.value]

    def rq(self, a):
        b = self.read(a, 8)
        return struct.unpack("<Q", b)[0] if b and len(b) == 8 else 0

    def ri(self, a):
        b = self.read(a, 4)
        return struct.unpack("<I", b)[0] if b and len(b) == 4 else 0

    # ---- names ----
    def fname(self, idx):
        if not idx or idx < 0:
            return None
        block = idx >> 16
        offset = (idx & 0xFFFF) * 2
        if block > 8000:
            return None
        blk = self.rq(self.gnames + 0x10 + block * 8)
        if blk < 0x10000:
            return None
        hb = self.read(blk + offset, 2)
        if not hb or len(hb) < 2:
            return None
        h = hb[0] | (hb[1] << 8)
        wide = h & 1
        ln = h >> 6
        if ln <= 0 or ln > 250:
            return None
        sb = self.read(blk + offset + 2, ln)
        if not sb or len(sb) < ln:
            return None
        try:
            if wide:
                if ln % 2:
                    return None
                u = sb.decode("utf-16-le", errors="strict")
                return u.split("\x00")[0] if u else None
            return sb.decode("ascii", errors="strict").split("\x00")[0]
        except UnicodeDecodeError:
            return None

    # ---- reflection ----
    def class_of(self, obj):
        c = self.rq(obj + OBJ_CLASS)
        return c if c > 0x10000 else 0

    def class_name(self, obj):
        c = self.class_of(obj)
        if not c:
            return None
        return self.fname(self.ri(c + OBJ_NAME))

    def obj_name(self, obj):
        return self.fname(self.ri(obj + OBJ_NAME))

    def find_field(self, cls, want):
        depth = 0
        while cls and cls > 0x10000 and depth < 14:
            pc = self.rq(cls + STRUCT_CHILDPROPS)
            i = 0
            while pc and pc > 0x10000 and i < 400:
                nm = self.fname(self.ri(pc + FIELD_NAME))
                if nm == want:
                    return pc
                pc = self.rq(pc + FIELD_NEXT)
                i += 1
            cls = self.rq(cls + STRUCT_SUPER)
            depth += 1
        return 0

    def field_off(self, cls, want):
        f = self.find_field(cls, want)
        return self.ri(f + FIELD_OFFSET) if f else None

    def find_function(self, cls, want):
        """UFunction* by name via the Children chain (UField::Next @ +0x28)."""
        depth = 0
        while cls and cls > 0x10000 and depth < 16:
            c = self.rq(cls + STRUCT_CHILDREN)
            i = 0
            while c and c > 0x10000 and i < 600:
                cc = self.class_name(c)
                if cc == "Function":
                    if self.fname(self.ri(c + OBJ_NAME)) == want:
                        return c
                c = self.rq(c + UFIELD_NEXT)
                i += 1
            cls = self.rq(cls + STRUCT_SUPER)
            depth += 1
        return 0

    # ---- world / pawn ----
    def world(self):
        w = self.rq(self.base + OFF_GWORLD)
        if w < 0x10000 or self.rq(w + WORLD_PERSISTENT) < 0x10000:
            w = self.rq(self.base + OFF_GWORLD2)
        return w if w > 0x10000 else 0

    def pawn(self):
        w = self.world()
        if not w:
            return 0
        gi = self.rq(w + WORLD_GAMEINSTANCE)
        if gi < 0x10000:
            return 0
        off = self.field_off(self.class_of(gi), "LocalPlayers")
        if off is None:
            return 0
        lp0 = self.rq(self.rq(gi + off))
        if lp0 < 0x10000:
            return 0
        off = self.field_off(self.class_of(lp0), "PlayerController")
        if off is None:
            return 0
        pc = self.rq(lp0 + off)
        if pc < 0x10000:
            return 0
        po = self.field_off(self.class_of(pc), "Pawn")
        p = self.rq(pc + po) if po else 0
        if p < 0x10000:
            ao = self.field_off(self.class_of(pc), "AcknowledgedPawn")
            p = self.rq(pc + ao) if ao else 0
        return p if p > 0x10000 else 0

    def levels(self):
        w = self.world()
        if not w:
            return []
        out = []
        pl = self.rq(w + WORLD_PERSISTENT)
        if pl > 0x10000:
            out.append(pl)
        arr = self.rq(w + WORLD_STREAMING)
        num = self.ri(w + WORLD_STREAMING + 8)
        if arr > 0x10000 and 0 < num < 500:
            entries = self.read(arr, num * 8)
            if entries:
                for i in range(num):
                    lvs = struct.unpack_from("<Q", entries, i * 8)[0]
                    if lvs > 0x10000:
                        loaded = self.rq(lvs + STREAMING_LOADED)
                        if loaded > 0x10000:
                            out.append(loaded)
        return out

    def actors(self, level):
        arr = self.rq(level + LEVEL_ACTORS)
        num = self.ri(level + LEVEL_ACTORS + 8)
        if arr < 0x10000 or num == 0 or num > 200000:
            return []
        data = self.read(arr, num * 8)
        if not data:
            return []
        return [struct.unpack_from("<Q", data, i * 8)[0]
                for i in range(num) if struct.unpack_from("<Q", data, i * 8)[0] > 0x10000]

    def actor_pos(self, actor):
        root = self.rq(actor + ACTOR_ROOT)
        if root < 0x10000:
            return None
        b = self.read(root + ROOT_BOUNDS, 24)
        return struct.unpack("<ddd", b) if b and len(b) == 24 else None

    def find_class_by_actor_name(self, name):
        for lv in self.levels():
            for a in self.actors(lv):
                if self.class_name(a) == name:
                    return self.class_of(a)
        return 0


if __name__ == "__main__":
    ue = UE()
    print("pid=%d base=%X" % (ue.pid, ue.base))
    w = ue.world()
    print("world=%X %s" % (w, ue.obj_name(w) if w else "-"))
    p = ue.pawn()
    print("pawn=%X %s" % (p, ue.class_name(p) if p else "-"))
    if p:
        isCom = ue.rq(p + PAWN_IS)
        com = ue.rq(p + PAWN_COM)
        print("isCom=%X %s | playerCom=%X %s" % (isCom, ue.class_name(isCom), com, ue.class_name(com)))
        print("pre=%X tag=%X" % (ue.rq(isCom + IS_PRE), ue.ri(isCom + IS_TAG)))
        cls = ue.class_of(p)
        for fn in ("ServerInteractTurn", "Server_RequestPickupByUI", "K2_SetActorLocation"):
            print("UF %s = %X" % (fn, ue.find_function(cls, fn)))
