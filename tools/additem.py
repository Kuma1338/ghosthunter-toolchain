# additem.py <ItemId> [Count] [GNum] [BType]  -- fire ServerAddItem via the queue
# run in the DESKTOP terminal (session 1) with the overlay/game up.
import sys, struct
sys.path.insert(0, r'D:\gh_tools\tools')
import xxq
if len(sys.argv) < 2:
    print("usage: python additem.py <ItemId> [Count=10] [GNum=0] [BType=0]"); sys.exit(1)
itemid = int(sys.argv[1])
count  = int(sys.argv[2]) if len(sys.argv) > 2 else 10
gnum   = int(sys.argv[3]) if len(sys.argv) > 3 else 0
btype  = int(sys.argv[4]) if len(sys.argv) > 4 else 0
print(f"waiting for match...")
ue, pid, pawn = xxq.wait_for_match()
print(f"pawn={pawn:X} pid={pid}")
with xxq.Queue(pid) as q:
    q.assert_healthy()
    uf = ue.find_function(ue.class_of(pawn), 'ServerAddItem')
    if not uf:
        print("ServerAddItem not found"); sys.exit(1)
    parms = struct.pack('<iiii', itemid, count, gnum, btype)
    r = q.fire(pawn, uf, parms)
    print(f"ServerAddItem(id={itemid}, count={count}, gnum={gnum}, btype={btype}) -> {xxq.describe(r)}")
    print("check your inventory now.")
