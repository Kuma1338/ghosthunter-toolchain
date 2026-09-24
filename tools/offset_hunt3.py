# offset_hunt3.py - vtable-reference counting + relaxed GNames hunt.
# ProcessEvent/CRF: their absolute address sits in every UObject-derived
# class vtable -> hundreds of references in .rdata/.data. The candidate with
# overwhelming reference count wins.
# GNames: pointer-run detection (any section), then name-chain validation of
# block0 + probe the header near the run for the block counter.
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
CRF_SIG = bytes.fromhex('4C89442418' + '5557415541564157')

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

pe_hits = set(find_all(PE_SIG))
crf_hits = set(find_all(CRF_SIG))
print(f"PE candidates: {len(pe_hits)}, CRF candidates: {len(crf_hits)}")

# ---- vtable reference counting: single pass over ALL data-ish sections ----
# tally qwords that equal base+candidate for any candidate
text_lo = 0x1000
text_hi = max(v + s for _, v, s, c in secs if c & 0x20000000)
cand_abs = {base + c: c for c in (pe_hits | crf_hits)}
tally = {}
print("counting vtable references in data sections (~15s)...")
for name, vaddr, vsize, chars in secs:
    if chars & 0x20000000: continue      # skip exec
    if vsize < 0x1000: continue
    CHUNK = 4 * 1024 * 1024
    for off in range(0, vsize, CHUNK):
        n = min(CHUNK, vsize - off)
        data = rd(base + vaddr + off, n)
        if not data: continue
        for i in range(0, n - 8, 8):
            q = struct.unpack_from('<Q', data, i)[0]
            c = cand_abs.get(q)
            if c is not None:
                tally[c] = tally.get(c, 0) + 1

pe_t = sorted(((tally.get(c, 0), c) for c in pe_hits), reverse=True)
crf_t = sorted(((tally.get(c, 0), c) for c in crf_hits), reverse=True)
print("ProcessEvent by vtable refs:")
for n, c in pe_t[:5]: print(f"  +{c:X}: {n}")
print("CallRemoteFunction by vtable refs:")
for n, c in crf_t[:5]: print(f"  +{c:X}: {n}")

# ---- GNames: relaxed run scan + name-chain validation ----
def heapish(q): return 0x7F0000000000 < q < 0x800000000000

print("\nGNames hunt (all sections, run>=12, name-chain validated)...")
gn_cands = {}
for name, vaddr, vsize, chars in secs:
    if chars & 0x20000000: continue
    if vsize < 0x10000: continue
    CHUNK = 4 * 1024 * 1024
    for off in range(0, vsize, CHUNK - 0x100):
        n = min(CHUNK, vsize - off)
        data = rd(base + vaddr + off, n)
        if not data: continue
        i = 0
        while i < n - 8 * 16:
            q0 = struct.unpack_from('<Q', data, i)[0]
            if not heapish(q0):
                i += 8
                continue
            # measure the run length
            rl = 0
            while i + rl * 8 + 8 <= n and heapish(struct.unpack_from('<Q', data, i + rl * 8)[0]):
                rl += 1
                if rl > 9000: break
            if rl >= 12:
                # validate block0 name chain
                blk = rd(q0, 128)
                names = []
                if blk:
                    o = 0
                    for _ in range(12):
                        if o + 2 > len(blk): break
                        hh = blk[o] | (blk[o + 1] << 8)
                        ln = hh >> 6
                        wide = hh & 1
                        if ln < 1 or ln > 40: break
                        nb = blk[o + 2:o + 2 + ln]
                        try:
                            nm = nb.decode('utf-16-le' if wide else 'ascii').split('\x00')[0]
                            if not nm: break
                            names.append(nm)
                        except Exception:
                            break
                        o += 2 + ln
                        if o & 1: o += 1
                if len(names) >= 6:
                    blocks_addr = vaddr + off + i          # address of blocks[0]
                    gn_cands[blocks_addr] = (rl, names)
            i += max(rl, 1) * 8

print(f"GNames block-array candidates: {len(gn_cands)}")
for ba, (rl, names) in gn_cands.items():
    # probe the header BEFORE blocks[0] for a plausible counter == rl-ish
    hdr_probe = []
    for ho in range(0x08, 0x48, 4):
        v = r32(base + ba - ho)
        if 8 <= v <= max(rl + 8, 64) and v >= rl - 4:
            hdr_probe.append(f"-{ho:X}:{v}")
    print(f"  blocks@+{ba:X} runlen={rl} names={names[:6]} counterprobe={hdr_probe}")
