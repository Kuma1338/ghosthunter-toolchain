r"""隔空摸容器 (remote container touching): open every gold/red box on the map with server-side
loot, then vacuum all drops to the player's feet.

Prerequisite: xx_handle.txt calibrated this session (one real F press).

Usage: python xxstar.py [max_boxes]
"""
import ctypes
import struct
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import uemem
from uemem import (UE, PAWN_IS, IS_PRE, TB_STATE, TB_COM, DROP_PICKED,  # noqa: E402
                   TOOL_COM, ACTOR_ROOT, ROOT_BOUNDS)

k32 = ctypes.WinDLL("kernel32")
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
MAX_BOXES = int(sys.argv[1]) if len(sys.argv) > 1 else 5

# name -> quality (from the ESP overlay's confirmed table)
GOLD_NAMES = {"金丝楠木箱柜", "鎏金兽首百宝箱", "紫漆牡丹箱"}
RED_NAMES = {"花梨木龙纹箱", "赤血龙木柜"}

TB_STATE_OFF, TB_NAME_OFF = 0x2B8, 0x450
FTEXT_STRPTR = 0x20

ue = UE()
pawn = ue.pawn()
isCom = ue.rq(pawn + PAWN_IS)
asc = ue.rq(pawn + 0xC38)
uf_setpre = ue.find_function(ue.class_of(isCom), "ServerSetPreBeInteractComponent")
# LOCAL activation API (the game's own path): generates proper client-side
# prediction and sends its own server RPC - immune to the server-side
# prediction validation that silently blocks bare RPCs (2026-09-22 build).
uf_try = ue.find_function(ue.class_of(asc), "TryActivateAbility")
uf_setloc = ue.find_function(ue.class_of(pawn), "K2_SetActorLocation")
uf_pickup = ue.find_function(ue.class_of(pawn), "Server_RequestPickupByUI")
print(f"pawn={pawn:X} isCom={isCom:X} ASC={asc:X}")

handle = int(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "xx_handle.txt")).read().strip())
print(f"interact handle = 0x{handle:X}")

hm = k32.OpenFileMappingW(0xF001F, 0, "XIXING_SHARED_V1")
m = k32.MapViewOfFile(hm, 0xF001F, 0, 0, 21160)
buf = (ctypes.c_char * 21160).from_address(m)
h = k32.OpenProcess(0x1F0FFF, 0, ue.pid)
assert h, f"OpenProcess failed err={ctypes.GetLastError()}"
print(f"proc handle={h}")


def fire(this, func, parms):
    pend, done = struct.unpack("<II", bytes(buf[8:16]))
    params = k32.VirtualAllocEx(h, None, 320, 0x3000, 0x04)
    if not params:
        raise RuntimeError(f"VirtualAllocEx failed err={ctypes.GetLastError()} h={h}")
    w = ctypes.c_size_t()
    if not k32.WriteProcessMemory(h, params, parms, len(parms), ctypes.byref(w)):
        raise RuntimeError("WPM failed - abort")
    slot = (done + pend) % 16
    off = CMD_OFF + slot * CMD_STRIDE
    buf[off:off + CMD_STRIDE] = struct.pack("<IIQQQQ", 1, 0, this, func, params, 0)
    time.sleep(0.02)
    buf[8:12] = struct.pack("<I", pend + 1)


def read_ftext(actor, off):
    """FText at actor+off -> wstring (up to 32 chars)."""
    td = ue.rq(actor + off)
    if td < 0x10000:
        return None
    sp = ue.rq(td + FTEXT_STRPTR)
    if sp < 0x10000:
        return None
    raw = ue.read(sp, 64)
    if not raw:
        return None
    try:
        s = raw.decode("utf-16-le", errors="strict")
        return s.split("\x00")[0] if s else None
    except UnicodeDecodeError:
        return None


def scan_boxes():
    """All unopened gold/red TB boxes AND gold/red tool nodes (mining/collect)
    -> [(actor, com, name, quality)]. Tool quality = SpawnIndex@+0x310 - 1
    (4=gold 5=red per the name table); com @ +0x2C8 for the tool family."""
    out = []
    for lv in ue.levels():
        for a in ue.actors(lv):
            cn = ue.class_name(a)
            if cn in ("BP_YiGui_C", "BP_HeZi_C", "BP_BaoXiang_C"):
                if ue.ri(a + TB_STATE_OFF) == 3:
                    continue
                nm = read_ftext(a, TB_NAME_OFF)
                if nm is None:
                    continue  # not streamed yet
                if nm in GOLD_NAMES:
                    out.append((a, ue.rq(a + TB_COM), nm, 3))
                elif nm in RED_NAMES:
                    out.append((a, ue.rq(a + TB_COM), nm, 4))
            elif cn in ("BP_Mining_C", "BP_Collect_C"):
                si = ue.ri(a + 0x310)
                # SpawnIndex 1白2蓝3紫4金5红 (ESP-verified mapping, HANDOFF)
                if si == 4:
                    out.append((a, ue.rq(a + 0x2C8), cn + "#gold", 3))
                elif si == 5:
                    out.append((a, ue.rq(a + 0x2C8), cn + "#red", 4))
    # red first then gold; within the same quality, NEAREST first (the server
    # range-checks interactions at ~70-90m - always work from close outward)
    _pp = None
    try:
        _pp = ue.actor_pos(ue.pawn())
    except Exception:
        pass
    def _d(t):
        if not _pp:
            return 0.0
        p = ue.actor_pos(t[0])
        if not p:
            return 1e18
        return sum((x - y) ** 2 for x, y in zip(p, _pp))
    out.sort(key=lambda t: (-t[3], _d(t)))
    return out


