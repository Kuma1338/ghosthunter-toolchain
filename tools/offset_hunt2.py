# offset_hunt2.py - disambiguate + validate the new offsets.
# 1. ProcessEvent/CRF: count E8 rel32 call sites per pattern hit (true hot
#    functions have thousands of callers)
# 2. GNames: strict FNamePool signature - counter@+8 == N, blocks[0..N) valid,
#    blocks[N] == 0, block0 parses as a long chain of name entries
# 3. GWorld: near GNames, structural validation
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
print(f"pid={pid}")

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
print(f"base = {base:X}")

dos = rd(base, 0x40)
pe_off = struct.unpack('<I', dos[0x3C:0x40])[0]
nt = rd(base + pe_off, 0x40)
num_sec = struct.unpack('<H', nt[6:8])[0]
opt_size = struct.unpack('<H', nt[20:22])[0]
sec0 = pe_off + 24 + opt_size
secs = []
for i in range(num_sec):
    s = rd(base + sec0 + i * 40, 40)
    if not s or len(s) < 40: break
    name = s[:8].rstrip(b'\0').decode('ascii', 'replace')
    vsize, vaddr = struct.unpack('<II', s[8:16])
    chars = struct.unpack('<I', s[36:40])[0]
    secs.append((name, vaddr, vsize, chars))

PE_SIG = bytes.fromhex('405556574154415541564157' + '4881EC')
CRF_SIG = bytes.fromhex('4C89442418' + '55574155415641 57'.replace(' ', ''))

def find_all(sig):
    hits = []
    for name, vaddr, vsize, chars in secs:
        if not (chars & 0x20000000): continue
        CHUNK = 4 * 1024 * 1024
        for off in range(0, vsize, CHUNK - 64):
            n = min(CHUNK, vsize - off)
            data = rd(base + vaddr + off, n)
            if not data: continue
            i = 0
            while True:
                i = data.find(sig, i)
                if i < 0: break
                hits.append(vaddr + off + i)
                i += 1
    return hits

pe_hits = find_all(PE_SIG)
crf_hits = find_all(CRF_SIG)
print(f"PE sig hits: {len(pe_hits)}, CRF sig hits: {len(crf_hits)}")

# ---- call-site counting: one pass over .text tallying E8 rel32 targets ----
def count_calls(cand_set):
    tally = {c: 0 for c in cand_set}
    for name, vaddr, vsize, chars in secs:
        if not (chars & 0x20000000): continue
        CHUNK = 4 * 1024 * 1024
        for off in range(0, vsize, CHUNK):
            n = min(CHUNK, vsize - off)
            data = rd(base + vaddr + off, n)
            if not data: continue
            i = 0
            while True:
                i = data.find(b'\xE8', i)
                if i < 0 or i + 5 > n: break
                rel = struct.unpack_from('<i', data, i + 1)[0]
                target = vaddr + off + i + 5 + rel
                if target in tally:
                    tally[target] += 1
                i += 1
    return tally

print("counting call sites (one .text pass, may take ~20s)...")
pe_tally = count_calls(set(pe_hits))
crf_tally = count_calls(set(crf_hits))
print("\nProcessEvent candidates by call count:")
for c, n in sorted(pe_tally.items(), key=lambda kv: -kv[1])[:6]:
    print(f"  +{c:X}: {n} calls")
print("CallRemoteFunction candidates by call count:")
for c, n in sorted(crf_tally.items(), key=lambda kv: -kv[1])[:6]:
    print(f"  +{c:X}: {n} calls")

# ---- GNames strict validation ----
def heapish(q): return 0x7F0000000000 < q < 0x800000000000

print("\nGNames strict hunt (writable sections only)...")
gn_found = 0
for name, vaddr, vsize, chars in secs:
    if chars & 0x20000000: continue
    if chars & 0x80000000 == 0: continue      # must be writable
    if vsize < 0x10000: continue
    CHUNK = 4 * 1024 * 1024
    for off in range(0, vsize, CHUNK - 0x100):
        n = min(CHUNK, vsize - off)
        data = rd(base + vaddr + off, n)
        if not data: continue
        # strict: 4 consecutive valid block ptrs, then walk back to the pool base
        for i in range(0, n - 8 * 40, 8):
            q0 = struct.unpack_from('<Q', data, i)[0]
            if not heapish(q0): continue
            # this might be blocks[0] = pool_base + 0x10
            pool = base + vaddr + off + i - 0x10
            N = r32(pool + 8)
            if not (8 <= N <= 8192): continue
            ok = True
            for b in range(min(N, 6)):
                if not heapish(rq(pool + 0x10 + b * 8)): ok = False; break
            if not ok: continue
            # blocks[N] should be null (or beyond array)
            bN = rq(pool + 0x10 + N * 8) if N < 8192 else 1
            # parse block0's first entries as a name chain
            blk = rd(q0, 64)
            names = []
            if blk:
                o = 0
                for _ in range(8):
                    if o + 2 > len(blk): break
                    hh = blk[o] | (blk[o + 1] << 8)
                    ln = hh >> 6
                    wide = hh & 1
                    if ln < 1 or ln > 40: break
                    nb = blk[o + 2:o + 2 + ln]
                    try:
                        nm = nb.decode('utf-16-le' if wide else 'ascii').split('\x00')[0]
                        names.append(nm)
                    except Exception:
                        break
                    o += 2 + ln
                    if o & 1: o += 1
            if len(names) >= 5 and all(nm for nm in names):
                print(f"  GNames=+{pool - base:X} N={N} block0 names: {names[:8]}")
                gn_found += 1
                if gn_found >= 4: break
    if gn_found >= 4: break
if not gn_found:
    print("  no strict GNames candidate in writable sections!")
