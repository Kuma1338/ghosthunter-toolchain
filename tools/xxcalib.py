r"""Calibrate the interact ability handle: reads the RPC logger ring after one
real F press and extracts the ServerTryActivateAbility handle with
InputPressed=1 (the interact input; the movement spam has InputPressed=0)."""
import ctypes
import struct
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import uemem
from uemem import UE  # noqa: E402

k32 = ctypes.WinDLL("kernel32")
P = ctypes.c_void_p
k32.OpenFileMappingW.restype = P
k32.OpenFileMappingW.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_wchar_p]
k32.MapViewOfFile.restype = P
k32.MapViewOfFile.argtypes = [P, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_size_t]

ue = UE()
h = k32.OpenFileMappingW(0xF001F, 0, "XIXING_RPC_V1")
assert h, "RPC section not found - inject xixing3.dll first"
m = k32.MapViewOfFile(h, 0xF001F, 0, 0, 8 + 512 * 144)
buf = (ctypes.c_char * (8 + 512 * 144)).from_address(m)
magic, ver, count = struct.unpack("<III", bytes(buf[:12]))
assert magic == 0x58495852, "bad RPC section"
print(f"{count} RPCs captured")

candidates = {}
for i in range(min(count, 512)):
    off = 24 + i * 144
    func_, this_ = struct.unpack("<QQ", bytes(buf[off:off + 16]))
    parms = bytes(buf[off + 16:off + 144])
    nm = ue.obj_name(func_) if func_ > 0x10000 else None
    if nm == "ServerTryActivateAbility":
        handle = struct.unpack_from("<I", parms, 0)[0]
        pressed = struct.unpack_from("<B", parms, 4)[0]
        print(f"  [{i:03d}] TryActivate handle=0x{handle:X} InputPressed={pressed}")
        if pressed:
            candidates[handle] = candidates.get(handle, 0) + 1

if not candidates:
    print("NO InputPressed=1 activation found - press F on a box first, then rerun")
    sys.exit(1)
best = max(candidates.items(), key=lambda kv: kv[1])[0]
print(f"\nINTERACT HANDLE = 0x{best:X}")
with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "xx_handle.txt"), "w") as f:
    f.write(f"{best}\n")
print("saved to xx_handle.txt")
