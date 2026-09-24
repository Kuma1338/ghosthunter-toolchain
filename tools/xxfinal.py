r"""THE REPLAY: SetPre(boxCom) + ServerTryActivateAbility(0x7E, 1).
Auto-locks the box he faces; watches for open + loot."""
import ctypes
import struct
import sys
import time

sys.path.insert(0, r'D:\gh_tools\tools')
import uemem
from uemem import UE, PAWN_IS, IS_PRE, IS_CURSTART, TB_STATE  # noqa: E402

k32 = ctypes.WinDLL('kernel32')
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
HANDLE = 0x7E   # this match's interact ability handle (captured live)

ue = UE()
pawn = ue.pawn()
isCom = ue.rq(pawn + PAWN_IS)
asc = ue.rq(pawn + 0xC38)
uf_setpre = ue.find_function(ue.class_of(isCom), 'ServerSetPreBeInteractComponent')
uf_try = ue.find_function(ue.class_of(asc), 'ServerTryActivateAbility')
print(f"pawn={pawn:X} isCom={isCom:X} ASC={asc:X}")
print(f"UF SetPre={uf_setpre:X} TryActivate={uf_try:X}")

hm = k32.OpenFileMappingW(0xF001F, 0, 'XIXING_SHARED_V1')
m = k32.MapViewOfFile(hm, 0xF001F, 0, 0, 21160)
buf = (ctypes.c_char * 21160).from_address(m)
magic, ver, pend, done, okf, pump, logc = struct.unpack('<7I', bytes(buf[:28]))
p1 = pump
time.sleep(0.8)
p2 = struct.unpack('<I', bytes(buf[20:24]))[0]
print(f"queue: hookOk={okf:08X} pump {p1}->{p2}")
assert p2 > p1, "queue not pumping"

h = k32.OpenProcess(0x1F0FFF, 0, ue.pid)

def fire(this, func, parms, label):
    pend, done = struct.unpack('<II', bytes(buf[8:16]))
    params = k32.VirtualAllocEx(h, None, 320, 0x3000, 0x04)
    w = ctypes.c_size_t()
    k32.WriteProcessMemory(h, params, parms, len(parms), ctypes.byref(w))
    slot = (done + pend) % 16
    off = CMD_OFF + slot * CMD_STRIDE
    buf[off:off+CMD_STRIDE] = struct.pack('<IIQQQQ', 1, 0, this, func, params, 0)
    time.sleep(0.01)
    buf[8:12] = struct.pack('<I', pend + 1)
    for _ in range(20):
        time.sleep(0.1)
        d2 = struct.unpack('<I', bytes(buf[12:16]))[0]
        if d2 > done:
            break
    print(f"  [{label}] sent")

print("waiting for prompt (stand at a FRESH box, do NOT press F)...")
target = 0
t0 = time.time()
while time.time() - t0 < 120:
    target = ue.rq(isCom + IS_PRE)
    if target > 0x10000 and ue.ri(ue.rq(target + 0x20) + TB_STATE) != 3:
        break
    time.sleep(0.3)
if not target:
    print("no prompt")
    sys.exit(1)
box = ue.rq(target + 0x20)
print(f"TARGET com={target:X} box={box:X} state={ue.ri(box + TB_STATE)}")

# THE F PRESS REPLAY
fire(isCom, uf_setpre, struct.pack('<Q', target), 'SetPre(boxCom)')
time.sleep(0.4)
fire(asc, uf_try, struct.pack('<IIQQ', HANDLE, 1, 0, 0), 'TryActivate(0x7E, 1)')

print("watching 20s (channel ~6s)...")
for i in range(40):
    time.sleep(0.5)
    st = ue.ri(box + TB_STATE)
    cur = ue.rq(isCom + IS_CURSTART)
    rec = ue.read(target + 0xCF8, 8)
    recq = int.from_bytes(rec, 'little') if rec else 0
    if (i + 1) % 2 == 0 or st == 3:
        print(f"  t+{(i+1)*0.5:.1f} state={st} curStart={cur:X} comRec={recq:X}")
    if st == 3:
        print(">>> BOX OPENED - checking for loot...")
        # count drops near the box
        drops = 0
        bpos = ue.actor_pos(box)
        for lv in ue.levels():
            for a in ue.actors(lv):
                if ue.class_name(a) == 'BP_DropInteract_C':
                    p = ue.actor_pos(a)
                    if p and bpos:
                        d = sum((x-y)**2 for x, y in zip(p, bpos))
                        if d < 6250000:  # 25m radius
                            drops += 1
        print(f">>> DROPS near box: {drops}")
        break
