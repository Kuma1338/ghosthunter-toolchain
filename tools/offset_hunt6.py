# offset_hunt6.py - final: strict GWorld hunt via class-name == 'World'.
# GNames = +0xAFCAF40 (verified by content). Scan .data around it for the
# GWorld global: target P where class(P)->name decodes to 'World'.
import sys, ctypes, struct, subprocess, bisect
from ctypes import wintypes

k32 = ctypes.WinDLL("kernel32")
k32.OpenProcess.restype = wintypes.HANDLE
k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
k32.ReadProcessMemory.restype = wintypes.BOOL
k32.ReadProcessMemory.argtypes = [wintypes.HANDLE, wintypes.LPCVOID, wintypes.LPVOID,
                                  ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
k32.VirtualQueryEx.restype = ctypes.c_size_t
k32.VirtualQueryEx.argtypes = [wintypes.HANDLE, wintypes.LPCVOID, ctypes.c_void_p, ctypes.c_size_t]
psapi = ctypes.WinDLL("psapi")
psapi.EnumProcessModulesEx.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.HMODULE),
                                       wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.DWORD]

class MBI(ctypes.Structure):
    _fields_ = [("BaseAddress", ctypes.c_void_p), ("AllocationBase", ctypes.c_void_p),
                ("AllocationProtect", wintypes.DWORD), ("RegionSize", ctypes.c_size_t),
                ("State", wintypes.DWORD), ("Protect", wintypes.DWORD), ("Type", wintypes.DWORD)]

out = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq GhostHunterClientSteam-Win64-Shipping.exe'],
                     capture_output=True, text=True, encoding='gbk', errors='ignore').stdout
lines = [l for l in out.split('\n') if 'GhostHunter' in l]
if not lines:
    print("game not running"); sys.exit(1)
pid = int(lines[0].split()[1])
h = k32.OpenProcess(0x1F0FFF, False, pid)

def rd(a, s):
    b = ctypes.create_string_buffer(s); g = ctypes.c_size_t()
    return b.raw[:g.value] if k32.ReadProcessMemory(h, ctypes.c_void_p(a), b, s, ctypes.byref(g)) else None
def rq(a):
    b = rd(a, 8); return struct.unpack('<Q', b)[0] if b and len(b) == 8 else 0

mods = (wintypes.HMODULE * 8)(); cb = wintypes.DWORD()
psapi.EnumProcessModulesEx(h, mods, ctypes.sizeof(mods), ctypes.byref(cb), 3)
base = ctypes.cast(mods[0], ctypes.c_void_p).value
print(f"pid={pid} base={base:X}")

GN = 0xAFCAF40
# committed region list for validity checks
regions = []
starts, ends = [], []
addr = 0x10000
mbi = MBI()
while addr < 0x7FFFFFFFFFFF:
    r = k32.VirtualQueryEx(h, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi))
    if r == 0: break
    rs = mbi.RegionSize or 0x1000
    if mbi.State == 0x1000 and mbi.Protect in (0x04, 0x02, 0x40, 0x20, 0x08):
        b = mbi.BaseAddress or 0
        regions.append((b, rs)); starts.append(b); ends.append(b + rs)
    addr = (mbi.BaseAddress or addr) + rs

def committed(a):
    i = bisect.bisect_right(starts, a) - 1
    return i >= 0 and a < ends[i]

# fname via verified GNames (block cache)
_blocks = {}
def fname(idx):
    if not idx or idx < 0: return None
    bi = idx >> 16
    blk = _blocks.get(bi)
    if blk is None:
        blk = rq(base + GN + 0x10 + bi * 8)
        if blk < 0x10000 or not committed(blk): return None
        _blocks[bi] = blk
    off = (idx & 0xFFFF) * 2
    hb = rd(blk + off, 2)
    if not hb or len(hb) < 2: return None
    hh = hb[0] | (hb[1] << 8)
    wide = hh & 1; ln = hh >> 6
    if ln <= 0 or ln > 250: return None
    sb = rd(blk + off + 2, ln)
    if not sb or len(sb) < ln: return None
    try:
        return (sb.decode('utf-16-le') if wide else sb.decode('ascii')).split('\x00')[0]
    except Exception:
        return None

# sanity: decode the first entries of block0
blk0 = rq(base + GN + 0x10)
print(f"block0={blk0:X} first names: {[fname(i) for i in range(4)]}")

# scan .data ±6MB around GNames for GWorld
print("scanning .data for GWorld (class name == 'World')...")
lo = GN - 6 * 1024 * 1024
hi = GN + 6 * 1024 * 1024
data = rd(base + lo, hi - lo)
hits = []
if data:
    for i in range(0, len(data) - 8, 8):
        p = struct.unpack_from('<Q', data, i)[0]
        if p < 0x10000 or not committed(p): continue
        cls = rq(p + 0x10)
        if cls < 0x10000 or not committed(cls): continue
        nidx_b = rd(cls + 0x18, 4)
        if not nidx_b or len(nidx_b) < 4: continue
        cn = fname(struct.unpack('<I', nidx_b)[0])
        if cn == 'World':
            nidx = struct.unpack('<I', rd(p + 0x18, 4) or b'\0\0\0\0')[0]
            hits.append((lo + i, p, fname(nidx)))
print(f"GWorld hits: {len(hits)}")
for off, p, nm in hits[:6]:
    print(f"  GWorld=+{off:X}  world={p:X}  name='{nm}'")

if hits:
    off, p, nm = hits[0]
    # also verify GWorld2-style siblings nearby + level class name
    pl = rq(p + 0x30)
    plc = rq(pl + 0x10)
    print(f"  level class = '{fname(struct.unpack('<I', rd(plc+0x18,4))[0])}'")
    print(f"\n=== NEW OFFSET TABLE ===")
    print(f"GNames   = +{GN:X}   (old +0xAFC9F40, delta {GN-0xAFC9F40:+X})")
    print(f"GWorld   = +{off:X}   (old +0xB11AB68, delta {off-0xB11AB68:+X})")
    print(f"ProcessEvent = +0x15E51E0 (old +0x15E5170, delta +70)")
    print(f"CallRemoteFunction = +0x27EEBA0 (old +0x27EEB30, delta +70)")
