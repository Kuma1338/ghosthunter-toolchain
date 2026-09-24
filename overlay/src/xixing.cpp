#include "xixing.h"
#include <cstdio>
#include <cstring>
#include <cmath>
#include <algorithm>
#include <psapi.h>

// ---------------- shared log ----------------
// portable: log next to overlay.exe
static const char* logPathPortable() {
    static char buf[MAX_PATH] = {};
    if (!buf[0]) {
        wchar_t w[MAX_PATH]; GetModuleFileNameW(nullptr, w, MAX_PATH);
        wchar_t* slash = wcsrchr(w, L'\\');
        if (slash) { wcscpy_s(slash + 1, MAX_PATH - (slash + 1 - w), L"overlay.log"); WideCharToMultiByte(CP_UTF8, 0, w, -1, buf, MAX_PATH, 0, 0); }
        else WideCharToMultiByte(CP_UTF8, 0, w, -1, buf, MAX_PATH, 0, 0);
    }
    return buf;
}
static void xxLog(const char* msg) {
    FILE* f = nullptr;
    if (fopen_s(&f, logPathPortable(), "a") == 0 && f) {
        fprintf(f, "[%u] [xx] %s\n", GetTickCount(), msg);
        fclose(f);
    }
}

// ---------------- DLL injection (LoadLibraryW via CreateRemoteThread) ----------------
// portable: resolve xixing8.dll next to overlay.exe (release layout)
static const wchar_t* dllPathPortable() {
    static wchar_t buf[MAX_PATH] = {};
    if (!buf[0]) {
        GetModuleFileNameW(nullptr, buf, MAX_PATH);
        wchar_t* slash = wcsrchr(buf, L'\\');
        if (slash) wcscpy_s(slash + 1, MAX_PATH - (slash + 1 - buf), L"xixing8.dll");
    }
    return buf;
}
#define DLL_PATH dllPathPortable()

// verify a module is loaded via the PROCESS MODULE LIST (authoritative),
// never GetExitCodeThread (that truncates the 64-bit HMODULE to 32 bits).
static bool moduleLoaded(HANDLE h, const wchar_t* needle) {
    HMODULE mods[1024]; DWORD cb = 0;
    if (!EnumProcessModulesEx(h, mods, sizeof(mods), &cb, LIST_MODULES_ALL)) return false;
    wchar_t name[MAX_PATH];
    int n = (int)(cb / sizeof(HMODULE));
    for (int i = 0; i < n && i < 1024; i++)
        if (GetModuleFileNameExW(h, mods[i], name, MAX_PATH) && wcsstr(name, needle)) return true;
    return false;
}

