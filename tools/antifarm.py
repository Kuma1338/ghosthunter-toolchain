import os
import sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import uemem
from uemem import UE
ue = UE()
def res():
    for _ in range(20):
        try:
            p=ue.pawn()
            if p and p>0x10000 and 'MainCity' not in (ue.obj_name(ue.class_of(p)) or ''): return p
        except: pass
        time.sleep(0.3)
    return 0
pawn=res()
if not pawn: print("no pawn (not in match)"); sys.exit()
# enumerate functions on pawn class chain + PlayerController + GameMode/State
def fns(cls):
    out=[]; depth=0; c=cls
    while c and c>0x10000 and depth<18:
        ch=ue.rq(c+0x48); i=0
        while ch and ch>0x10000 and i<5000:
            if ue.class_name(ch)=='Function':
                nm=ue.fname(ue.ri(ch+0x18))
                if nm: out.append(nm)
            ch=ue.rq(ch+0x28); i+=1
        c=ue.rq(c+0x40); depth+=1
    return out
targets={'pawn':ue.class_of(pawn)}
# PC + GameMode via world
w=ue.world()
gi=ue.rq(w+0x228)
lp=ue.rq(ue.rq(gi+(ue.field_off(ue.class_of(gi),"LocalPlayers") or 0x38)))
pc=ue.rq(lp+(ue.field_off(ue.class_of(lp),"PlayerController") or 0x30))
if pc>0x10000: targets['PC']=ue.class_of(pc)
al=[]
kw=('quit','kick','force','forbid','ban','anti','cheat','detect','farm','limit','warn',
    'exit','logout','disconnect','punish','illegal','abnormal','count','threshold','offline','remove')
for label,cls in targets.items():
    for nm in set(fns(cls)):
        low=nm.lower()
        if any(k in low for k in kw):
            al.append(f"{label}: {nm}")
for x in sorted(set(al)): print("  ",x)
print(f"({len(al)} matches)")
