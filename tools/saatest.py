# attrfire.py v3 - UTF-16 FString fix + full restart resilience.
# waits for game + in-match pawn (survives restarts), then:
#   phase 1: ServerAddAttribute(Atk) (Method,Type) matrix, self-validate on GAS Atk
#   phase 2: with the winning combo, fire OpenSpeed (human-set template if found)
# all reads/writes verified live; no hardcoded heap addresses.
import os
import sys, os, time, struct, ctypes, subprocess
from ctypes import wintypes

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
P = ctypes.c_void_p
k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.OpenFileMappingW.restype = P
k32.OpenFileMappingW.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_wchar_p]
k32.MapViewOfFile.restype = P
k32.MapViewOfFile.argtypes = [P, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32,
                              ctypes.c_size_t]
k32.VirtualAllocEx.restype = P
k32.VirtualAllocEx.argtypes = [P, P, ctypes.c_size_t, wintypes.DWORD, wintypes.DWORD]
k32.WriteProcessMemory.restype = wintypes.BOOL
k32.WriteProcessMemory.argtypes = [P, P, P, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
k32.OpenProcess.restype = P
k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]

CMD_OFF, CMD_STRIDE = 40, 40
LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'saatest_log.txt')
_lines = []
def log(s):
    _lines.append(s)
    print(s, flush=True)

def find_game():
    out = subprocess.run(['tasklist', '/FI',
                          'IMAGENAME eq GhostHunterClientSteam-Win64-Shipping.exe'],
                         capture_output=True, text=True,
                         encoding='gbk', errors='ignore').stdout
    for l in out.split('\n'):
        if 'GhostHunter' in l:
            try:
                return int(l.split()[1])
            except Exception:
                return None
    return None

def fresh_ue(pid):
    import uemem
    uemem.FORCE_PID = pid
    from uemem import UE
    return UE()

def in_match(ue):
    """pawn ptr if in a real match (not main city), else 0"""
    try:
        pawn = ue.pawn()
    except Exception:
        return 0
    if not pawn or pawn < 0x10000:
        return 0
    try:
        pcn = ue.obj_name(ue.class_of(pawn)) or ''
    except Exception:
        return 0
    if 'MainCity' in pcn:
        return 0
    return pawn

def wait_match():
    """blocks until a live game process has an in-match pawn. returns (ue, pid, pawn)."""
    pid = None
    ue = None
    while True:
        p = find_game()
        if not p:
            time.sleep(5); continue
        if p != pid:
            pid = p
            ue = None
        if ue is None:
            try:
                ue = fresh_ue(pid)
            except Exception:
                ue = None
                time.sleep(3); continue
        pawn = in_match(ue)
        if pawn:
            return ue, pid, pawn
        time.sleep(3)

def find_prop(cls, want):
    depth = 0; c = cls
    while c and c > 0x10000 and depth < 14:
        pc = ue_.rq(c + 0x50)
        i = 0
        while pc and pc > 0x10000 and i < 600:
            nm = ue_.fname(ue_.ri(pc + 0x20))
            if nm == want:
                return pc, ue_.ri(pc + 0x44)
            pc = ue_.rq(pc + 0x18); i += 1
        c = ue_.rq(c + 0x40); depth += 1
    return 0, None

def class_props(cls):
    out = {}
    depth = 0; c = cls
    while c and c > 0x10000 and depth < 12:
        pc = ue_.rq(c + 0x50)
        i = 0
        while pc and pc > 0x10000 and i < 500:
            nm = ue_.fname(ue_.ri(pc + 0x20))
            if nm and nm not in out:
                out[nm] = pc
            pc = ue_.rq(pc + 0x18); i += 1
        c = ue_.rq(c + 0x40); depth += 1
    return out

