# capwatch.py - background watcher: wait for game process + pawn, then apply
# fat capsules once, then keep re-applying every 3s while the match lasts
# (newly spawned monsters get written within one cycle).
import os
import sys, os, time, subprocess

PY = sys.executable
SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fatcapsule.py')

def game_running():
    out = subprocess.run(['tasklist', '/FI',
                          'IMAGENAME eq GhostHunterClientSteam-Win64-Shipping.exe'],
                         capture_output=True, text=True,
                         encoding='gbk', errors='ignore').stdout
    return any('GhostHunter' in l for l in out.split('\n'))

print("waiting for game process...", flush=True)
waited = 0
while not game_running():
    time.sleep(5)
    waited += 5
    if waited % 60 == 0:
        print(f"still waiting ({waited}s)", flush=True)

print("game up; waiting for pawn / running fatcapsule...", flush=True)
applied_pid = None
last_run = 0
while True:
    if not game_running():
        print("game exited; back to waiting", flush=True)
        applied_pid = None
        while not game_running():
            time.sleep(5)
        print("game up again", flush=True)
    # re-apply every 3s: catches new spawns AND fresh matches
    if time.time() - last_run >= 3:
        last_run = time.time()
        try:
            r = subprocess.run([PY, SCRIPT], capture_output=True, text=True,
                               encoding='utf-8', errors='replace', timeout=25)
            out = (r.stdout or '') + (r.stderr or '')
            # only print when something was actually written (match active)
            wrote = 'wrote ' in out and 'wrote 0/' not in out
            if wrote:
                # print a compact one-line status, suppress the per-monster spam
                line = [l for l in out.split('\n') if l.startswith('wrote ')]
                pers = [l for l in out.split('\n') if l.startswith('after 4s')]
                print(f"[{time.strftime('%H:%M:%S')}] {line[0] if line else ''} "
                      f"{pers[0] if pers else ''}", flush=True)
        except subprocess.TimeoutExpired:
            print("fatcapsule timeout (match loading?)", flush=True)
    time.sleep(2)
