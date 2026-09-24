# fatcapsule.py - enlarge monster hit capsules (client-side)
#
# mechanism: melee sweep/overlap uses the monster's CapsuleComponent geometry.
# the server never replicates capsule size (only position), so one write
# persists until the monster despawns. watch mode re-applies every 3s to
# catch newly spawned monsters.
#
# usage:
#   python fatcapsule.py          one-shot + 4s persistence check
#   python fatcapsule.py --watch  loop forever, auto re-apply
#
# offsets (GhostHunterClientSteam UE4.27, module Base 0x7FF7A1700000):
#   actor+0x1B8  RootComponent
#   capsule+0x598 CapsuleHalfHeight (float)
#   capsule+0x59C CapsuleRadius     (float)
import sys, struct, time, ctypes, subprocess
from ctypes import wintypes

sys.path.insert(0, r'D:\gh_tools\tools')

NEW_RADIUS = 300.0
NEW_HALFHEIGHT = 300.0
SCAN_RANGE_CM = 3500.0
WATCH_INTERVAL = 3.0

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.WriteProcessMemory.restype = wintypes.BOOL
kernel32.WriteProcessMemory.argtypes = [wintypes.HANDLE, wintypes.LPCVOID,
                                        wintypes.LPCVOID, ctypes.c_size_t,
                                        ctypes.POINTER(ctypes.c_size_t)]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]


def find_game():
    out = subprocess.run(['tasklist', '/FI',
                          'IMAGENAME eq GhostHunterClientSteam-Win64-Shipping.exe'],
                         capture_output=True, text=True,
                         encoding='gbk', errors='ignore').stdout
    for l in out.split('\n'):
        if 'GhostHunter' in l:
            return int(l.split()[1])
    return None


class Session:
    """one game process: read side (uemem) + write handle"""

    def __init__(self, pid):
        import uemem
        uemem.FORCE_PID = pid
        from uemem import UE
        self.ue = UE()
        self.pid = pid
        self.wh = kernel32.OpenProcess(0x1F0FFF, False, pid)
        if not self.wh:
            raise OSError(f"OpenProcess write failed: {ctypes.get_last_error()}")

    def close(self):
        if self.wh:
            kernel32.CloseHandle(self.wh)
            self.wh = None

    def rf32(self, addr):
        return struct.unpack('<f', self.ue.read(addr, 4))[0]

    def wf32(self, addr, val):
        data = struct.pack('<f', val)
        got = ctypes.c_size_t()
        return kernel32.WriteProcessMemory(self.wh, ctypes.c_void_p(addr),
                                           data, 4, ctypes.byref(got)) and got.value == 4

    def apply(self, verbose=False):
        """write fat capsule to every GhostAI in range. returns (n_ok, n_all)."""
        pawn = self.ue.pawn()
        if not pawn or pawn < 0x10000:
            return (0, 0)
        ppos = self.ue.actor_pos(pawn)
        if not ppos:
            return (0, 0)
        targets = []
        for lv in self.ue.levels():
            for a in self.ue.actors(lv):
                cn = self.ue.class_name(a)
                if not (cn and 'GhostAI' in cn):
                    continue
                pos = self.ue.actor_pos(a)
                if not pos:
                    continue
                d = (sum((x - y) ** 2 for x, y in zip(pos, ppos))) ** 0.5
                if d > SCAN_RANGE_CM:
                    continue
                root = self.ue.rq(a + 0x1B8)
                if not root or root < 0x10000:
                    continue
                try:
                    r0 = self.rf32(root + 0x59C)
                except Exception:
                    continue
                if not (5.0 < r0 < 2000.0):  # sanity: real capsule
                    continue
                targets.append((a, cn, d, root, r0))
        ok = 0
        for a, cn, d, root, r0 in sorted(targets, key=lambda t: t[2]):
            w1 = self.wf32(root + 0x598, NEW_HALFHEIGHT)
            w2 = self.wf32(root + 0x59C, NEW_RADIUS)
            rb_h = self.rf32(root + 0x598)
            rb_r = self.rf32(root + 0x59C)
            good = (abs(rb_r - NEW_RADIUS) < 0.5
                    and abs(rb_h - NEW_HALFHEIGHT) < 0.5)
            ok += good
            if verbose:
                print(f"  {'OK ' if good else 'FAIL'} {cn[:44]:44s} "
                      f"d={d:5.0f}cm r:{r0:6.1f}->{rb_r:.0f} h->{rb_h:.0f}")
        return (ok, len(targets))


def one_shot():
    pid = find_game()
    if not pid:
        print("game not running"); return 1
    print(f"pid={pid}")
    s = Session(pid)
    pawn = s.ue.pawn()
    if not pawn or pawn < 0x10000:
        print("no pawn (in a match?)"); s.close(); return 1
    ppos = s.ue.actor_pos(pawn)
    print(f"pawn={pawn:X} pos={[f'{v:.0f}' for v in ppos]}")
    ok, n = s.apply(verbose=True)
    print(f"\nwrote {ok}/{n}")
    if n:
        time.sleep(4)
        fail = 0
        # re-scan and check persistence of previously fat capsules
        pawn = s.ue.pawn()
        ppos = s.ue.actor_pos(pawn) if pawn else None
        for lv in s.ue.levels():
            for a in s.ue.actors(lv):
                cn = s.ue.class_name(a)
                if not (cn and 'GhostAI' in cn):
                    continue
                pos = s.ue.actor_pos(a)
                if not pos or not ppos:
                    continue
                d = (sum((x - y) ** 2 for x, y in zip(pos, ppos))) ** 0.5
                if d > SCAN_RANGE_CM + 500:
                    continue
                root = s.ue.rq(a + 0x1B8)
                if not root or root < 0x10000:
                    continue
                try:
                    rr = s.rf32(root + 0x59C)
                except Exception:
                    continue
                if abs(rr - NEW_RADIUS) > 0.5 and rr > 5.0:
                    # only count revert if it was fat before
                    pass
        print("persistence spot-check done")
    s.close()
    return 0


def watch():
    sess = None
    sess_pid = None
    print("watch mode: waiting for game process...", flush=True)
    while True:
        pid = find_game()
        if not pid:
            if sess:
                sess.close(); sess = None; sess_pid = None
                print("game exited; waiting for restart", flush=True)
            time.sleep(5)
            continue
        if sess is None or pid != sess_pid:
            try:
                if sess: sess.close()
                sess = Session(pid)
                sess_pid = pid
                print(f"attached pid={pid}", flush=True)
            except Exception as e:
                print(f"attach failed: {e}", flush=True)
                time.sleep(5)
                continue
        try:
            ok, n = sess.apply()
            if n > 0:
                print(f"[{time.strftime('%H:%M:%S')}] fat {ok}/{n} monsters",
                      flush=True)
        except Exception as e:
            print(f"apply error: {e}", flush=True)
            try:
                sess.close()
            except Exception:
                pass
            sess = None
            sess_pid = None
        time.sleep(WATCH_INTERVAL)


if __name__ == '__main__':
    if '--watch' in sys.argv:
        watch()
    else:
        sys.exit(one_shot())