ue_ = None
def run_match(ue, pid, pawn):
    global ue_
    ue_ = ue
    log(f"=== match: pawn={pawn:X} ({ue.obj_name(ue.class_of(pawn))}) pid={pid} ===")
    def rf32(a): return struct.unpack('<f', ue.read(a, 4))[0]

    # queue
    hm = k32.OpenFileMappingW(0xF001F, 0, "XIXING_SHARED_V1")
    if not hm:
        log("queue mapping missing"); return None
    m = k32.MapViewOfFile(hm, 0xF001F, 0, 0, 21160)
    buf = (ctypes.c_char * 21160).from_address(m)
    h = k32.OpenProcess(0x1F0FFF, 0, pid)
    if not h:
        log("OpenProcess failed"); return None

    def fire(this, func, parms):
        pend, done = struct.unpack("<II", bytes(buf[8:16]))
        params = k32.VirtualAllocEx(h, None, 320, 0x3000, 0x04)
        assert params
        w = ctypes.c_size_t()
        assert k32.WriteProcessMemory(h, params, parms, len(parms), ctypes.byref(w))
        slot = (done + pend) % 16
        off = CMD_OFF + slot * CMD_STRIDE
        buf[off:off + CMD_STRIDE] = struct.pack("<IIQQQQ", 1, 0, this, func, params, 0)
        time.sleep(0.02)
        buf[8:12] = struct.pack("<I", pend + 1)
        # read Cmd.result (+32): 0=never drained, 1=called, 0xDEAD=SEH
        for _ in range(60):
            time.sleep(0.1)
            r = struct.unpack("<Q", bytes(buf[off + 32:off + 40]))[0]
            if r:
                return r
        return 0

    # sets
    asc = ue.rq(pawn + 0xC38)
    _, sp_off = find_prop(ue.class_of(asc), 'SpawnedAttributes')
    if not sp_off: log("no SpawnedAttributes"); return None
    arr = ue.rq(asc + sp_off); scnt = ue.ri(asc + sp_off + 8)
    set_inst = ue.rq(arr)
    log(f"set: {ue.class_name(set_inst)} @ {set_inst:X}")
    sprops = class_props(ue.class_of(set_inst))

    # FGameplayAttribute template: scan pawn AsyncTasks for base-set attrs
    tmpl_base = None; tmpl_human = None
    pcls = ue.class_of(pawn)
    at_list = []
    depth = 0; c = pcls
    while c and c > 0x10000 and depth < 16:
        pc = ue.rq(c + 0x50)
        i = 0
        while pc and pc > 0x10000 and i < 800:
            nm = ue.fname(ue.ri(pc + 0x20)); off = ue.ri(pc + 0x44); esz = ue.ri(pc + 0x34)
            if nm and esz == 8:
                try:
                    obj = ue.rq(pawn + off)
                    if obj and obj > 0x10000:
                        ocn = ue.class_name(obj) or ''
                        if 'AsyncTaskAttribute' in ocn:
                            at_list.append(obj)
                except Exception:
                    pass
            pc = ue.rq(pc + 0x18); i += 1
        c = ue.rq(c + 0x40); depth += 1
    for ati in at_list:
        try:
            raw = ue.read(ati, 0x800)
        except Exception:
            continue
        if not raw: continue
        for i in range(0, len(raw) - 0x38, 8):
            q = struct.unpack('<Q', raw[i:i+8])[0]
            if not (0x10000 < q < 0x800000000000): continue
            try:
                nm = ue.fname(ue.ri(q + 0x20))
            except Exception:
                continue
            if not nm or nm not in sprops: continue
            base = i - 0x10
            if base < 0: continue
            ga = raw[base:base + 56]
            if len(ga) < 56: continue
            owner = struct.unpack('<Q', ga[0x30:0x38])[0]
            try:
                ocn, onm = ue.class_name(owner), ue.obj_name(owner)
            except Exception:
                continue
            if ocn == 'Class' and onm and 'AttributeSet' in onm:
                if 'Human' in onm and not tmpl_human:
                    tmpl_human = (ga, nm)
                elif 'Base' in onm and not tmpl_base:
                    tmpl_base = (ga, nm)
    if not tmpl_base:
        tmpl_base = tmpl_human  # base attrs also live in the human set chain
    if not tmpl_base:
        log("no template found"); return None
    log(f"templates: base={tmpl_base[1]} human={tmpl_human[1] if tmpl_human else '-'}")

    uf = ue.find_function(ue.class_of(pawn), 'ServerAddAttribute')
    if not uf or uf < 0x10000:
        log("no UFunction"); return None

    def saa(name, fprop, tmpl, method, value, typ):
        s16 = name.encode('utf-16-le') + b'\x00\x00'
        sb = k32.VirtualAllocEx(h, None, 64, 0x3000, 0x04)
        w = ctypes.c_size_t()
        assert k32.WriteProcessMemory(h, sb, s16, len(s16), ctypes.byref(w))
        parms = bytearray(0x48)
        struct.pack_into('<QII', parms, 0x00, sb, len(name) + 1, len(name) + 1)
        struct.pack_into('<Q', parms, 0x10, fprop)
        parms[0x18:0x30] = tmpl[0x18:0x30]
        struct.pack_into('<Q', parms, 0x30, struct.unpack('<Q', tmpl[0x30:0x38])[0])
        parms[0x38] = method & 0xFF
        struct.pack_into('<f', parms, 0x3C, value)
        parms[0x40] = typ & 0xFF
        return fire(pawn, uf, bytes(parms))

    # ---- phase 1: Atk combo matrix ----
    fp_atk = sprops.get('Atk')
    _, atk_off = find_prop(ue.class_of(set_inst), 'Atk')
    if not fp_atk or atk_off is None:
        log("Atk prop missing"); return None
    cur0 = rf32(set_inst + atk_off + 0xC)   # CurrentValue is at +0xC, not +0
    log(f"phase1: Atk={cur0:.2f}, matrix...")
    combo = None
    for method, typ in [(0,0),(1,0),(2,0),(3,0),(0,1),(1,1),(2,1),(3,1)]:
        if not in_match(ue):
            log("match ended mid-matrix"); return None
        try:
            res = saa('Atk', fp_atk, tmpl_base[0], method, 250000.0, typ)
        except Exception as e:
            log(f"fire error {e}"); return None
        time.sleep(3)
        cur = rf32(set_inst + atk_off + 0xC)
        log(f"  M={method} T={typ}: result=0x{res:X} Atk(+0xC)={cur:.2f}")
        if abs(cur - cur0) > 0.01:
            combo = (method, typ)
            log(f"*** COMBO FOUND: Method={method} Type={typ} ({cur0:.2f} -> {cur:.2f}) ***")
            break
    if not combo:
        log("phase1: no effect")
        return None

    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'saa_combo.txt'), 'w') as f:
        f.write(f"{combo[0]} {combo[1]} Atk {cur:.2f}\n")

    # ---- phase 2: OpenSpeed with winning combo ----
    fp_open = sprops.get('OpenSpeed')
    _, open_off = find_prop(ue.class_of(set_inst), 'OpenSpeed')
    if fp_open and open_off is not None and in_match(ue):
        o0 = rf32(set_inst + open_off + 0xC)
        tmpl_use = tmpl_human[0] if tmpl_human else tmpl_base[0]
        try:
            saa('OpenSpeed', fp_open, tmpl_use, combo[0], o0 if o0 > 0.05 else 1.0, combo[1])
            time.sleep(3)
            o1 = rf32(set_inst + open_off + 0xC)
            log(f"phase2: OpenSpeed {o0:.2f} -> {o1:.2f} "
                f"{'*** OPEN SPEED BOOSTED ***' if abs(o1-o0) > 0.01 else '(no change)'}")
        except Exception as e:
            log(f"phase2 error {e}")
    return combo

while True:
    try:
        ue, pid, pawn = wait_match()
        result = run_match(ue, pid, pawn)
        if result:
            log("SUCCESS - exiting loop")
            break
        log("retrying next match...")
        time.sleep(5)
    except KeyboardInterrupt:
        break
    except Exception as e:
        log(f"outer error: {e}")
        time.sleep(5)

with open(LOG, 'w', encoding='utf-8') as f:
    f.write('\n'.join(_lines))
log(f"saved -> {LOG}")