def alive_drop(a):
    return ue.class_name(a) == "BP_DropInteract_C" and not ue.ri(a + DROP_PICKED)


def drop_color(a):
    """Item quality byte @ +0x4BA. Empirically mapped (c6=RED confirmed live):
    2=white 3=blue 4=purple 5=GOLD 6=RED (byte = quality + 2)."""
    return (ue.ri(a + 0x4BA) & 0xFF) - 2


def scan_drops(min_color=3):
    """RED and GOLD drops only (quality scale: 2=white 3=blue 4=purple 5=gold 6=red).
    Sorted red first, gold second."""
    out = []
    for lv in ue.levels():
        for a in ue.actors(lv):
            if alive_drop(a):
                c = drop_color(a)
                if c >= min_color:
                    out.append((c, a))
    out.sort(key=lambda t: -t[0])
    return [a for _, a in out]


# ---------------- phase 0: handle calibration with VERIFICATION ----------------
def read_ring_candidates():
    """Interact handle derivation v3 - NO blind firing.

    Ring signature (live-decoded): the interact activation is
    ServerTryActivateAbility with InputPressed=1 and an ALL-ZERO parm tail;
    the movement spam is pressed=0 with counter bytes; item abilities (hoof
    0x77) are pressed=1 with zero tails too - so the KNOWN item handle is
    explicitly excluded, and candidates are ordered by recency (the
    calibration flow: press F on a box, then run this script immediately).
    The last-verified file handle is prepended (0x7D recurred 5 matches)."""
    KNOWN_ITEM_HANDLES = {0x77}   # donkey hoof - bare activation crashes (cause #8)
    k32.OpenFileMappingW.restype = ctypes.c_void_p
    k32.OpenFileMappingW.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_wchar_p]
    k32.MapViewOfFile.restype = ctypes.c_void_p
    k32.MapViewOfFile.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_size_t]
    hm2 = k32.OpenFileMappingW(0xF001F, 0, "XIXING_RPC_V1")
    if not hm2:
        return []
    m2 = k32.MapViewOfFile(hm2, 0xF001F, 0, 0, 8 + 512 * 144)
    b2 = (ctypes.c_char * (8 + 512 * 144)).from_address(m2)
    magic, ver, count = struct.unpack("<III", bytes(b2[:12]))
    if magic != 0x58495852:
        return []
    n = min(count, 512)
    cands = []
    seen = set()
    for i in range(n - 1, -1, -1):   # newest first
        off = 24 + i * 144
        func_ = struct.unpack("<Q", bytes(b2[off:off + 8]))[0]
        nm = ue.obj_name(func_) if func_ > 0x10000 else None
        if nm != "ServerTryActivateAbility":
            continue
        parms = bytes(b2[off + 16:off + 144])
        h = struct.unpack_from("<I", parms, 0)[0]
        pressed = struct.unpack_from("<B", parms, 4)[0]
        # NO clean-tail requirement: since the 2026-09-22 server build every
        # activation carries a prediction key (non-zero tail) - the clean
        # filter rejected ALL of his real presses.
        if pressed and h not in KNOWN_ITEM_HANDLES and h not in seen:
            seen.add(h)
            cands.append(h)
    # prepend the last-verified handle
    try:
        prev = int(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "xx_handle.txt")).read().strip())
        if prev not in cands and prev not in KNOWN_ITEM_HANDLES:
            cands.insert(0, prev)
    except Exception:
        pass
    return cands

# grace: the calibration F press's ability may still be ending - activating
# during the drain window gets locally rejected ("no effect" + state damage)
print("grace: 5s ability drain...")
time.sleep(5.0)
candidates = read_ring_candidates()
print(f"phase 0: candidates (recency order): {[hex(c) for c in candidates]}")
if not candidates:
    print("NO candidates - arm the RPC logger and press F on a box first")
    sys.exit(1)

