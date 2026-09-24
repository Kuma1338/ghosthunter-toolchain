# dumpprice.py - hunt the item price field inside DTItemData of live drops.
# known: DTItemData inline @ drop+0x450, ItemId@+0x458, Name FText@+0x460
#        (PUA garbled), quality@+0x4BA. price lives SOMEWHERE near here.
# anchor data point from Ethan: 九霄八卦晶 = 14062.
# usage: python dumpprice.py          (dump all live drops)
#        python dumpprice.py 14062    (also highlight this value everywhere)
import os
import sys, struct, subprocess
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ANCHOR = int(sys.argv[1]) if len(sys.argv) > 1 else 14062

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
    print("no pawn (not in match)"); sys.exit(1)
ppos = ue.actor_pos(pawn)
print(f"pawn={pawn:X} pos=({ppos[0]:.0f},{ppos[1]:.0f},{ppos[2]:.0f}) anchor={ANCHOR}")

drops = []
for lv in ue.levels():
    for a in ue.actors(lv):
        if ue.class_name(a) == "BP_DropInteract_C" and not ue.ri(a + 0x740):
            pos = ue.actor_pos(a)
            d = sum((x-y)**2 for x, y in zip(pos, ppos))**0.5 if pos else 1e18
            drops.append((d, a))
drops.sort()
print(f"{len(drops)} live drops\n")

# DTItemData window: dump +0x440..0x500 (a bit wider than the assumed range)
LO, HI = 0x440, 0x500
for i, (d, a) in enumerate(drops[:25]):
    q = (ue.ri(a + 0x4BA) & 0xFF) - 2
    itemid = ue.ri(a + 0x458)
    raw = ue.read(a + LO, HI - LO)
    if not raw: continue
    # highlight anchor matches (int32/int64/float)
    hits = []
    for off in range(0, len(raw) - 4, 4):
        v32 = struct.unpack('<I', raw[off:off+4])[0]
        if v32 == ANCHOR: hits.append(f"i32@+{LO+off:X}")
        f = struct.unpack('<f', raw[off:off+4])[0]
        if abs(f - ANCHOR) < 0.5: hits.append(f"f32@+{LO+off:X}")
        v64 = struct.unpack('<Q', raw[off:off+8])[0] if off + 8 <= len(raw) else 0
        if v64 == ANCHOR: hits.append(f"i64@+{LO+off:X}")
    hitstr = ('  <<<< ' + ', '.join(hits)) if hits else ''
    print(f"[{i:2d}] d={d/100:5.0f}m q={q} ItemId={itemid:6d}{hitstr}")
    # hex dump 16 bytes per line with offsets
    for off in range(0, len(raw), 16):
        chunk = raw[off:off+16]
        hexs = ' '.join(f'{b:02X}' for b in chunk)
        # annotate plausible ints/floats on this line
        ann = []
        for k in range(0, len(chunk) - 3, 4):
            v = struct.unpack('<I', chunk[k:k+4])[0]
            if 100 <= v <= 10_000_000:
                f = struct.unpack('<f', chunk[k:k+4])[0]
                if 100 <= f <= 10_000_000 and f != v:
                    ann.append(f"+{LO+off+k:X}:i{v}/f{f:.0f}")
                else:
                    ann.append(f"+{LO+off+k:X}:i{v}")
        print(f"     +{LO+off:X}: {hexs}{'   ' + ' '.join(ann) if ann else ''}")
    print()
print("done - pick up 2-3 items, tell me each price, I correlate by matching values")
