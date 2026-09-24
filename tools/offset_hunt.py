# offset_hunt.py - find the new module-relative offsets after a game update.
# strategy:
#   1. parse PE sections of the game module
#   2. .text pattern scan: ProcessEvent (14B prologue) + CallRemoteFunction (13B)
#   3. .data scan: GNames = long runs of consecutive heap-pointer qwords whose
#      block0 content decodes as a FNamePool name entry ("None"-style header)
#   4. GWorld: near-neighborhood scan around GNames (engine globals cluster),
#      validated structurally (world->PersistentLevel->Actors TArray)
import os
import sys, ctypes, struct, subprocess
from ctypes import wintypes
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

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
assert h, "OpenProcess failed"
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

mods = (wintypes.HMODULE * 8)()
cb = wintypes.DWORD()
psapi.EnumProcessModulesEx(h, mods, ctypes.sizeof(mods), ctypes.byref(cb), 3)
base = ctypes.cast(mods[0], ctypes.c_void_p).value
print(f"base = {base:X}")

# ---- 1. PE sections ----
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
    print(f"  {name:10s} va=+{vaddr:08X} size=+{vsize:08X} exec={'Y' if chars & 0x20000000 else 'n'}")

# ---- 2. .text pattern scans ----
PE_SIG = bytes.fromhex('405556574154415541564157' + '4881EC')
CRF_SIG = bytes.fromhex('4C89442418555741554156 41 57'.replace(' ', ''))

def scan_pattern(sig, label):
    hits = []
    for name, vaddr, vsize, chars in secs:
        if not (chars & 0x20000000):  # executable only
            continue
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
    print(f"{label}: {len(hits)} hit(s) " + ' '.join(f'+{x:X}' for x in hits[:8]))
    return hits

pe_hits = scan_pattern(PE_SIG, "ProcessEvent sig")
crf_hits = scan_pattern(CRF_SIG, "CallRemoteFunction sig")

# ---- 3. GNames hunt: pointer runs in data sections ----
print("\nscanning data sections for FNamePool block arrays...")
def heapish(q):
    return 0x7F0000000000 < q < 0x800000000000

gn_candidates = []
for name, vaddr, vsize, chars in secs:
    if chars & 0x20000000:  # skip exec
        continue
    # skip tiny/rsrc sections
    if vsize < 0x10000: continue
    CHUNK = 4 * 1024 * 1024
    for off in range(0, vsize, CHUNK):
        n = min(CHUNK, vsize - off)
        data = rd(base + vaddr + off, n)
        if not data: continue
        # find runs of >=12 consecutive heap-ish qwords
        run_start = None
        run_len = 0
        for i in range(0, n - 8, 8):
            q = struct.unpack_from('<Q', data, i)[0]
            if heapish(q):
                if run_start is None: run_start = i
                run_len += 1
                if run_len >= 12:
                    cand = vaddr + off + run_start
                    # candidate blocks array: blocks[0] at this address
                    # validate: block0's first bytes decode as a name entry
                    b0 = struct.unpack_from('<Q', data, run_start)[0]
                    b = rd(b0, 24)
                    if b and len(b) >= 6:
                        hh = b[0] | (b[1] << 8)
                        ln = hh >> 6
                        wide = hh & 1
                        if 1 <= ln <= 40 and not wide:
                            try:
                                nm = b[2:2+ln].decode('ascii')
                                if nm.isidentifier() or nm == 'None':
                                    gn_candidates.append((cand - 0x10, run_len, nm))
                            except Exception:
                                pass
                        elif 1 <= ln <= 40 and wide:
                            try:
                                nm = b[2:2+ln].decode('utf-16-le').split('\x00')[0]
                                if nm and len(nm) > 1:
                                    gn_candidates.append((cand - 0x10, run_len, nm + '(wide)'))
                            except Exception:
                                pass
            else:
                run_start = None
                run_len = 0

print(f"GNames candidates ({len(gn_candidates)}):")
for c, rl, nm in gn_candidates[:10]:
    print(f"  GNames=+{c:X} runlen={rl} block0[0]='{nm}'")

# ---- 4. GWorld hunt near GNames ----
if gn_candidates:
    gn_off = gn_candidates[0][0]
    print(f"\nscanning +/-4MB around GNames (+{gn_off:X}) for GWorld...")
    lo = max(0, gn_off - 4 * 1024 * 1024)
    hi = gn_off + 4 * 1024 * 1024
    gworld_hits = []
    data = rd(base + lo, hi - lo)
    if data:
        for i in range(0, len(data) - 8, 8):
            w = struct.unpack_from('<Q', data, i)[0]
            if not heapish(w): continue
            pl = rq(w + 0x30)
            if not heapish(pl): continue
            arr = rq(pl + 0xA0)
            cnt = struct.unpack('<I', rd(pl + 0xA8, 4) or b'\0')[0]
            if heapish(arr) and 50 < cnt < 100000:
                gworld_hits.append((lo + i, cnt))
    print(f"GWorld candidates: {len(gworld_hits)}")
    for off, cnt in gworld_hits[:6]:
        print(f"  GWorld=+{off:X} actors={cnt}")
PYEOF_MARKER_NOT_USED = 0