boxes = scan_boxes()
verified = 0
for cand in candidates[:2]:   # derived candidates only - no blind firing
    boxes = scan_boxes()
    if not boxes:
        print("no unopened gold/red box available for verification")
        break
    box, com, nm, q = boxes[0]
    print(f"  candidate 0x{cand:X} on {nm} ...", end=" ", flush=True)
    fire(isCom, uf_setpre, struct.pack("<Q", com))
    time.sleep(0.4)
    fire(asc, uf_try, struct.pack("<IBB", cand, 1, 0) + bytes(16))
    ok = False
    for _ in range(24):
        time.sleep(0.35)
        if ue.ri(box + TB_STATE_OFF) == 3:
            ok = True
            break
    if ok:
        print("OPENED -> HANDLE VERIFIED")
        handle = cand
        verified += 1
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "xx_handle.txt"), "w") as f:
            f.write(f"{cand}\n")
        time.sleep(4.0)   # ability-end grace
        break
    print("no effect")
    time.sleep(4.0)
if not verified:
    print("NO candidate opened a box - aborting (GAS may be wedged, restart advised)")
    sys.exit(1)
print(f"interact handle = 0x{handle:X} (verified live)")

def target_done(actor, nm):
    """Completion check: TB boxes flip state to 3; tool nodes (mining/collect)
    are consumed - the actor despawns or changes class."""
    if nm.startswith("BP_"):
        return ue.class_name(actor) not in ("BP_Mining_C", "BP_Collect_C")
    return ue.ri(actor + TB_STATE_OFF) == 3


# ---------------- phase 1: open all gold/red boxes + tool nodes ----------------
boxes = scan_boxes()
print(f"\n=== PHASE 1: {min(len(boxes), MAX_BOXES)} gold/red targets ===")
opened = 0
for i, (box, com, nm, q) in enumerate(boxes):
    if opened >= MAX_BOXES:
        break
    if not nm.startswith("BP_") and ue.ri(box + TB_STATE_OFF) == 3:
        continue
    print(f"[{i+1}/{len(boxes)}] {nm} ...", end=" ", flush=True)
    fire(isCom, uf_setpre, struct.pack("<Q", com))
    time.sleep(0.3)
    fire(asc, uf_try, struct.pack("<IBB", handle, 1, 0) + bytes(16))
    # wait for the channel (~6s)
    ok = False
    for _ in range(24):
        time.sleep(0.35)
        if target_done(box, nm):
            ok = True
            break
    if ok:
        opened += 1
        print("DONE")
        # the GAS ability END lags completion: activating the next interaction
        # while the ability is still alive wedges the client GAS (input freeze).
        # 9s total also spaces server-side interaction rate to human pace
        # (6 rapid opens in 60s killed the session - rate countermeasure).
        time.sleep(9.0)
    else:
        print("timeout")
        time.sleep(5.0)   # let any half-started ability fully drain

# late-streaming rescan: box/node names can become readable seconds after the
# level loads; a single pass misses them. Re-scan until nothing new appears.
for _pass in range(2):
    time.sleep(3.0)
    boxes2 = scan_boxes()
    todo = [b for b in boxes2 if not target_done(b[0], b[2])]
    if not todo:
        break
    print(f"rescan pass: {len(todo)} newly-readable targets")
    for i, (box, com, nm, q) in enumerate(todo):
        print(f"[R{i+1}] {nm} ...", end=" ", flush=True)
        fire(isCom, uf_setpre, struct.pack("<Q", com))
        time.sleep(0.3)
        fire(asc, uf_try, struct.pack("<IBB", handle, 1, 0) + bytes(16))
        ok = False
        for _ in range(24):
            time.sleep(0.35)
            if target_done(box, nm):
                ok = True
                break
        if ok:
            opened += 1
            print("DONE")
            time.sleep(4.0)
        else:
            print("timeout (too far? walk within ~70m)")

print(f"\n=== PHASE 2: vacuum ({opened} targets done) ===")
# GRACE: drops take 1-2s to physically appear after the box opens.
# Teleporting a half-spawned actor = crash (root/physics not initialized).
print("waiting 4s for drops to settle on the ground...")
time.sleep(4.0)

moved = set()     # drops already picked (or attempted)
done = 0
t0 = time.time()
while time.time() - t0 < 150:
    drops = scan_drops()
    if not drops:
        time.sleep(1.5)
        if not scan_drops():
            break
        continue
    acted = False
    for a in drops:
        if a in moved:
            continue
        if not alive_drop(a):
            continue
        # PICKUP-TO-BACKPACK (red/gold only): the game's own collection flow
        # via Server_RequestPickupByUI. Weight-limited: over-weight drops
        # remain at the boxes for manual handling.
        fire(pawn, uf_pickup, struct.pack("<QIB", a, 0, 0) + bytes(5))
        done += 1
        moved.add(a)
        acted = True
        for _ in range(10):
            time.sleep(0.2)
            if not alive_drop(a):
                break
        time.sleep(0.4)
    if not acted:
        time.sleep(1.0)

print(f"\n=== DONE: {opened} boxes opened, {done} red/gold drops picked up ===")
print("over-weight drops remain at the boxes - free up carry weight to grab them")
