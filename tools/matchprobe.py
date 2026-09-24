# matchprobe.py - one-shot background probe for the NEXT match.
# 1. wait for pawn (match start)
# 2. verify spawned GhostAI capsules are at class defaults (no pool residue)
# 3. dump pawn-class functions (dev RPC hunt) + range-related properties
# READ-ONLY. exits after dumping. output -> D:\gh_tools\tmp_matchprobe.txt
import sys, os, time, struct, subprocess

sys.path.insert(0, r'D:\gh_tools\tools')
OUT = r'D:\gh_tools\tmp_matchprobe.txt'

def find_game():
    out = subprocess.run(['tasklist', '/FI',
                          'IMAGENAME eq GhostHunterClientSteam-Win64-Shipping.exe'],
                         capture_output=True, text=True,
                         encoding='gbk', errors='ignore').stdout
    for l in out.split('\n'):
        if 'GhostHunter' in l:
            return int(l.split()[1])
    return None

lines = []
def log(s):
    lines.append(s)
    print(s, flush=True)

log("waiting for pawn (enter a match)...")

pawn = 0
ue = None
pid = None
while True:
    p = find_game()
    if not p:
        time.sleep(5); continue
    if p != pid:
        pid = p
        import uemem
        uemem.FORCE_PID = pid
        from uemem import UE
        ue = UE()
        log(f"game pid={pid}")
    try:
        pawn = ue.pawn()
    except Exception:
        pawn = 0
    if pawn and pawn > 0x10000:
        # skip the main-city hub pawn - we want the IN-MATCH character class
        try:
            pcn = ue.obj_name(ue.class_of(pawn)) or ''
        except Exception:
            pcn = ''
        if 'MainCity' in pcn:
            pawn = 0  # keep waiting
            time.sleep(3)
            continue
        break
    time.sleep(3)

log(f"pawn={pawn:X} class={ue.obj_name(ue.class_of(pawn))}")

def rf32(a): return struct.unpack('<f', ue.read(a, 4))[0]

# --- 1. capsule sanity on live monsters ---
caps = []
try:
    for lv in ue.levels():
        for a in ue.actors(lv):
            cn = ue.class_name(a)
            if not (cn and 'GhostAI' in cn): continue
            root = ue.rq(a + 0x1B8)
            if not root or root < 0x10000: continue
            try: caps.append((cn, rf32(root + 0x59C), rf32(root + 0x598)))
            except Exception: pass
except Exception as e:
    log(f"capsule scan error: {e}")
log(f"\ncapsules on {len(caps)} live monsters:")
for cn, r, h in caps[:20]:
    flag = "FAT-RESIDUE!" if r > 250 else "ok"
    log(f"  {flag:13s} {cn[:46]:46s} r={r:.1f} h={h:.1f}")

# --- 2. pawn class function dump ---
cls = ue.class_of(pawn)
FN_KEYS = ('add', 'set', 'range', 'dist', 'atk', 'attack', 'hp', 'def',
           'speed', 'move', 'radius', 'size')
PROP_KEYS = ('range', 'dist', 'atk', 'attack', 'weapon', 'jian', 'radius',
             'speed', 'power', 'strength')

log(f"\n=== FUNCTIONS ({ue.obj_name(cls)} chain) ===")
depth = 0; seen = set(); c = cls
while c and c > 0x10000 and depth < 16:
    cnm = ue.obj_name(c)
    ch = ue.rq(c + 0x48)
    i = 0; fns = []
    while ch and ch > 0x10000 and i < 800:
        try:
            if ue.class_name(ch) == "Function":
                nm = ue.fname(ue.ri(ch + 0x18))
                if nm and nm not in seen:
                    seen.add(nm); fns.append(nm)
        except Exception: pass
        ch = ue.rq(ch + 0x28); i += 1
    hits = [f for f in fns if any(k in f.lower() for k in FN_KEYS)]
    if hits:
        log(f"[{cnm}]")
        for f in sorted(hits): log(f"  {f}")
    c = ue.rq(c + 0x40); depth += 1
log(f"(total unique functions: {len(seen)})")

log("\n=== PROPERTIES (keyword filter) ===")
depth = 0; c = cls
while c and c > 0x10000 and depth < 16:
    cnm = ue.obj_name(c)
    pc = ue.rq(c + 0x50)
    i = 0
    while pc and pc > 0x10000 and i < 600:
        nm = ue.fname(ue.ri(pc + 0x20))
        if nm and any(k in nm.lower() for k in PROP_KEYS):
            off = ue.ri(pc + 0x44); esz = ue.ri(pc + 0x34)
            log(f"  [{cnm}] {nm:40s} +{off:X} esz={esz}")
        pc = ue.rq(pc + 0x18); i += 1
    c = ue.rq(c + 0x40); depth += 1

with open(OUT, 'w', encoding='utf-8') as f:
    f.write('\n'.join(lines))
log(f"\nsaved -> {OUT}")
