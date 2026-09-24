#pragma once
// XiXing v3 (integrated): one EXE does everything - DLL injection, ESP, and
// the XiXing pipeline on the M4 (XBUTTON1) mouse side button.
//
// Pipeline (worker thread, never blocks rendering):
//   1. resolve pawn / ASC / isCom / UFunctions (LOCAL TryActivateAbility path)
//   2. derive the interact handle from the v4 DLL's RPC ring (newest pressed,
//      excluding known item handles) - the handle drifts EVERY match
//   3. verify on the nearest gold/red box, then open up to 5 per trigger
//      (9s pacing - 6 rapid opens killed the session once; human rhythm)
//   4. two late-streaming rescans, then red/gold drop pickup to backpack
#include "mem.h"
#include "ue4.h"
#include <windows.h>
#include <cstdint>
#include <string>
#include <thread>
#include <atomic>
#include <unordered_set>

namespace xx {
constexpr uint32_t MAGIC = 0x58495847;
constexpr uint32_t RPC_MAGIC = 0x58495852;
constexpr int CMD_SLOTS = 16;
constexpr int PARAM_BYTES = 320;
constexpr int LOG_SLOTS = 256;

#pragma pack(push, 1)
struct Cmd {
    uint32_t op;
    uint32_t pad;
    uint64_t thisPtr;
    uint64_t funcPtr;
    uint64_t paramsPtr;
    uint64_t result;
};
struct LogEntry {
    uint64_t thisPtr;
    uint64_t funcPtr;
    uint8_t  params[64];
};
struct Shared {
    uint32_t magic;
    uint32_t version;
    volatile uint32_t pending;
    volatile uint32_t doneSeq;
    volatile uint32_t hookOk;
    volatile uint32_t pump;
    volatile uint32_t logCount;
    uint32_t pad;
    volatile uint64_t logFilterThis;
    Cmd      cmds[CMD_SLOTS];
    LogEntry log[LOG_SLOTS];
};
struct RpcEntry {
    uint64_t funcPtr;
    uint64_t thisPtr;
    uint32_t tick;             // GetTickCount() at capture (xixing5+ layout)
    uint8_t  parms[124];       // parms start at +20 - MUST match xixing_dll.cpp!
};
// v4 (xixing4.dll) wrote parms at +16 with NO tick - that mismatch made the
// latch read the TICK as the ability handle (TryActivate(tick) = silent no-op).
struct RpcShared {
    uint32_t magic;
    uint32_t version;
    volatile uint32_t count;
    uint32_t pad2;
    volatile uint64_t filterThis;
    RpcEntry entries[512];
};
#pragma pack(pop)
} // namespace xx

struct XxStats {
    // O2 fix: fixed buffer (no std::wstring across threads)
    wchar_t phase[96] = {};
    CRITICAL_SECTION cs;
    XxStats() { InitializeCriticalSection(&cs); }
    ~XxStats() { DeleteCriticalSection(&cs); }
    void setPhase(const wchar_t* p) {
        EnterCriticalSection(&cs);
        wcsncpy_s(phase, p, 95);
        LeaveCriticalSection(&cs);
    }
    void getPhase(wchar_t* out, size_t n) {
        EnterCriticalSection(&cs);
        wcsncpy_s(out, n, phase, _TRUNCATE);
        LeaveCriticalSection(&cs);
    }
    int boxesDone = 0;
    int dropsPicked = 0;
    int targetsTotal = 0;
    bool running = false;
    bool injected = false;
    bool queueAlive = false;
};

class XiXing {
public:
    ~XiXing() { stop(); }

    // per-frame driver: ensures injection + connection (cheap, non-blocking)
    void tick(const Mem& mem, UE4& ue);
    // M4 pressed -> start the pipeline (spawns a worker thread)
    void trigger(const Mem& mem, UE4& ue);
    // F9: one-shot dev RPC (ServeraddATK etc.) via the queue
    void fireDevRpc(const Mem& mem, UE4& ue, const char* funcName, int32_t value);
    // M5 pressed -> abort the running pipeline at the next checkpoint
    void abort() { if (stats.running) { stopping = true; stats.setPhase(L"aborting..."); } }
    void stop();

    XxStats stats;

private:
    // connection state
    HANDLE mapFile = nullptr;
    HANDLE rpcMapFile = nullptr;
    xx::Shared* shared = nullptr;
    xx::RpcShared* rpc = nullptr;
    uintptr_t paramsBase = 0;
    DWORD gamePid = 0;
    bool injectionTried = false;

    // UFunctions (per world)
    uintptr_t ufSetPre = 0, ufLocalTry = 0, ufPickup = 0;
    uintptr_t ufWorldMark = 0;

    // LATCHED interact handle: captured at the moment of a MANUAL box open
    // (gold/red container flips 0->3 while the pipeline is idle).
    // KEY FIX: fullScan EXCLUDES opened boxes (state==3 -> skip), so the
    // containers() list NEVER shows a 0->3 flip - the box just vanishes.
    // We maintain our OWN tracked set: collect gold/red actors from the list,
    // then read their states independently each frame (even after they leave
    // the containers list).
    uintptr_t latchedHandle = 0;
    std::unordered_map<uintptr_t, uint32_t> lastBoxStates;   // actor -> last known state

    std::thread worker;
    std::atomic<bool> stopping{false};

    bool tryConnect(const Mem& mem);
    bool injectDll(const Mem& mem);
    bool canSend() const;
    bool submit(const Mem& mem, uintptr_t thisPtr, uintptr_t funcPtr,
                const void* params, size_t n);
    void runPipeline(const Mem* mem, UE4* ue);
    uintptr_t deriveHandle(UE4& ue);
    void watchManualOpen(const Mem& mem, UE4& ue);   // tick: latch on manual box opens
};
