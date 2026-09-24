# offset_hunt5.py - find the FNamePool by content, not by structure.
# engine registration order is fixed: "None", "ByteProperty", "IntProperty"...
# 1. walk committed regions (VirtualQueryEx), prioritize 0x20000-sized ones
# 2. scan for b'ByteProperty' with b'None' just before it
# 3. deduce the new entry header format from the known lengths (4 / 12)
# 4. find the .data qword pointing at the block base -> GNames = ptr_addr - 0x10
# 5. strict GWorld hunt: world whose class name decodes to 'World'
import sys, ctypes, struct, subprocess
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
assert h

def rd(a, s):
    b = ctypes.create_string_buffer(s); g = ctypes.c_size_t()
    return b.raw[:g.value] if k32.ReadProcessMemory(h, ctypes.c_void_p(a), b, s, ctypes.byref(g)) else None
def rq(a):
    b = rd(a, 8); return struct.unpack('<Q', b)[0] if b and len(b) == 8 else 0

mods = (wintypes.HMODULE * 8)(); cb = wintypes.DWORD()
psapi.EnumProcessModulesEx(h, mods, ctypes.sizeof(mods), ctypes.byref(cb), 3)
base = ctypes.cast(mods[0], ctypes.c_void_p).value
mod_lo = base
mod_hi = base + 0xBB00000  # sections end ~0xBAF0000+0x322CC8
print(f"pid={pid} base={base:X}")

# ---- 1. region walk ----
regions = []
addr = 0x10000
mbi = MBI()
while addr < 0x7FFFFFFFFFFF:
    r = k32.VirtualQueryEx(h, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi))
    if r == 0: break
    rs = mbi.RegionSize or 0x1000
    if mbi.State == 0x1000 and mbi.Protect in (0x04, 0x02, 0x40, 0x20, 0x08):  # committed readable
        b = mbi.BaseAddress or 0
        if not (mod_lo <= b < mod_hi):  # skip the module image
            regions.append((b, rs))
    addr = (mbi.BaseAddress or addr) + rs
print(f"committed regions outside module: {len(regions)}")

# prioritize: exact 0x20000-size regions first, then larger, skip tiny
prio = sorted(regions, key=lambda r: (0 if r[1] == 0x20000 else (1 if r[1] >= 0x100000 else 2), r[0]))

# ---- 2. scan for the engine-name signature ----
print("scanning for 'ByteProperty' preceded by 'None'...")
found = None
for b, sz in prio:
    if sz < 0x1000 or sz > 0x10000000: continue
    data = rd(b, sz)
    if not data: continue
    i = data.find(b'ByteProperty')
    if i < 0: continue
    # 'None' should be shortly before
    ctx = data[max(0, i-80):i]
    j = ctx.find(b'None')
    if j < 0: continue
    none_off = max(0, i-80) + j
    print(f"  HIT: region {b:X} size={sz:X}")
    print(f"  'None' @ {b+none_off:X}, 'ByteProperty' @ {b+i:X}")
    # dump the entry area
    d = data[max(0, none_off-8):i+40]
    for k in range(0, len(d), 16):
        ch = d[k:k+16]
        print(f"    +{k:03X}: {' '.join(f'{x:02X}' for x in ch):48s} "
              f"{''.join(chr(x) if 32<=x<127 else '.' for x in ch)}")
    found = (b, sz, none_off, i, data)
    break

if not found:
    print("signature not found in prioritized scan!")
    sys.exit(1)

b, sz, none_off, bp_off, data = found
# ---- 3. deduce header format ----
# 'None' name bytes start at none_off. header u16 at none_off-2 (if 2-byte header)
hdrN = struct.unpack_from('<H', data, none_off - 2)[0]
print(f"\nheader before 'None': 0x{hdrN:04X} (len must be 4)")
for shift in range(0, 12):
    ln = hdrN >> shift
    if ln == 4:
        print(f"  len==4 with >>{shift}  (wide bit = {hdrN & 1})")
hdrB = struct.unpack_from('<H', data, bp_off - 2)[0]
print(f"header before 'ByteProperty': 0x{hdrB:04X} (len must be 12)")
for shift in range(0, 12):
    ln = hdrB >> shift
    if ln == 12:
        print(f"  len==12 with >>{shift}  (wide bit = {hdrB & 1})")

# entry stride check: None entry = hdr2+4 = 6 bytes -> ByteProperty at none_off-2+6?
stride = bp_off - (none_off - 2)
print(f"None-entry start to ByteProperty start: {stride} bytes (expected 6 if contiguous)")

# ---- 4. block base: entries typically start at block+0; None is entry #0 ----
block_base = b  # region base (entries start at region start)
# find .data/.rdata pointer to block_base or nearby
print(f"\nsearching module data for pointers to {block_base:X} (+-0x10000)...")
tgt_lo, tgt_hi = block_base - 0x100, block_base + 0x10000
ptr_sites = []
for secname_lo in (0xACD8000, 0x8619000):  # .data, .rdata starts (from hunt1 sections)
    for off in range(0, 0x7000000, 4 * 1024 * 1024):
        d2 = rd(base + secname_lo + off, 4 * 1024 * 1024)
        if not d2: continue
        for i in range(0, len(d2) - 8, 8):
            q = struct.unpack_from('<Q', d2, i)[0]
            if tgt_lo <= q <= tgt_hi:
                ptr_sites.append(secname_lo + off + i)
        if secname_lo + off > 0xB3A0000: break
print(f"pointer sites: {[f'+{p:X}' for p in ptr_sites[:12]]}")
for p in ptr_sites[:12]:
    # GNames candidate = p - 0x10 (blocks[0] at pool+0x10)
    print(f"  site +{p:X} -> GNames candidate +{p-0x10:X}")
    # check blocks[1]
    b1 = rq(base + p + 8)
    print(f"    blocks[1] = {b1:X} {'(heap-ok)' if 0x7F0000000000 < b1 < 0x800000000000 else '(bad)'}")
