import sys, struct
sys.path.insert(0, r'D:\gh_tools\tools')
import uemem
from uemem import UE
ue = UE()
pawn = ue.pawn()
asc = ue.rq(pawn + 0xC38)
sp_off = ue.field_off(ue.class_of(asc), 'SpawnedAttributes')
arr = ue.rq(asc + sp_off); num = ue.ri(asc + sp_off + 8)
print(f"ASC={asc:X} SpawnedAttributes@+{sp_off:X} arr={arr:X} num={num}")
for i in range(max(1,num)):
    inst = ue.rq(arr + i*8)
    print(f"  set[{i}]={inst:X} class={ue.class_name(inst)}")
inst = ue.rq(arr)
print(f"\n=== raw hex of set_instance {inst:X}, +0x00..+0x120 ===")
raw = ue.read(inst, 0x120)
for off in range(0, len(raw), 16):
    chunk = raw[off:off+16]
    hexs = ' '.join(f'{b:02X}' for b in chunk)
    floats = ' '.join(f'{struct.unpack("<f",chunk[k:k+4])[0]:>10.2f}' for k in range(0,16,4))
    print(f"  +{off:03X}: {hexs}   | {floats}")
print("\n=== scan whole set for plausible attribute floats (0.5..99999) ===")
big = ue.read(inst, 0x740)
hits=[]
for off in range(0, len(big)-4, 4):
    v = struct.unpack('<f', big[off:off+4])[0]
    if 0.5 <= v <= 99999 and v==v:
        hits.append((off,v))
for off,v in hits[:60]:
    print(f"  +0x{off:03X} = {v:.3f}")
