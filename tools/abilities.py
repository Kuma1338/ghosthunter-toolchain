import sys, struct
sys.path.insert(0, r'D:\gh_tools\tools')
import uemem
from uemem import UE
ue = UE()
pawn = ue.pawn()
if not pawn: print("no pawn"); sys.exit(1)
asc = ue.rq(pawn + 0xC38)
print(f"pawn={pawn:X} asc={asc:X} ({ue.class_name(asc)})")
# ActivatableAbilities TArray @ ASC+0x418
arr = ue.rq(asc + 0x418); num = ue.ri(asc + 0x418 + 8)
print(f"ActivatableAbilities @+0x418: ptr={arr:X} num={num}")
if not arr or arr < 0x10000 or num<=0 or num>200:
    # try nearby offsets for the spec array
    for off in (0x410,0x418,0x420,0x428,0x430):
        a=ue.rq(asc+off); n=ue.ri(asc+off+8)
        print(f"  probe +0x{off:X}: ptr={a:X} num={n}")
    sys.exit()
# read the buffer; guess spec stride by scanning for valid UGameplayAbility* + handle
buf = ue.read(arr, num*0x100 if num*0x100 < 0x4000 else 0x4000)
print(f"buffer {len(buf)} bytes")
# scan for valid UObject pointers (ability instances) and their class names
seen=[]
for off in range(0, len(buf)-8, 8):
    p = struct.unpack('<Q', buf[off:off+8])[0]
    if 0x10000 < p < 0x7fffffffffff:
        try:
            cls = ue.class_of(p)
            cn = ue.obj_name(cls) if cls and cls>0x10000 else None
        except: cn=None
        if cn and ('Ability' in cn or 'GA_' in cn or 'Interact' in cn or 'Skill' in cn or 'BP_' in cn):
            # look for a handle int in the 0x40 bytes before this ptr
            lo = max(0, off-0x40)
            handles=[]
            for h in range(lo, off, 4):
                v = struct.unpack('<i', buf[h:h+4])[0]
                if 1 <= v <= 0x200: handles.append((h-off, v))
            seen.append((off, p, cn, handles))
print(f"\n=== {len(seen)} ability instances found ===")
for off,p,cn,handles in seen[:40]:
    hs = " ".join(f"[{d:+d}]={v}(0x{v:X})" for d,v in handles[-4:])
    print(f"  buf+0x{off:03X} ability={cn:40} nearbyHandles: {hs}")
