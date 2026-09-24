# dump_pawn_fns.py - list ALL functions and properties on the pawn's class
# chain, filtered. goal: find ServeraddATK siblings (range/attack-distance
# dev RPCs) and range-related properties on the character or weapon.
import sys, subprocess
sys.path.insert(0, r'D:\gh_tools\tools')

def find_game():
    out = subprocess.run(['tasklist', '/FI',
                          'IMAGENAME eq GhostHunterClientSteam-Win64-Shipping.exe'],
                         capture_output=True, text=True,
                         encoding='gbk', errors='ignore').stdout
    for l in out.split('\n'):
        if 'GhostHunter' in l:
            return int(l.split()[1])
    return None

pid = find_game()
if not pid:
    print("game not running"); sys.exit(1)

import uemem
uemem.FORCE_PID = pid
from uemem import UE
ue = UE()

pawn = ue.pawn()
if not pawn or pawn < 0x10000:
    print("no pawn (in a match?)"); sys.exit(1)
cls = ue.class_of(pawn)
print(f"pawn={pawn:X} class={ue.obj_name(cls)}")

FN_KEYS = ('add', 'set', 'range', 'dist', 'atk', 'attack', 'hp', 'def',
           'speed', 'move', 'radius', 'size')
PROP_KEYS = ('range', 'dist', 'atk', 'attack', 'weapon', 'jian', 'radius',
             'speed', 'power', 'strength')

print("\n=== FUNCTIONS (pawn class chain) ===")
depth = 0
seen = set()
c = cls
while c and c > 0x10000 and depth < 16:
    cnm = ue.obj_name(c)
    ch = ue.rq(c + 0x48)  # STRUCT_CHILDREN
    i = 0
    fns = []
    while ch and ch > 0x10000 and i < 800:
        if ue.class_name(ch) == "Function":
            nm = ue.fname(ue.ri(ch + 0x18))  # OBJ_NAME
            if nm and nm not in seen:
                seen.add(nm)
                fns.append(nm)
        ch = ue.rq(ch + 0x28)  # UFIELD_NEXT
        i += 1
    hits = [f for f in fns if any(k in f.lower() for k in FN_KEYS)]
    if hits:
        print(f"\n[{cnm}]")
        for f in sorted(hits):
            print(f"  {f}")
    c = ue.rq(c + 0x40)
    depth += 1

print(f"\n(total unique functions seen: {len(seen)})")

print("\n=== PROPERTIES (pawn class chain, keyword filter) ===")
depth = 0
c = cls
while c and c > 0x10000 and depth < 16:
    cnm = ue.obj_name(c)
    pc = ue.rq(c + 0x50)  # STRUCT_CHILDPROPS
    i = 0
    while pc and pc > 0x10000 and i < 600:
        nm = ue.fname(ue.ri(pc + 0x20))
        if nm and any(k in nm.lower() for k in PROP_KEYS):
            off = ue.ri(pc + 0x44)
            esz = ue.ri(pc + 0x34)
            print(f"  [{cnm}] {nm:40s} +{off:X} esz={esz}")
        pc = ue.rq(pc + 0x18)
        i += 1
    c = ue.rq(c + 0x40)
    depth += 1
print("done")
