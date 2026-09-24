# dump_rpc_params.py - dump UFunction param layouts for dev RPCs + hunt
# attribute sets (GAS) + normal-attack ability config. READ-ONLY.
import sys, struct, subprocess
sys.path.insert(0, r'D:\gh_tools\tools')

def find_game():
    out = subprocess.run(['tasklist', '/FI',
                          'IMAGENAME eq GhostHunterClientSteam-Win64-Shipping.exe'],
                         capture_output=True, text=True,
                         encoding='gbk', errors='ignore').stdout
    for l in out.split('\n') if False else out.split('\n'):
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

def rf32(a): return struct.unpack('<f', ue.read(a, 4))[0]

pawn = ue.pawn()
if not pawn or pawn < 0x10000:
    print("no pawn"); sys.exit(1)
cls = ue.class_of(pawn)
print(f"pawn={pawn:X} class={ue.obj_name(cls)}")

# ---- A. function param dumps ----
WANT = ['ServerAddAttribute', 'ServeraddATK', 'ServeraddHP', 'ServeraddMP',
        'ServeraddYangQi', 'ServerreplyHP', 'ServerResetCD', 'ReSetCD',
        'ServerWHOSYOURDADDY', 'WHOSYOURDADDY', 'AddModDamage',
        'RemoveModDamage', 'GetAllDamageAdditionScale', 'ServerAddPerspective',
        'NormalAttack', 'AddFullHealth', 'AddHp', 'AddSoul', 'AddMoney',
        'AddItem', 'DebugAddItemInfo', 'PDC_CheckDamagePredicted_NoAdd']

def dump_fn_params(uf, nm):
    print(f"\n--- {nm} @ {uf:X} ---")
    try:
        flags = ue.ri(uf + 0xB0)
    except Exception:
        flags = 0
    net = ''
    if flags & 0x200000: net = 'Server(client->server)'
    elif flags & 0x1000000: net = 'Client(server->client)'
    elif flags & 0x4000: net = 'Multicast'
    else: net = 'local'
    print(f"  flags={flags:08X} [{net}]")
    pc = ue.rq(uf + 0x50)  # STRUCT_CHILDPROPS
    i = 0
    n = 0
    while pc and pc > 0x10000 and i < 40:
        pnm = ue.fname(ue.ri(pc + 0x20))
        off = ue.ri(pc + 0x44)
        esz = ue.ri(pc + 0x34)
        if pnm:
            print(f"  param {pnm:28s} @{off:X} size={esz}")
            n += 1
        pc = ue.rq(pc + 0x18)
        i += 1
    if n == 0:
        print("  (no params)")

for want in WANT:
    uf = ue.find_function(cls, want)
    if uf:
        dump_fn_params(uf, want)
    else:
        print(f"\n--- {want}: NOT FOUND ---")

# ---- B. hunt AttributeSet objects via pointer properties on the pawn ----
print("\n=== AttributeSet hunt (pointer props -> objects) ===")
depth = 0
c = cls
seen_off = set()
attrsets = {}
while c and c > 0x10000 and depth < 16:
    pc = ue.rq(c + 0x50)
    i = 0
    while pc and pc > 0x10000 and i < 800:
        nm = ue.fname(ue.ri(pc + 0x20))
        off = ue.ri(pc + 0x44)
        esz = ue.ri(pc + 0x34)
        if nm and esz == 8 and off not in seen_off:
            seen_off.add(off)
            try:
                obj = ue.rq(pawn + off)
            except Exception:
                obj = 0
            if obj and obj > 0x10000:
                try:
                    ocn = ue.class_name(obj) or ''
                except Exception:
                    ocn = ''
                if 'AttributeSet' in ocn or 'Attribute' in ocn:
                    attrsets[ocn] = obj
        pc = ue.rq(pc + 0x18)
        i += 1
    c = ue.rq(c + 0x40)
    depth += 1

for ocn, obj in attrsets.items():
    print(f"\n[{ocn}] obj={obj:X} float props (attributes):")
    ocls = ue.class_of(obj)
    depth = 0
    c2 = ocls
    while c2 and c2 > 0x10000 and depth < 8:
        pc = ue.rq(c2 + 0x50)
        i = 0
        while pc and pc > 0x10000 and i < 300:
            nm = ue.fname(ue.ri(pc + 0x20))
            off = ue.ri(pc + 0x44)
            esz = ue.ri(pc + 0x34)
            if nm and esz == 4 and not nm.startswith(('b', 'Replicated')) :
                try:
                    v = rf32(obj + off)
                    if 0.01 < abs(v) < 1e9:
                        print(f"  {nm:36s} +{off:X} = {v:.1f}")
                except Exception:
                    pass
            pc = ue.rq(pc + 0x18)
            i += 1
        c2 = ue.rq(c2 + 0x40)
        depth += 1

if not attrsets:
    print("(no AttributeSet objects found via pointer props)")

# ---- C. normal attack ability object ----
print("\n=== NorAttackAbility ===")
na = ue.rq(pawn + 0xED0)
nah = ue.ri(pawn + 0xED8)
print(f"handle={nah} obj={na:X}")
if na and na > 0x10000:
    try:
        ncn = ue.class_name(na)
        print(f"class={ncn} name={ue.obj_name(na)}")
        # dump its props with range/atk/radius/damage keywords
        ncls = ue.class_of(na)
        depth = 0
        c3 = ncls
        while c3 and c3 > 0x10000 and depth < 10:
            pc = ue.rq(c3 + 0x50)
            i = 0
            while pc and pc > 0x10000 and i < 400:
                nm = ue.fname(ue.ri(pc + 0x20))
                off = ue.ri(pc + 0x44)
                esz = ue.ri(pc + 0x34)
                if nm and any(k in nm.lower() for k in
                              ('range', 'radius', 'dist', 'atk', 'attack',
                               'damage', 'dmg', 'sphere', 'box', 'size')):
                    v = ''
                    try:
                        if esz == 4: v = f"= {rf32(na + off):.1f}"
                        elif esz == 8:
                            vv = struct.unpack('<d', ue.read(na + off, 8))[0]
                            v = f"= {vv:.1f}"
                    except Exception:
                        pass
                    print(f"  [{ue.obj_name(c3)}] {nm:36s} +{off:X} esz={esz} {v}")
                pc = ue.rq(pc + 0x18)
                i += 1
            c3 = ue.rq(c3 + 0x40)
            depth += 1
    except Exception as e:
        print(f"read fail: {e}")
print("\ndone")