bool XiXing::injectDll(const Mem& mem) {
    if (injectionTried && stats.injected) return true;
    if (injectionTried && !stats.injected) {
        // retry every ~5s until it sticks
        static DWORD lastTry = 0;
        if (GetTickCount() - lastTry < 5000) return false;
        lastTry = GetTickCount();
    }
    injectionTried = true;
    HANDLE h = mem.procHandle;   // opened with PROCESS_ALL_ACCESS by Mem::attach
    if (!h) return false;
    size_t bytes = (wcslen(DLL_PATH) + 1) * sizeof(wchar_t);
    void* remote = VirtualAllocEx(h, nullptr, bytes, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    if (!remote) return false;
    if (!WriteProcessMemory(h, remote, DLL_PATH, bytes, nullptr)) return false;
    HMODULE k32 = GetModuleHandleW(L"kernel32.dll");
    FARPROC ll = GetProcAddress(k32, "LoadLibraryW");
    HANDLE t = CreateRemoteThread(h, nullptr, 0, (LPTHREAD_START_ROUTINE)ll, remote, 0, nullptr);
    if (!t) return false;
    WaitForSingleObject(t, 10000);
    CloseHandle(t);
    VirtualFreeEx(h, remote, 0, MEM_RELEASE);
    // verify via the module list, NOT the truncated thread exit code (a
    // high-address load returns 0 -> false "failed" -> 5s retry storm)
    if (moduleLoaded(h, L"xixing8.dll")) {
        stats.injected = true;
        xxLog("DLL injected by overlay (module verified)");
        return true;
    }
    return false;
}

// ---------------- connection ----------------
bool XiXing::tryConnect(const Mem& mem) {
    // W2 fix: pid-change reset MUST run before the early-return - the overlay
    // holds section handles that outlive the game process, so shared&&rpc
    // stays true forever and the reset never fires after a game restart.
    if (gamePid != mem.procId) {
        gamePid = mem.procId;
        paramsBase = 0;
        ufWorldMark = 0;
        latchedHandle = 0;
        lastBoxStates.clear();
        // the previous game process died: its DLL died with it - re-inject
        stats.injected = false;
        injectionTried = false;
        if (shared) { UnmapViewOfFile(shared); shared = nullptr; }
        if (rpc) { UnmapViewOfFile(rpc); rpc = nullptr; }
        if (mapFile) { CloseHandle(mapFile); mapFile = nullptr; }
        if (rpcMapFile) { CloseHandle(rpcMapFile); rpcMapFile = nullptr; }
    }
    if (!shared) {
        mapFile = OpenFileMappingW(FILE_MAP_ALL_ACCESS, FALSE, L"XIXING_SHARED_V1");
        if (mapFile) {
            shared = (xx::Shared*)MapViewOfFile(mapFile, FILE_MAP_ALL_ACCESS, 0, 0, sizeof(xx::Shared));
            if (!shared) { CloseHandle(mapFile); mapFile = nullptr; }
        }
    }
    if (!rpc) {
        rpcMapFile = OpenFileMappingW(FILE_MAP_ALL_ACCESS, FALSE, L"XIXING_RPC_V1");
        if (rpcMapFile) {
            rpc = (xx::RpcShared*)MapViewOfFile(rpcMapFile, FILE_MAP_ALL_ACCESS, 0, 0, sizeof(xx::RpcShared));
            if (!rpc) { CloseHandle(rpcMapFile); rpcMapFile = nullptr; }
        }
    }
    return shared && rpc;
}

bool XiXing::canSend() const {
    if (!shared || shared->magic != xx::MAGIC) return false;
    if (shared->hookOk != xx::MAGIC) return false;
    return stats.queueAlive;
}

bool XiXing::submit(const Mem& mem, uintptr_t thisPtr, uintptr_t funcPtr,
                    const void* params, size_t n) {
    if (!canSend() || !thisPtr || !funcPtr) return false;
    // G2 fix: read doneSeq FIRST, then pending. With the DLL decrementing
    // pending before incrementing doneSeq, this order makes the sum
    // (done+pending) never exceed the true total -> no stale-slot writes.
    uint32_t done = shared->doneSeq;
    uint32_t pending = shared->pending;
    if (pending >= 14) return false;
    if (n > xx::PARAM_BYTES) return false;
    if (!paramsBase) {
        paramsBase = mem.allocRemote(xx::CMD_SLOTS * xx::PARAM_BYTES);
        if (!paramsBase) return false;
    }
    uint32_t slot = (done + pending) % xx::CMD_SLOTS;
    uintptr_t pbase = paramsBase + (uintptr_t)slot * xx::PARAM_BYTES;
    if (n && !mem.write(pbase, params, n)) return false;
    xx::Cmd& c = shared->cmds[slot];
    c.op = 1; c.pad = 0;
    c.thisPtr = thisPtr; c.funcPtr = funcPtr; c.paramsPtr = pbase; c.result = 0;
    MemoryBarrier();
    InterlockedIncrement((volatile LONG*)&shared->pending);
    return true;
}

// ---------------- handle derivation from the RPC ring ----------------
static bool fnPtrSaneX(uintptr_t p) { return p > 0x10000 && p < 0x7FFFFFFFFFFF; }

uintptr_t XiXing::deriveHandle(UE4& ue) {
    if (!rpc || rpc->magic != xx::RPC_MAGIC) return 0;
    int n = rpc->count;
    if (n <= 0 || n > 512) n = 0;
    const uint32_t KNOWN_ITEM = 0x77;   // donkey hoof
    // The interact ability is NOT bound to the modular-input system: its
    // TryActivate(pressed=1) has NO Server_*ModularAbilityInput* entries
    // nearby. Skills (glider, attacks) ARE modular-input bound - their
    // activations sit adjacent to ModularAbilityInput Pressed/Released
    // entries carrying InputTag.* tags. Scan backward for the newest
    // pressed=1 activation that is NOT modular-input adjacent.
    for (int i = n - 1; i >= 0; i--) {
        const xx::RpcEntry& e = rpc->entries[i];
        std::string nm = ue.funcNameOf(e.funcPtr);
        if (nm != "ServerTryActivateAbility") continue;
        uint32_t h = *(uint32_t*)e.parms;
        uint8_t pressed = e.parms[4];
        if (!pressed || h == KNOWN_ITEM) continue;
        // check +-4 ring slots for ModularAbilityInput entries
        bool modularBound = false;
        for (int j = i - 4; j <= i + 4; j++) {
            if (j < 0 || j >= n || j == i) continue;
            std::string nm2 = ue.funcNameOf(rpc->entries[j].funcPtr);
            if (nm2.find("ModularAbilityInput") != std::string::npos) {
                modularBound = true;
                break;
            }
        }
        if (!modularBound) return h;   // the interact handle
    }
    return 0;
}

// ---------------- open-correlation handle latch (timestamp window) ----------------
// A MANUAL gold/red box open (state 0->3) means F was pressed ~5-6s ago
// (the channel duration). With ring timestamps we take the pressed=1
// activation that happened 2-10s before the flip = the F press. Skills
// used DURING the channel are <2s before the flip -> excluded. Immune to
// combat noise and mid-channel skill use.
void XiXing::watchManualOpen(const Mem& mem, UE4& ue) {
    if (stats.running) return;                      // don't latch during our own opens
    if (!rpc || rpc->magic != xx::RPC_MAGIC) {
        static DWORD lastRpcErr = 0;
        if (GetTickCount() - lastRpcErr > 5000) {
            lastRpcErr = GetTickCount();
            xxLog(rpc ? "latch: rpc magic bad" : "latch: rpc null");
        }
        return;
    }
    // STEP 1: harvest gold/red actor ADDRESSES from the containers list.
    // fullScan EXCLUDES opened boxes (state==3 -> skip), so we can't rely on
    // the list for state tracking - we collect addresses here, then read
    // states from our OWN set below (survives the box leaving the list).
    const std::vector<ContainerEsp>& cs = ue.containers();
    for (const auto& c : cs) {
        if (c.isTool) continue;   // any BOX quality calibrates (tools lack the 0x2B8 open-state)
        if (lastBoxStates.find(c.actor) == lastBoxStates.end()) {
            // new box: baseline its current state (no flip on first sight)
            uint32_t st = mem.readU32(c.actor + 0x2B8) & 0xFF;
            lastBoxStates[c.actor] = st;
        }
    }
    // STEP 2: read states from OUR tracked set (independent of the list).
    // This catches the 0->3 flip even after fullScan removes the box.
    static DWORD lastDiag = 0;
    bool diagDue = GetTickCount() - lastDiag > 5000;
    if (diagDue) {
        lastDiag = GetTickCount();
        char b[160];
        sprintf_s(b, "latch diag: %d cont, tracking %d boxes",
                  (int)cs.size(), (int)lastBoxStates.size());
        xxLog(b);
    }
    for (auto it = lastBoxStates.begin(); it != lastBoxStates.end(); ) {
        uintptr_t actor = it->first;
        uint32_t prev = it->second;
        uint32_t st = mem.readU32(actor + 0x2B8) & 0xFF;
        it->second = st;
        if (prev == 0 && st == 3) {
            char dbg[128];
            sprintf_s(dbg, "FLIP DETECTED: actor=%llX", (unsigned long long)actor);
            xxLog(dbg);
            // derive the handle: newest pressed=1 TryActivateAbility (no modular
            // filter - during combat every entry has ModularInput neighbors within
            // ±16, blocking everything; the verification step catches wrong handles)
            // Pick the activation that STARTED this open, NOT the newest. The
            // open channel is ~5-6s, so the interact TryActivate fired ~5.5s
            // before this flip; skills/items used DURING the channel are newer
            // and "newest" wrongly latched those (M4 then cast a skill). Choose
            // the pressed TryActivate whose age best matches the channel.
            DWORD flipTick = GetTickCount();
            const uint32_t CHANNEL_MS = 5500;
            int n = rpc->count;
            if (n > 512) n = 512;
            const uint32_t KNOWN_ITEM = 0x77;
            uint32_t bestH = 0; long bestDiff = 1L << 28;
            for (int i = n - 1; i >= 0; i--) {
                const xx::RpcEntry& e = rpc->entries[i];
                if (ue.funcNameOf(e.funcPtr) != "ServerTryActivateAbility") continue;
                uint32_t h = *(uint32_t*)e.parms;
                uint8_t pressed = e.parms[4];
                if (!pressed || h == KNOWN_ITEM) continue;
                long age = (long)(flipTick - e.tick);
                long diff = age - (long)CHANNEL_MS; if (diff < 0) diff = -diff;
                if (diff < bestDiff) { bestDiff = diff; bestH = h; }
            }
            if (bestH && bestDiff < 3500) {   // a plausible channel-start activation
                latchedHandle = bestH;
                char b[96];
                sprintf_s(b, "handle latched: %llX (channel-timed, off=%ldms)",
                          (unsigned long long)bestH, bestDiff);
                xxLog(b);
            } else {
                xxLog("latch: no channel-timed activation - kept prior handle");
            }
        }
        // prune: remove boxes that are gone (class unreadable) or long-opened
        if (st == 3 && prev == 3) {
            // been open for at least one cycle - stop tracking
            it = lastBoxStates.erase(it);
        } else {
            ++it;
        }
    }
}

// ---------------- per-frame driver ----------------
void XiXing::tick(const Mem& mem, UE4& ue) {
    if (!mem.attached()) return;
    // Do NOT inject/hook until the game has a live pawn (past the pure
    // loading screens). installHook() SUSPENDS EVERY game thread to patch
    // ProcessEvent; doing that mid-load deadlocks the still-initializing
    // game -> "character frozen, camera won't move". Wait for a pawn first.
    uintptr_t pawn = ue.getPawn();
    if (!pawn) return;
    injectDll(mem);
    if (!tryConnect(mem)) return;
    // flush stale queue (a crashed session's leftovers fault on dead pointers)
    if (shared->pending > 0 && shared->hookOk != xx::MAGIC) {
        uint32_t abandoned = shared->pending;   // capture BEFORE clearing -
        shared->pending = 0;                    // (the old += pending-after-
        shared->doneSeq += abandoned;           //  zero bug left doneSeq stuck
    }                                           //  and the slot index desynced)
    // arm the RPC logger on the live ASC whenever the filter is unset/stale
    pawn = ue.getPawn();
    if (pawn) {
        uintptr_t asc = mem.readPtr(pawn + 0xC38);
        if (fnPtrSaneX(asc) && rpc->filterThis != asc) {
            rpc->count = 0;
            rpc->filterThis = asc;
            lastBoxStates.clear();   // world/possess changed - state baseline resets
            latchedHandle = 0;       // handle drifts every match - re-latch
        }
    }
    // heartbeat
    static uint32_t lastPump = 0;
    static DWORD lastPumpTick = 0;
    stats.queueAlive = lastPumpTick != 0 && (GetTickCount() - lastPumpTick) < 5000;
    if (shared->pump != lastPump) { lastPump = shared->pump; lastPumpTick = GetTickCount(); }

    // RING KEEP-FRESH: the v4 DLL's ring is capped at 512 entries (not circular).
    // Movement spam fills it in seconds, so the F-press activation never gets
    // recorded. While idle (no pipeline), auto-clear the ring when it fills,
    // so it's always ready to capture the next F-press.
    if (!stats.running && rpc && rpc->magic == xx::RPC_MAGIC && rpc->count >= 480) {
        rpc->count = 0;
    }

    // latch the interact handle on manual opens (flip detection)
    if (stats.queueAlive) watchManualOpen(mem, ue);
}

// ---------------- M4 trigger ----------------
void XiXing::trigger(const Mem& mem, UE4& ue) {
    if (stats.running) return;
    if (worker.joinable()) worker.join();
    stopping = false;
    stats.running = true;
    stats.boxesDone = 0;
    stats.dropsPicked = 0;
    stats.setPhase(L"starting...");
    // copies for the thread (mem/ue are static in main)
    worker = std::thread([this, &mem, &ue]() { runPipeline(&mem, &ue); });
}

void XiXing::stop() {
    stopping = true;
    if (worker.joinable()) worker.join();
    stats.running = false;
}

// ---------------- the pipeline (worker thread) ----------------
static const wchar_t* GOLD_NAMES[] = { L"金丝楠木箱柜", L"鎏金兽首百宝箱", L"紫漆牡丹箱" };
static const wchar_t* RED_NAMES[] = { L"花梨木龙纹箱", L"赤血龙木柜" };

static bool isGoldName(const std::wstring& n) {
    for (auto g : GOLD_NAMES) if (n == g) return true;
    return false;
}
static bool isRedName(const std::wstring& n) {
    for (auto r : RED_NAMES) if (n == r) return true;
    return false;
}

struct Target {
    uintptr_t actor, com;
    std::wstring name;
    int quality;      // 3=gold 4=red
    double dist;
    bool tool;        // mining/collect node
};

void XiXing::runPipeline(const Mem* memP, UE4* ueP) {
    const Mem& mem = *memP;
    UE4& ue = *ueP;
    auto setPhase = [this](const wchar_t* p) { stats.setPhase(p); };

    // ---- resolve ----
    setPhase(L"resolving...");
    uintptr_t pawn = ue.getPawn();
    if (!pawn) { setPhase(L"no pawn"); stats.running = false; return; }
    uintptr_t isCom = mem.readPtr(pawn + 0x1328);
    uintptr_t asc = mem.readPtr(pawn + 0xC38);
    uintptr_t isCls = mem.readPtr(isCom + 0x10);
    uintptr_t ascCls = mem.readPtr(asc + 0x10);
    ufSetPre = ue.findFunction(isCls, "ServerSetPreBeInteractComponent");
    ufLocalTry = ue.findFunction(ascCls, "TryActivateAbility");   // LOCAL path
    ufPickup = ue.findFunction(mem.readPtr(pawn + 0x10), "Server_RequestPickupByUI");
    if (!ufSetPre || !ufLocalTry || !ufPickup) {
        setPhase(L"UFunction resolve failed");
        stats.running = false;
        return;
    }
    // DIAGNOSTIC: log resolved UFunction addresses + flags to catch wrong-function resolution
    {
        char b[256];
        uint32_t tryFlags = mem.readU32(ufLocalTry + 0xB0);
        uint32_t setpreFlags = mem.readU32(ufSetPre + 0xB0);
        sprintf_s(b, "UF diag: SetPre=%llX(fl=%X) LocalTry=%llX(fl=%X %s) Pickup=%llX | isCom=%llX ASC=%llX",
                  (unsigned long long)ufSetPre, setpreFlags,
                  (unsigned long long)ufLocalTry, tryFlags,
                  (tryFlags & 0x00200000) ? "SERVER-RPC!" : "local",
                  (unsigned long long)ufPickup,
                  (unsigned long long)isCom, (unsigned long long)asc);
        xxLog(b);
        // SAFETY: if LocalTry is actually the SERVER RPC, abort (wrong function = crash)
        if (tryFlags & 0x00200000) {
            xxLog("ABORT: TryActivateAbility resolved to SERVER RPC - wrong function!");
            setPhase(L"wrong UFunction - aborting");
            stats.running = false;
            return;
        }
    }

    // ---- targets ---- (scanned FIRST: the vacuum-only path needs no handle)
    uintptr_t handle = latchedHandle;   // declared early: openOne captures it
    setPhase(L"scanning...");
    // tool nodes (mining/collect) have no confirmed "consumed" state byte yet;
    // once attempted they go here so collectTargets stops re-selecting them.
    std::unordered_set<uintptr_t> toolTried;
    auto collectTargets = [&]() -> std::vector<Target> {
        std::vector<Target> out;
        const std::vector<ContainerEsp>& cs = ue.containers();
        double ppos[3];
        bool havePos = ue.readActorPos(pawn, ppos);
        for (const auto& c : cs) {
            Target t{};
            t.actor = c.actor;
            std::string cn = ue.classNameOf(c.actor);
            bool isTool = (cn == "BP_Mining_C" || cn == "BP_Collect_C");
            t.tool = isTool;
            if (isTool) {
                if (toolTried.count(c.actor)) continue;      // already attempted this run
                t.quality = 0;                               // tool nodes sort AFTER every box
                // per-instance interact component is at +0x2C8; +0x338 holds a
                // SHARED CDO ptr (identical on every node) - using it was why
                // tool nodes never opened. Use +0x2C8.
                t.com = mem.readPtr(c.actor + 0x2C8);
            } else {
                // OPEN ANY BOX QUALITY (user request; was red/gold only)
                t.quality = (int)c.quality + 1;   // White=1 .. Red=5 (higher opens first)
                if (t.quality < 1) t.quality = 1; // unknown-name box: still open, lowest
                t.com = mem.readPtr(c.actor + 0x338);
                if (!fnPtrSaneX(t.com)) t.com = mem.readPtr(c.actor + 0x2C8);
            }
            t.name = c.name;
            t.dist = c.dist;
            if (!fnPtrSaneX(t.com)) continue;
            out.push_back(t);
        }
        // red > gold; boxes before tool nodes; nearest first within a tier
        std::sort(out.begin(), out.end(), [](const Target& a, const Target& b) {
            if (a.quality != b.quality) return a.quality > b.quality;
            if (a.tool != b.tool) return !a.tool;
            return a.dist < b.dist;
        });
        return out;
    };

    auto targetDone = [&](const Target& t) {
        std::string cn = ue.classNameOf(t.actor);
        if (cn == "BP_Mining_C" || cn == "BP_Collect_C") return false;   // tool node still alive
        if (cn == "BP_YiGui_C" || cn == "BP_HeZi_C" || cn == "BP_BaoXiang_C")
            return (mem.readU32(t.actor + 0x2B8) & 0xFF) == 3;           // box: state 3 = opened (BYTE)
        return true;   // class gone/changed = consumed
    };

    auto openOne = [&](Target& t) -> bool {
        // G3: liveness check - the 2s-cached target list can reference
        // actors the server already destroyed (streamed out / consumed)
        {
            std::string cn = ue.classNameOf(t.actor);
            if (cn != "BP_YiGui_C" && cn != "BP_HeZi_C" && cn != "BP_BaoXiang_C"
                && cn != "BP_Mining_C" && cn != "BP_Collect_C") return false;
        }
        // SAFETY NET: verify the UFunction pointers are still valid (readable
        // and their class is "Function") before EVERY submit. A stale/freed
        // UFunction = ProcessEvent on garbage = game crash.
        {
            uintptr_t tryCls = mem.readPtr(ufLocalTry + 0x10);
            if (!fnPtrSaneX(tryCls)) { setPhase(L"ufLocalTry stale - abort"); xxLog("SAFETY: ufLocalTry unreadable"); return false; }
            uintptr_t preCls = mem.readPtr(ufSetPre + 0x10);
            if (!fnPtrSaneX(preCls)) { setPhase(L"ufSetPre stale - abort"); xxLog("SAFETY: ufSetPre unreadable"); return false; }
        }
        // SAFETY (crash #5 fix): re-read the component FRESH right before the
        // submit, and skip if the box is already opened. The cached t.com can
        // point to a component the server already freed (box consumed) -> the
        // queued ProcessEvent dereferences a dead pointer -> SEH -> crash. A
        // fresh read shrinks that window to the minimum.
        if (targetDone(t)) return true;
        uint64_t com = t.tool ? mem.readPtr(t.actor + 0x2C8)        // tool: per-instance com
                              : mem.readPtr(t.actor + 0x338);       // box: TB family
        if (!fnPtrSaneX(com) && !t.tool) com = mem.readPtr(t.actor + 0x2C8);
        if (!fnPtrSaneX(com)) return false;                   // component gone
        if (!submit(mem, isCom, ufSetPre, &com, 8)) return false;
        Sleep(400);
        uint8_t parms[24] = {};
        *(uint32_t*)parms = (uint32_t)handle;
        parms[4] = 1;   // bAllowRemoteActivation
        if (!submit(mem, asc, ufLocalTry, parms, sizeof(parms))) return false;
        if (t.tool) {
            // no confirmed "consumed" byte for tool nodes yet: drive the same
            // interact ability, wait out the harvest channel, and treat as
            // attempted (toolTried stops re-selection). If the actor gets
            // destroyed mid-wait, that IS the harvested signal.
            toolTried.insert(t.actor);
            for (int i = 0; i < 16 && !stopping; i++) {
                Sleep(350);
                std::string cn = ue.classNameOf(t.actor);
                if (cn != "BP_Mining_C" && cn != "BP_Collect_C") return true;
            }
            return true;
        }
        for (int i = 0; i < 26 && !stopping; i++) {
            Sleep(350);
            if (targetDone(t)) return true;
        }
        return targetDone(t);
    };

    // ---- red/gold drop picker (shared by interleave and final vacuum) ----
    // one-at-a-time with liveness checks (rapid-fire references to destroyed
    // actors corrupted the game - crash cause #1)
    std::unordered_set<uintptr_t> pickedSet;
    auto pickRedGoldDrops = [&](DWORD timeoutMs) -> int {
        int picked = 0;
        DWORD t0 = GetTickCount();
        while (GetTickCount() - t0 < timeoutMs && !stopping) {
            const std::vector<DropEsp>& drops = ue.drops();
            bool acted = false;
            for (const auto& d : drops) {
                if (d.picked || pickedSet.count(d.actor)) continue;
                int color = (int)(mem.readU32(d.actor + 0x4BA) & 0xFF) - 2;
                if (color < 3) continue;   // red/gold items only
                // double liveness check: the 400ms-cached drop list can contain
                // actors the server already destroyed - a pickup on those = SEH
                // storm + heap corruption (crash after ~8 interactions)
                if (ue.classNameOf(d.actor) != "BP_DropInteract_C") {
                    pickedSet.insert(d.actor);
                    continue;
                }
                uint8_t pk[16] = {};
                *(uint64_t*)pk = d.actor;
                if (submit(mem, pawn, ufPickup, pk, sizeof(pk))) {
                    pickedSet.insert(d.actor);
                    picked++;
                    stats.dropsPicked++;
                    acted = true;
                    setPhase(L"picking drops...");
                    for (int w = 0; w < 10 && !stopping; w++) {
                        Sleep(200);
                        if (ue.classNameOf(d.actor) != "BP_DropInteract_C") break;
                    }
                    Sleep(300);
                    break;   // one at a time
                }
            }
            if (!acted) {
                Sleep(1000);
                std::vector<DropEsp> again = ue.drops();   // O1 snapshot
                bool any = false;
                for (const auto& d : again) {
                    if (!d.picked && !pickedSet.count(d.actor)) {
                        int color = (int)(mem.readU32(d.actor + 0x4BA) & 0xFF) - 2;
                        if (color >= 3) { any = true; break; }
                    }
                }
                if (!any) break;
            }
        }
        return picked;
    };

    // ---- handle (only needed when there are boxes to open) ----
    // LATCHED ONLY (from manual box open). The ring-newest approach picks up
    // whatever the user did LAST (jump, skill) - only the flip-latched handle
    // is guaranteed to be the interact ability.

    // ---- verify on the nearest target ----
    setPhase(L"verifying handle...");
    std::vector<Target> targets = collectTargets();
    if (targets.empty()) {
        // nothing to OPEN (e.g. the user opened every gold/red box manually) -
        // but their drops may still be on the ground. Vacuum-only run:
        // needs NO handle (no ability activation happens).
        xxLog("pipeline: 0 targets - vacuum-only run");
        setPhase(L"no boxes - vacuuming drops...");
        Sleep(2000);
        pickRedGoldDrops(60000);
        wchar_t vb[96];
        swprintf(vb, 96, L"done: %d boxes, %d items", stats.boxesDone, stats.dropsPicked);
        setPhase(vb);
        xxLog("pipeline finished (vacuum-only)");
        stats.running = false;
        return;
    }
    if (!handle) {
        setPhase(L"NO LATCH - open a gold/red box with F, wait 2s, then M4");
        xxLog("M4 refused: no latched handle");
        stats.running = false;
        return;
    }
    bool verified = false;
    for (int attempt = 0; attempt < 2 && !verified && !stopping; attempt++) {
        targets = collectTargets();
        if (targets.empty()) break;
        // prefer a real BOX to verify the handle: a box has an unambiguous
        // opened-state (+0x2B8==3); tool nodes have no confirmed done-byte yet.
        const Target* vt = nullptr;
        for (const auto& t : targets) if (!t.tool) { vt = &t; break; }
        if (!vt) {
            // no BOX in range - only tool nodes. We already hold a latched
            // handle (checked above); trust it and let the loop harvest tools.
            xxLog("verify: no BOX, only tool nodes - trusting latch, harvesting tools");
            verified = true;
            break;
        }
        {
            char b[128];
            sprintf_s(b, "verify attempt %d: BOX q=%d d=%.0fm handle=%X tool=%d",
                      attempt + 1, vt->quality, vt->dist / 100.0,
                      (uint32_t)handle, (int)vt->tool);
            xxLog(b);
        }
        Target vtCopy = *vt;   // openOne takes Target& (non-const)
        if (openOne(vtCopy)) {
            verified = true;
            stats.boxesDone++;
        } else {
            Sleep(4000);
        }
    }
    if (!verified) {
        setPhase(L"verification failed (press F once, then M4)");
        xxLog("pipeline: verification FAILED (handle rejected - relatch with F)");
        stats.running = false; return; }
    latchedHandle = handle;   // verified live - latch for subsequent M4 presses

    // ---- open up to 5 per trigger (MATCHES the proven Python xxstar.py flow:
    // verify -> open all with 9s pacing -> final vacuum; NO interleaved pickup,
    // NO rescan - the Python version doesn't do them during the open loop and
    // it's the only version that has never crashed) ----
    Sleep(3500);   // pace between boxes (shortened per user; ability still drains)
    int opened = 1;
    // SAFETY: first M4 press after a fresh latch does verification + 1 box only.
    // If the game survives, the next M4 press runs the full 5. Minimizes crash exposure.
    static bool firstRunAfterLatch = true;
    const int CAP = firstRunAfterLatch ? 2 : 6;   // 8 hit crash #5 (~8 interactions); 6 stays safely under
    if (firstRunAfterLatch) {
        firstRunAfterLatch = false;
        setPhase(L"safety run: verify + 1 box (press M4 again for full 5)");
    }
    for (int i = 1; i < CAP && !stopping; i++) {
        // G4: revalidate pawn/ASC/isCom (mount/respawn/streaming can replace them)
        {
            uintptr_t p2 = ue.getPawn();
            if (p2 != pawn) { setPhase(L"pawn changed - abort"); break; }
            uintptr_t a2 = mem.readPtr(p2 + 0xC38);
            if (a2 != asc) { setPhase(L"ASC changed - abort"); break; }
            uintptr_t i2 = mem.readPtr(p2 + 0x1328);
            if (i2 != isCom) { setPhase(L"isCom changed - abort"); break; }
        }
        targets = collectTargets();
        bool did = false;
        for (auto& t : targets) {
            if (targetDone(t)) continue;
            wchar_t buf[96];
            swprintf(buf, 96, L"opening %d/%d...", i + 1, CAP);
            setPhase(buf);
            if (openOne(t)) {
                stats.boxesDone++;
                did = true;
            }
            break;   // one per round
        }
        if (!did) break;
        Sleep(3500);   // same pacing (shortened per user)
    }

    // ---- final vacuum (all drops at once, like the Python script) ----
    setPhase(L"vacuum...");
    Sleep(4000);
    pickRedGoldDrops(60000);

    wchar_t buf[96];
    swprintf(buf, 96, L"done: %d boxes, %d items", stats.boxesDone, stats.dropsPicked);
    setPhase(buf);
    xxLog("pipeline finished");
    stats.running = false;
}

// ---------------- F9: one-shot dev RPC (damage boost etc.) ----------------
void XiXing::fireDevRpc(const Mem& mem, UE4& ue, const char* funcName, int32_t value) {
    if (!shared || shared->magic != xx::MAGIC || shared->hookOk != xx::MAGIC) {
        xxLog("fireDevRpc: queue not ready");
        return;
    }
    uintptr_t pawn = ue.getPawn();
    if (!pawn) { xxLog("fireDevRpc: no pawn"); return; }
    uintptr_t uf = ue.findFunction(mem.readPtr(pawn + 0x10), funcName);
    if (!uf) { xxLog("fireDevRpc: UFunction not found"); return; }
    // parms: Value@0(4) = int32
    uint8_t parms[16] = {};
    *(int32_t*)parms = value;
    if (!submit(mem, pawn, uf, parms, sizeof(parms))) {
        xxLog("fireDevRpc: submit failed");
        return;
    }
    char b[96];
    sprintf_s(b, "fireDevRpc: %s(%d) sent", funcName, value);
    xxLog(b);
    stats.setPhase(L"ATK boosted!");
}
