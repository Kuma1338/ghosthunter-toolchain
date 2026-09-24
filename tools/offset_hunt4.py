# offset_hunt4.py - mutual validation hunt.
# 1. scan around the OLD GWorld offset (+-5MB) for a structurally valid world:
#    ptr W: W+0x30 -> PersistentLevel -> +0xA0 Actors TArray (count 50..200k)
# 2. take the world's FName index (@+0x18) and the persistent level's class
#    name index (@class+0x18) - both must decode to plausible strings via a
#    candidate GNames pool -> mutual lock.
# 3. GNames candidates: pointer runs near the OLD GNames offset (blocks array).
import sys, ctypes, struct, subprocess
from ctypes import wintypes

k32 = ctypes.WinDLL("kernel32")
k32.OpenProcess.restype = wintypes.HANDLE
k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
k32.ReadProcessMemory.restype = wintypes.BOOL
k32.ReadProcessMemory.argtypes = [wintypes.HANDLE, wintypes.LPCVOID, wintypes.LPVOID,
                                  ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
psapi = ctypes.WinDLL("psapi")
psapi.EnumProcessModulesEx.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.HMODULE),
                                       wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.DWORD]

out = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq GhostHunterClientSteam-Win64-Shipping.exe'],
                     capture_output=True, text=True, encoding='gbk', errors='ignore').stdout
lines = [l for l in out.split('\n') if 'GhostHunter' in l]
if not lines:
    print("game not running"); sys.exit(1)
pid = int(lines[0].split()[1])
h = k32.OpenProcess(0x1F0FFF, False, pid)
assert h

def rd(addr, size):
    buf = ctypes.create_string_buffer(size)
    got = ctypes.c_size_t()
    if k32.ReadProcessMemory(h, ctypes.c_void_p(addr), buf, size, ctypes.byref(got)):
        return buf.raw[:got.value]
    return None

def rq(a):
    b = rd(a, 8)
    return struct.unpack('<Q', b)[0] if b and len(b) == 8 else 0

def r32(a):
    b = rd(a, 4)
    return struct.unpack('<I', b)[0] if b and len(b) == 4 else 0

mods = (wintypes.HMODULE * 8)()
cb = wintypes.DWORD()
psapi.EnumProcessModulesEx(h, mods, ctypes.sizeof(mods), ctypes.byref(cb), 3)
base = ctypes.cast(mods[0], ctypes.c_void_p).value
print(f"pid={pid} base={base:X}")

def heapish(q): return 0x7F0000000000 < q < 0x800000000000

def fname_at(gnames, idx):
    """decode FName via candidate pool base (old-build semantics)"""
    if not idx or idx < 0: return None
    block = idx >> 16
    off = (idx & 0xFFFF) * 2
    if block > 8000: return None
    blk = rq(gnames + 0x10 + block * 8)
    if blk < 0x10000: return None
    hb = rd(blk + off, 2)
    if not hb or len(hb) < 2: return None
    hh = hb[0] | (hb[1] << 8)
    wide = hh & 1
    ln = hh >> 6
    if ln <= 0 or ln > 250: return None
    sb = rd(blk + off + 2, ln)
    if not sb or len(sb) < ln: return None
    try:
        if wide:
            if ln % 2: return None
            u = sb.decode("utf-16-le", errors="strict")
            return u.split("\x00")[0] if u else None
        return sb.decode("ascii", errors="strict").split("\x00")[0]
    except Exception:
        return None

OLD_GWORLD = 0xB11AB68
OLD_GNAMES = 0xAFC9F40

# ---- 1. GWorld structural candidates around old offset ----
print(f"\nscanning +-5MB around +{OLD_GWORLD:X} for world structs...")
lo = OLD_GWORLD - 5 * 1024 * 1024
hi = OLD_GWORLD + 5 * 1024 * 1024
data = rd(base + lo, hi - lo)
worlds = []
if data:
    for i in range(0, len(data) - 8, 8):
        w = struct.unpack_from('<Q', data, i)[0]
        if not heapish(w): continue
        pl = rq(w + 0x30)
        if not heapish(pl): continue
        arr = rq(pl + 0xA0)
        cnt = r32(pl + 0xA8)
        if heapish(arr) and 50 < cnt < 200000:
            worlds.append((lo + i, w, cnt))
print(f"world candidates: {len(worlds)}")
for off, w, cnt in worlds[:8]:
    nidx = r32(w + 0x18)
    pl = rq(w + 0x30)
    plcls = rq(pl + 0x10)
    clsidx = r32(plcls + 0x18) if heapish(plcls) else 0
    print(f"  +{off:X}: world={w:X} actors={cnt} worldNameIdx=0x{nidx:X} levelClsIdx=0x{clsidx:X}")

# ---- 2. GNames pool candidates: pointer runs near old offset ----
print(f"\nscanning +-6MB around +{OLD_GNAMES:X} for pool (pointer runs)...")
lo2 = OLD_GNAMES - 6 * 1024 * 1024
hi2 = OLD_GNAMES + 6 * 1024 * 1024
data2 = rd(base + lo2, hi2 - lo2)
pools = []
if data2:
    i = 0
    n = len(data2)
    while i < n - 8 * 16:
        q0 = struct.unpack_from('<Q', data2, i)[0]
        if not heapish(q0):
            i += 8
            continue
        rl = 0
        while i + rl * 8 + 8 <= n and heapish(struct.unpack_from('<Q', data2, i + rl * 8)[0]):
            rl += 1
            if rl > 9000: break
        if rl >= 8:
            pools.append((lo2 + i, rl))   # address of run start (= blocks[0]?)
        i += max(rl, 1) * 8
print(f"pool run candidates: {len(pools)}")

# ---- 3. mutual validation ----
print("\nmutual validation (pool decodes world/level names):")
best = None
for off, w, cnt in worlds[:8]:
    nidx = r32(w + 0x18)
    pl = rq(w + 0x30)
    plcls = rq(pl + 0x10)
    clsidx = r32(plcls + 0x18) if heapish(plcls) else 0
    for pa, rl in pools:
        for hdr in (0x10, 0x00, 0x08, 0x18):
            pool = base + pa - hdr
            nm1 = fname_at(pool, nidx)
            nm2 = fname_at(pool, clsidx) if clsidx else None
            if nm1 and len(nm1) >= 3 and nm1.isprintable():
                tag = ''
                if nm2 and len(nm2) >= 3 and nm2.isprintable():
                    tag = f'  levelClass="{nm2}"'
                print(f"  GWorld=+{off:X} GNames=+{pa - hdr:X} "
                      f"(run@+{pa:X} len={rl} hdr={hdr:X}) worldName=\"{nm1}\"{tag}")
                if best is None:
                    best = (off, pa - hdr)
if not best:
    print("  no mutual match - widen scan or layout changed")
else:
    print(f"\n*** LOCKED: GWorld=+{best[0]:X} GNames=+{best[1]:X} ***")
