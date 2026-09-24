r"""Vacuum v2: ONE-AT-A-TIME drop pickup with liveness re-checks.

The rapid-fire version crashed the game: pickup destroys the drop actor
server-side, and later commands in the same burst referenced freed memory.
This version fires one drop at a time and confirms each pickup."""
import ctypes
import struct
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import uemem
from uemem import UE, DROP_PICKED  # noqa: E402

k32 = ctypes.WinDLL("kernel32")
P = ctypes.c_void_p
k32.OpenProcess.restype = P
k32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
k32.VirtualAllocEx.restype = P
k32.VirtualAllocEx.argtypes = [P, P, ctypes.c_size_t, ctypes.c_uint32, ctypes.c_uint32]
k32.WriteProcessMemory.restype = ctypes.c_int
k32.WriteProcessMemory.argtypes = [P, P, P, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
k32.OpenFileMappingW.restype = P
k32.OpenFileMappingW.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_wchar_p]
k32.MapViewOfFile.restype = P
k32.MapViewOfFile.argtypes = [P, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_size_t]

CMD_OFF, CMD_STRIDE = 40, 40
DURATION = float(sys.argv[1]) if len(sys.argv) > 1 else 60.0

ue = UE()
pawn = ue.pawn()
uf_setloc = ue.find_function(ue.class_of(pawn), "K2_SetActorLocation")
uf_pickup = ue.find_function(ue.class_of(pawn), "Server_RequestPickupByUI")
print(f"pawn={pawn:X} UF SetLoc={uf_setloc:X} Pickup={uf_pickup:X}")

hm = k32.OpenFileMappingW(0xF001F, 0, "XIXING_SHARED_V1")
m = k32.MapViewOfFile(hm, 0xF001F, 0, 0, 21160)
buf = (ctypes.c_char * 21160).from_address(m)
h = k32.OpenProcess(0x1F0FFF, 0, ue.pid)


def fire(this, func, parms):
    pend, done = struct.unpack("<II", bytes(buf[8:16]))
    params = k32.VirtualAllocEx(h, None, 320, 0x3000, 0x04)
    w = ctypes.c_size_t()
    k32.WriteProcessMemory(h, params, parms, len(parms), ctypes.byref(w))
    slot = (done + pend) % 16
    off = CMD_OFF + slot * CMD_STRIDE
    buf[off:off + CMD_STRIDE] = struct.pack("<IIQQQQ", 1, 0, this, func, params, 0)
    time.sleep(0.02)
    buf[8:12] = struct.pack("<I", pend + 1)


def scan_drops():
    out = []
    for lv in ue.levels():
        for a in ue.actors(lv):
            if ue.class_name(a) == "BP_DropInteract_C" and not ue.ri(a + DROP_PICKED):
                out.append(a)
    return out


def alive_drop(a):
    return ue.class_name(a) == "BP_DropInteract_C" and not ue.ri(a + DROP_PICKED)


done = 0
t0 = time.time()
while time.time() - t0 < DURATION:
    drops = scan_drops()
    if not drops:
        break
    a = drops[0]
    if not alive_drop(a):
        continue
    ppos = ue.actor_pos(pawn)
    if not ppos:
        break
    jx = ((done % 5) - 2) * 40.0
    jy = (((done // 5) % 5) - 2) * 40.0
    loc = (ppos[0] + jx, ppos[1] + jy, ppos[2] + 15.0)
    # K2_SetActorLocation: NewLocation@0(24B) bSweep@0x18 FHitResult@0x20(0x100) bTeleport@0x120
    parms = struct.pack("<ddd", *loc) + bytes(0x18 - 24) + b"\x00" + bytes(0x120 - 0x19) + b"\x01" + bytes(8)
    fire(a, uf_setloc, parms)
    time.sleep(0.15)
    if not alive_drop(a):
        continue
    # Server_RequestPickupByUI: DropItem@0 TargetGNum@8 TargetBType@0xC
    fire(pawn, uf_pickup, struct.pack("<QIB", a, 0, 0) + bytes(5))
    done += 1
    for _ in range(12):
        time.sleep(0.15)
        if not alive_drop(a):
            break

remaining = len(scan_drops())
print(f"vacuumed ~{done} drops, remaining: {remaining}")
if remaining == 0 and done > 0:
    print(">>> ALL DROPS VACUUMED <<<")
