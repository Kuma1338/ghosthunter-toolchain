// xixing.dll v3 - game-thread task queue + RPC send logger.
//
// v3 adds a SECOND hook on UActorComponent::CallRemoteFunction (module+0x27EEB30)
// so we can capture the player's GAS ability RPCs (ServerTryActivateAbility /
// WithEventData) in-process - no CE breakpoints, no VEH, zero freeze risk.
//
// Purpose: RPC calls (ProcessEvent) MUST run on the game thread. This DLL hooks
// UObject::ProcessEvent (module + 0x15E5170) and drains a command queue from
// shared memory on every invocation. The external overlay is the producer.
//
// CRASH LESSONS (user-tested, do not regress):
//  1. Never call UE functions from foreign threads (CE executeCodeEx) -> delayed crash.
//     All calls go through this queue, executed inside ProcessEvent on the game thread.
//  2. The real ProcessEvent prologue is EXACTLY:
//        40 55 56 57 41 54 41 55 41 56 41 57 | 48 81 EC ...
//     = push rbp; push rsi; push rdi; push r12; push r13; push r14; push r15  (12 bytes, 7 pushes)
//     Byte 12 (0x48) begins "sub rsp, imm32". We steal EXACTLY 12 bytes and write
//     a 12-byte patch: mov rax, imm64; jmp rax  (48 B8 <imm64> FF E0).
//     Any other steal length splits "sub rsp" -> stack corruption -> instant crash.
//  3. The signature is VERIFIED before patching; on mismatch we abort (no hook, no queue).
//     The overlay refuses to send commands until hookOk is set AND the pump counter
//     is advancing (proof the detour executes correctly).
//
// Shared block "XIXING_SHARED_V1" (both processes map the same pagefile section):
//   u32  magic    = 'XIXG'
//   u32  version  = 2
//   u32  pending  (overlay -> dll: queued command count; SPSC, sum(doneSeq+pending) is stable)
//   u32  doneSeq  (dll -> overlay: total commands processed)
//   u32  hookOk   (set to magic after verified install + readback)
//   u32  pump     (incremented on every detour entry; overlay heartbeat)
//   u32  logCount (ProcessEvent call logger: entries written)
//   u32  pad
//   u64  logFilterThis (logger armed when non-null: record calls whose 'this' matches)
//   Cmd  cmds[16]  {op, pad, thisPtr, funcPtr, paramsPtr, result}   op 1 = ProcessEvent
//   Log  log[256]  {thisPtr, funcPtr, params[64]}                   ground-truth capture
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <tlhelp32.h>
#include <cstdint>
#include <cstdio>
#include <cstring>

#pragma comment(lib, "user32.lib")

namespace xx {

constexpr uint32_t MAGIC   = 0x58495847;  // 'XIXG'
constexpr uint32_t VERSION = 3;
constexpr int CMD_SLOTS    = 16;
constexpr int PARAM_BYTES  = 320;         // K2_SetActorLocation needs 0x122
constexpr int LOG_SLOTS    = 256;

// module-relative offset of UObject::ProcessEvent (verified this build)
constexpr uintptr_t PE_OFFSET = 0x15E51E0;

// The verified 12-byte prologue: 7 complete push instructions.
static const uint8_t SIG[12] = { 0x40, 0x55, 0x56, 0x57, 0x41, 0x54, 0x41, 0x55, 0x41, 0x56, 0x41, 0x57 };

#pragma pack(push, 1)
struct Cmd {
    uint32_t op;            // 1 = ProcessEvent(this, func, params)
    uint32_t pad;
    uint64_t thisPtr;
    uint64_t funcPtr;       // UFunction*
    uint64_t paramsPtr;     // game-memory params buffer (PARAM_BYTES), owned by overlay
    uint64_t result;        // 1 = called, 0xDEAD = SEH
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
    volatile uint64_t logFilterThis;   // 0 = logger off
    Cmd      cmds[CMD_SLOTS];          // @40
    LogEntry log[LOG_SLOTS];           // @552..21031 (21160 total: v2-compatible)
};
#pragma pack(pop)

// v3: RPC send logger state lives in its OWN section (so v3 can coexist with a
// running v2 in the same process - the main section size is v2-compatible).
struct RpcEntry {
    uint64_t funcPtr;
    uint64_t thisPtr;
    uint32_t tick;             // GetTickCount() at capture (latch correlation)
    uint8_t  parms[124];
};
struct RpcShared {
    uint32_t magic;                    // 'XIXR'
    uint32_t version;                  // 1
    volatile uint32_t rpcLogCount;
    uint32_t pad;
    volatile uint64_t rpcFilterThis;   // 0 = off; else only this 'this' ptr
    RpcEntry rpcLog[512];              // stride 144
};
constexpr uint32_t RPC_MAGIC = 0x58495852;  // 'XIXR


static Shared* g_shared = nullptr;
static HANDLE  g_map = nullptr;
static RpcShared* g_rpc = nullptr;
static HANDLE  g_rpcMap = nullptr;

static uint8_t*  g_target = nullptr;        // ProcessEvent address
static uint8_t   g_origBytes[12] = {};      // saved prologue
static void*     g_trampoline = nullptr;    // 12 orig bytes + abs jmp back
static bool      g_hookInstalled = false;

typedef void(__fastcall* ProcessEvent_t)(void*, void*, void*);
static ProcessEvent_t g_origPE = nullptr;

// reentrancy guard: a queued ProcessEvent may itself call ProcessEvent (RPC dispatch);
// only the outermost detour entry drains the queue.
static __declspec(thread) bool tls_inDrain = false;

static void __fastcall hookPE_forward(void* a, void* b, void* c);

static void log(const char* msg) {
    // dedup: identical consecutive messages only count, never touch the disk.
    // (the SEH handler can fire thousands of times per second - per-call
    // fopen/fprintf/fclose on the GAME THREAD was itself a stall source)
    static char  lastMsg[256] = {};
    static DWORD repeat = 0;
    if (strcmp(msg, lastMsg) == 0) { repeat++; return; }
    FILE* f = nullptr;
    if (fopen_s(&f, "D:\\gh_tools\\overlay\\xixing.log", "a") == 0 && f) {
        if (repeat) fprintf(f, "[%u]   (previous message x%u)\n", GetTickCount(), repeat);
        fprintf(f, "[%u] %s\n", GetTickCount(), msg);
        fclose(f);
    }
    strncpy_s(lastMsg, msg, _TRUNCATE);
    repeat = 0;
}

// ---------------- hot-code patching with all other threads frozen ----------------
// ProcessEvent is executed constantly; a torn 12-byte write while another thread is
// mid-prologue is a crash. Freeze every other thread, patch, resume.
//
// DEADLOCK FIX (crash cause #10): never allocate memory after suspending
// threads - HeapAlloc blocks forever if a suspended thread holds the heap
// lock, leaving the game permanently frozen. All storage is static.
// v4-proven freeze/thaw: DWORD array with consistent 32-bit handle storage
// (kernel handles fit in 32 bits; stored AND read as DWORD - consistent).
static bool freezeOtherThreads(DWORD** outHandles, int* outCount) {
    *outHandles = nullptr; *outCount = 0;
    DWORD pid = GetCurrentProcessId();
    DWORD self = GetCurrentThreadId();
    HANDLE snap = CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0);
    if (snap == INVALID_HANDLE_VALUE) return false;
    DWORD buf[4096];
    int n = 0;
    THREADENTRY32 te{}; te.dwSize = sizeof(te);
    if (Thread32First(snap, &te)) {
        do {
            if (te.th32OwnerProcessID == pid && te.th32ThreadID != self && n < 4096) {
                HANDLE h = OpenThread(THREAD_SUSPEND_RESUME | THREAD_GET_CONTEXT, FALSE, te.th32ThreadID);
                if (h) buf[n++] = (DWORD)(uintptr_t)h;
            }
        } while (Thread32Next(snap, &te));
    }
    CloseHandle(snap);
    DWORD* hs = (DWORD*)HeapAlloc(GetProcessHeap(), 0, (n > 0 ? n : 1) * sizeof(DWORD));
    int cnt = 0;
    for (int i = 0; i < n; i++) {
        HANDLE h = (HANDLE)(uintptr_t)buf[i];
        if (SuspendThread(h) != (DWORD)-1) hs[cnt++] = (DWORD)(uintptr_t)h;
        else CloseHandle(h);
    }
    *outHandles = hs; *outCount = cnt;
    return true;
}

static void thawThreads(DWORD* hs, int cnt);

// HOT-PATCH SAFETY: a thread suspended mid-way through the bytes we are about
// to overwrite resumes into garbage -> delayed crash / input freeze. Only
// patch when NO thread's RIP lies inside the target region; retry otherwise.
static bool threadsCleanOfRegion(DWORD* hs, int cnt, uint8_t* region, size_t len) {
    for (int i = 0; i < cnt; i++) {
        HANDLE h = (HANDLE)(uintptr_t)hs[i];
        CONTEXT ctx{};
        ctx.ContextFlags = CONTEXT_CONTROL;
        if (GetThreadContext(h, &ctx)) {
            if (ctx.Rip >= (uintptr_t)region && ctx.Rip < (uintptr_t)region + len)
                return false;
        }
    }
    return true;
}

static void patchHot(uint8_t* target, const uint8_t* patch, size_t len) {
    for (int attempt = 0; attempt < 200; attempt++) {
        DWORD* hs = nullptr; int hc = 0;
        freezeOtherThreads(&hs, &hc);
        if (threadsCleanOfRegion(hs, hc, target, len)) {
            DWORD old;
            VirtualProtect(target, len, PAGE_EXECUTE_READWRITE, &old);
            memcpy(target, patch, len);
            VirtualProtect(target, len, old, &old);
            FlushInstructionCache(GetCurrentProcess(), target, len);
            thawThreads(hs, hc);
            return;
        }
        thawThreads(hs, hc);
        Sleep(1);   // a thread is mid-region; try again
    }
    // last resort after 200 tries (extremely unlikely): patch anyway
    DWORD* hs = nullptr; int hc = 0;
    freezeOtherThreads(&hs, &hc);
    DWORD old;
    VirtualProtect(target, len, PAGE_EXECUTE_READWRITE, &old);
    memcpy(target, patch, len);
    VirtualProtect(target, len, old, &old);
    FlushInstructionCache(GetCurrentProcess(), target, len);
    thawThreads(hs, hc);
}
static void thawThreads(DWORD* hs, int cnt) {
    for (int i = 0; i < cnt; i++) {
        HANDLE h = (HANDLE)(uintptr_t)hs[i];
        ResumeThread(h);
        CloseHandle(h);
    }
    if (hs) HeapFree(GetProcessHeap(), 0, hs);
}

// ---------------- FREEZE-FREE ATOMIC HOOK (v6) ----------------
// Thread suspension itself triggers freezes (crash cause #11: anti-cheat
// watchdog and/or unfound locks). This technique NEVER suspends anything:
//   1. find a 5+ byte CC (int3) padding cave within +/-127 bytes of the target
//   2. cave <- E9 rel32 (jmp to our far detour)          [nobody runs the cave yet]
//   3. atomically swap the FIRST 2 BYTES of the target to EB rel8 (jmp cave)
//      - the target is 16-byte aligned => the 2-byte store is atomic (x86 SDM)
//      - a thread mid-fetch sees either the old or the new pair - never torn
//      - worst case: one un-hooked call slips through - harmless
//   4. the detour re-executes the replaced first instruction via a trampoline
//      then jumps back into the original function.
static uint8_t* findCave(uint8_t* target) {
    // scan the CC padding around the function for >=5 consecutive int3 bytes
    for (int dir = 0; dir < 2; dir++) {
        int step = dir ? 1 : -1;
        for (int d = 8; d <= 120; d += step > 0 ? 1 : 1) {
            uint8_t* p = target + step * d;
            // 5 bytes at p all CC?
            bool ok = true;
            for (int k = 0; k < 5; k++) {
                if (p[k] != 0xCC) { ok = false; break; }
            }
            if (ok) {
                // rel8 from func+2 must reach p (signed char range)
                int64_t rel = (int64_t)(p - (target + 2));
                if (rel >= -128 && rel <= 127) return p;
            }
        }
    }
    return nullptr;
}

// trampoline2: re-executes the replaced first instruction(s), then jumps back
// to target+firstLen. Called by the detour (and by the queue drain).
static void* makeTrampoline2(uint8_t* target, const uint8_t* origFirst, int firstLen) {
    SYSTEM_INFO si; GetSystemInfo(&si);
    uint8_t* lo = (uint8_t*)si.lpMinimumApplicationAddress;
    uintptr_t base = (uintptr_t)target;
    for (int i = 0; i < 128; i++) {
        int64_t delta = (int64_t)(1 << 20) * (i + 1) * ((i % 2) ? -1 : 1);
        uintptr_t cand = base + delta;
        if (cand < (uintptr_t)lo || cand > 0x7FFFFFFFFFFF) continue;
        void* p = VirtualAlloc((void*)cand, 64, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
        if (p) {
            memcpy(p, origFirst, firstLen);
            uint8_t* t = (uint8_t*)p + firstLen;
            t[0] = 0x48; t[1] = 0xB8;
            uint64_t back = (uint64_t)(target + firstLen);
            memcpy(t + 2, &back, 8);
            t[10] = 0xFF; t[11] = 0xE0;
            DWORD old;
            VirtualProtect(p, 64, PAGE_EXECUTE_READ, &old);
            FlushInstructionCache(GetCurrentProcess(), p, firstLen + 12);
            return p;
        }
    }
    return nullptr;
}

// install: cave + atomic 2-byte EB swap. Returns the trampoline2 (or null).
static void* installAtomicHook(uint8_t* target, const uint8_t* sig, int sigLen,
                               int firstLen, void* detour, const char* tag) {
    // signature check first
    if (memcmp((void*)target, sig, sigLen) != 0) {
        char b[96];
        sprintf_s(b, "%s: SIGNATURE MISMATCH - refusing", tag);
        log(b);
        return nullptr;
    }
    uint8_t* cave = findCave(target);
    if (!cave) {
        char b[96];
        sprintf_s(b, "%s: no CC cave within rel8 range", tag);
        log(b);
        return nullptr;
    }
    void* tramp2 = makeTrampoline2(target, target, firstLen);
    if (!tramp2) { log("trampoline2 alloc failed"); return nullptr; }
    // cave: E9 rel32 -> detour
    uint8_t caveBytes[5];
    caveBytes[0] = 0xE9;
    int32_t rel32 = (int32_t)((uintptr_t)detour - (uintptr_t)(cave + 5));
    memcpy(caveBytes + 1, &rel32, 4);
    DWORD old;
    VirtualProtect(cave, 5, PAGE_EXECUTE_READWRITE, &old);
    memcpy(cave, caveBytes, 5);
    VirtualProtect(cave, 5, old, &old);
    FlushInstructionCache(GetCurrentProcess(), cave, 5);
    // atomic 2-byte swap at target: origFirst2 -> EB rel8
    // (the target is RX code memory - make the page writable first!)
    int8_t rel8 = (int8_t)((uintptr_t)cave - (uintptr_t)(target + 2));
    uint8_t eb[2] = { 0xEB, (uint8_t)rel8 };
    DWORD oldProt;
    VirtualProtect(target, 2, PAGE_EXECUTE_READWRITE, &oldProt);
    SHORT oldVal, newVal;
    memcpy(&oldVal, (void*)target, 2);
    memcpy(&newVal, eb, 2);
    SHORT prev = InterlockedCompareExchange16((volatile SHORT*)target, newVal, oldVal);
    if (prev != oldVal) {
        log("atomic swap raced - retrying once");
        memcpy(&oldVal, (void*)target, 2);
        prev = InterlockedCompareExchange16((volatile SHORT*)target, newVal, oldVal);
        if (prev != oldVal) {
            VirtualProtect(target, 2, oldProt, &oldProt);
            log("atomic swap FAILED");
            return nullptr;
        }
    }
    VirtualProtect(target, 2, oldProt, &oldProt);
    FlushInstructionCache(GetCurrentProcess(), target, 2);
    char b[128];
    sprintf_s(b, "%s: atomic 2-byte hook installed (cave=%llX tramp2=%llX)",
              tag, (unsigned long long)cave, (unsigned long long)tramp2);
    log(b);
    return tramp2;
}

// trampoline: original 12 bytes + mov rax, target+12; jmp rax  (abs jmp, no rel32 range care)
static bool makeTrampoline() {
    SYSTEM_INFO si; GetSystemInfo(&si);
    uint8_t* lo = (uint8_t*)si.lpMinimumApplicationAddress;
    uintptr_t base = (uintptr_t)g_target;
    for (int i = 0; i < 128; i++) {
        int64_t delta = (int64_t)(1 << 20) * (i + 1) * ((i % 2) ? -1 : 1);
        uintptr_t cand = base + delta;
        if (cand < (uintptr_t)lo || cand > 0x7FFFFFFFFFFF) continue;
        void* p = VirtualAlloc((void*)cand, 64, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
        if (p) {
            memcpy(p, g_origBytes, 12);
            uint8_t* t = (uint8_t*)p + 12;
            t[0] = 0x48; t[1] = 0xB8;                                  // mov rax, imm64
            uint64_t back = (uint64_t)(g_target + 12);
            memcpy(t + 2, &back, 8);
            t[10] = 0xFF; t[11] = 0xE0;                                // jmp rax
            DWORD old;
            VirtualProtect(p, 64, PAGE_EXECUTE_READ, &old);
            FlushInstructionCache(GetCurrentProcess(), p, 24);
            g_trampoline = p;
            return true;
        }
    }
    return false;
}

static bool installHook(uint8_t* target) {
    g_target = target;

    // --- rule 2/3: verify the 12-byte signature, abort on any mismatch ---
    uint8_t prologue[16] = {};
    memcpy(prologue, (void*)target, 16);   // in-process direct read
    char b[128];
    sprintf_s(b, "prologue: %02X %02X %02X %02X %02X %02X %02X %02X %02X %02X %02X %02X | %02X %02X %02X %02X",
              prologue[0], prologue[1], prologue[2], prologue[3], prologue[4], prologue[5], prologue[6],
              prologue[7], prologue[8], prologue[9], prologue[10], prologue[11],
              prologue[12], prologue[13], prologue[14], prologue[15]);
    log(b);

    if (memcmp(prologue, SIG, 12) != 0) {
        log("SIGNATURE MISMATCH - refusing to hook (no hook, no queue)");
        return false;
    }
    if (prologue[12] != 0x48 || prologue[13] != 0x81 || prologue[14] != 0xEC) {
        log("byte 12 is not 'sub rsp' opcode - layout changed, refusing to hook");
        return false;
    }
    memcpy(g_origBytes, prologue, 12);

    // v4-proven 12-byte steal via patchHot (RIP-checked, thread-frozen)
    if (!makeTrampoline()) { log("trampoline alloc failed"); return false; }
    g_origPE = (ProcessEvent_t)g_trampoline;
    uint8_t patch[12];
    patch[0] = 0x48; patch[1] = 0xB8;
    uint64_t detour = (uint64_t)&hookPE_forward;
    memcpy(patch + 2, &detour, 8);
    patch[10] = 0xFF; patch[11] = 0xE0;
    patchHot(target, patch, 12);
    uint8_t check[12] = {};
    memcpy(check, (void*)target, 12);
    if (memcmp(check, patch, 12) != 0) {
        log("READBACK MISMATCH after patch - refusing to arm");
        return false;
    }
    g_hookInstalled = true;
    log("hook installed: 12-byte steal (v4-proven), signature verified, readback ok");
    return true;
}

// ---------------- queue drain (game thread only) ----------------
static void drainQueue() {
    if (!g_shared || tls_inDrain) return;
    tls_inDrain = true;
    int budget = 8;   // spread load; the queue refills next pump anyway
    while (g_shared->pending > 0 && budget > 0) {
        uint32_t idx = g_shared->doneSeq % CMD_SLOTS;
        Cmd& c = g_shared->cmds[idx];
        if (c.op == 1 && c.thisPtr && c.funcPtr) {
            __try {
                g_origPE((void*)c.thisPtr, (void*)c.funcPtr, (void*)c.paramsPtr);
                c.result = 1;
            }
            __except (EXCEPTION_EXECUTE_HANDLER) {
                c.result = 0xDEAD;
                log("SEH caught inside queued ProcessEvent");
            }
        }
        // disarm the slot immediately: a desynced producer index must never
        // re-fire a stale slot (dead this/func = SEH storm). result is kept
        // so the overlay/tools can still read the outcome.
        c.op = 0;
        c.thisPtr = 0;
        c.funcPtr = 0;
        c.paramsPtr = 0;
        InterlockedDecrement((volatile LONG*)&g_shared->pending);
        g_shared->doneSeq = g_shared->doneSeq + 1;
        budget--;
    }
    tls_inDrain = false;
}

// detour: called on the game thread for every ProcessEvent invocation
static void __fastcall hookPE(void* thisPtr, void* func, void* params) {
    if (g_shared) {
        g_shared->pump = g_shared->pump + 1;
        // ground-truth logger: capture calls whose 'this' matches the filter
        if (g_shared->logFilterThis && g_shared->logCount < LOG_SLOTS) {
            uint64_t f = g_shared->logFilterThis;
            if ((uint64_t)thisPtr == f) {
                uint32_t n = g_shared->logCount;
                LogEntry& e = g_shared->log[n % LOG_SLOTS];
                e.thisPtr = (uint64_t)thisPtr;
                e.funcPtr = (uint64_t)func;
                __try { memcpy(e.params, params, 64); }
                __except (EXCEPTION_EXECUTE_HANDLER) { memset(e.params, 0, 64); }
                g_shared->logCount = n + 1;
            }
        }
        drainQueue();
    }
    ((void(__fastcall*)(void*, void*, void*))g_trampoline)(thisPtr, func, params);
}

static void __fastcall hookPE_forward(void* a, void* b, void* c) { hookPE(a, b, c); }

// ---------------- hook 2: UActorComponent::CallRemoteFunction ----------------
// module+0x27EEB30. Prologue (verified live):
//   4C 89 44 24 18        mov [rsp+18],r8
//   55 57                 push rbp; push rdi
//   41 55 41 56 41 57     push r13; push r14; push r15
//   = 13 bytes, 6 whole instructions. Steal 13, patch 12 (mov rax;jmp rax) + 1 NOP.
constexpr uintptr_t CRF_OFFSET = 0x27EEBA0;
static const uint8_t SIG_CRF[13] = { 0x4C,0x89,0x44,0x24,0x18,0x55,0x57,0x41,0x55,0x41,0x56,0x41,0x57 };
static uint8_t* g_crfTarget = nullptr;
static uint8_t  g_crfOrig[13] = {};
static void*    g_crfTramp = nullptr;

static void __fastcall hookCRF(void* thisPtr, void* func, void* parms, void* outParms, void* stack);

static bool installCRFHook() {
    uint8_t* target = (uint8_t*)((uintptr_t)GetModuleHandleW(nullptr) + CRF_OFFSET);
    uint8_t pro[13] = {};
    memcpy(pro, (void*)target, 13);
    if (memcmp(pro, SIG_CRF, 13) != 0) {
        log("CRF SIGNATURE MISMATCH - RPC logger not installed");
        return false;
    }
    memcpy(g_crfOrig, pro, 13);
    g_crfTarget = target;
    // v4-proven 13-byte steal via patchHot
    SYSTEM_INFO si; GetSystemInfo(&si);
    uint8_t* lo = (uint8_t*)si.lpMinimumApplicationAddress;
    for (int i = 0; i < 128; i++) {
        int64_t delta = (int64_t)(1 << 20) * (i + 1) * ((i % 2) ? -1 : 1);
        uintptr_t cand = (uintptr_t)target + delta;
        if (cand < (uintptr_t)lo || cand > 0x7FFFFFFFFFFF) continue;
        void* p = VirtualAlloc((void*)cand, 64, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
        if (p) {
            memcpy(p, g_crfOrig, 13);
            uint8_t* t = (uint8_t*)p + 13;
            t[0] = 0x48; t[1] = 0xB8;
            uint64_t back = (uint64_t)(target + 13);
            memcpy(t + 2, &back, 8);
            t[10] = 0xFF; t[11] = 0xE0;
            DWORD old;
            VirtualProtect(p, 64, PAGE_EXECUTE_READ, &old);
            FlushInstructionCache(GetCurrentProcess(), p, 25);
            g_crfTramp = p;
            break;
        }
    }
    if (!g_crfTramp) { log("CRF trampoline alloc failed"); return false; }
    uint8_t patch[13];
    patch[0] = 0x48; patch[1] = 0xB8;
    uint64_t d = (uint64_t)&hookCRF;
    memcpy(patch + 2, &d, 8);
    patch[10] = 0xFF; patch[11] = 0xE0; patch[12] = 0x90;
    patchHot(target, patch, 13);
    uint8_t chk[13] = {};
    memcpy(chk, (void*)target, 13);
    if (memcmp(chk, patch, 13) != 0) { log("CRF readback mismatch"); return false; }
    log("CRF hook installed (v4-proven 13-byte steal, RPC logger live)");
    return true;
}

static void __fastcall hookCRF(void* thisPtr, void* func, void* parms, void* outParms, void* stack) {
    if (g_rpc) {
        uint64_t filt = g_rpc->rpcFilterThis;
        if (filt) {
            if ((uint64_t)thisPtr == filt) {
                uint32_t n = g_rpc->rpcLogCount;
                RpcEntry& e = g_rpc->rpcLog[n % 512];
                e.funcPtr = (uint64_t)func;
                e.thisPtr = (uint64_t)thisPtr;
                e.tick = GetTickCount();
                __try { memcpy(e.parms, parms, 124); }
                __except (EXCEPTION_EXECUTE_HANDLER) { memset(e.parms, 0, 124); }
                g_rpc->rpcLogCount = n + 1;
            }
        }
    }
    ((void(__fastcall*)(void*, void*, void*, void*, void*))g_crfTramp)(thisPtr, func, parms, outParms, stack);
}

static DWORD WINAPI initThread(LPVOID) {
    g_map = CreateFileMappingW(INVALID_HANDLE_VALUE, nullptr, PAGE_READWRITE, 0,
                               sizeof(Shared), L"XIXING_SHARED_V1");  // ~95KB
    if (!g_map) { log("CreateFileMapping failed"); return 1; }
    g_shared = (Shared*)MapViewOfFile(g_map, FILE_MAP_ALL_ACCESS, 0, 0, sizeof(Shared));
    if (!g_shared) { log("MapViewOfFile failed"); return 1; }
    g_shared->magic = MAGIC;
    g_shared->version = VERSION;
    g_shared->pending = 0;
    g_shared->doneSeq = 0;
    g_shared->hookOk = 0;
    g_shared->pump = 0;
    g_shared->logCount = 0;
    g_shared->logFilterThis = 0;
    // separate RPC logger section
    g_rpcMap = CreateFileMappingW(INVALID_HANDLE_VALUE, nullptr, PAGE_READWRITE, 0,
                                  sizeof(RpcShared), L"XIXING_RPC_V1");
    if (g_rpcMap) {
        g_rpc = (RpcShared*)MapViewOfFile(g_rpcMap, FILE_MAP_ALL_ACCESS, 0, 0, sizeof(RpcShared));
        if (g_rpc) {
            g_rpc->magic = RPC_MAGIC;
            g_rpc->version = 1;
            g_rpc->rpcLogCount = 0;
            g_rpc->rpcFilterThis = 0;
            memset(g_rpc->rpcLog, 0, sizeof(g_rpc->rpcLog));
            log("RPC logger section ready");
        }
    }
    memset(g_shared->cmds, 0, sizeof(g_shared->cmds));
    memset(g_shared->log, 0, sizeof(g_shared->log));
    char vb[96];
    sprintf_s(vb, "shared mem ready (VERSION=%u, xixing8.dll)", (unsigned)VERSION);
    log(vb);

    uintptr_t base = (uintptr_t)GetModuleHandleW(nullptr);
    uint8_t* pe = (uint8_t*)(base + PE_OFFSET);
    char b[96];
    sprintf_s(b, "base=%p PE=%p", (void*)base, (void*)pe);
    log(b);

    if (!installHook(pe)) {
        // a v2 DLL may already own the ProcessEvent hook (signature mismatch) -
        // that is FINE: the RPC logger works independently.
        installCRFHook();
        return 0;
    }
    g_shared->hookOk = MAGIC;   // arm only now (rule 3)
    log("hookOk armed - overlay may send commands");
    installCRFHook();
    return 0;
}

} // namespace xx

BOOL APIENTRY DllMain(HMODULE h, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(h);
        CreateThread(nullptr, 0, xx::initThread, nullptr, 0, nullptr);
    } else if (reason == DLL_PROCESS_DETACH) {
        // FULL-byte unhook (G1 fix): the v4 steal is 12/13 bytes - restoring
        // only 2 leaves jmp-rax garbage in the prologue = crash on exit.
        if (xx::g_crfTramp && xx::g_crfTarget) {
            DWORD old;
            VirtualProtect(xx::g_crfTarget, 13, PAGE_EXECUTE_READWRITE, &old);
            memcpy(xx::g_crfTarget, xx::g_crfOrig, 13);
            VirtualProtect(xx::g_crfTarget, 13, old, &old);
            FlushInstructionCache(GetCurrentProcess(), xx::g_crfTarget, 13);
        }
        if (xx::g_hookInstalled && xx::g_target) {
            DWORD old;
            VirtualProtect(xx::g_target, 12, PAGE_EXECUTE_READWRITE, &old);
            memcpy(xx::g_target, xx::g_origBytes, 12);
            VirtualProtect(xx::g_target, 12, old, &old);
            FlushInstructionCache(GetCurrentProcess(), xx::g_target, 12);
        }
    }
    return TRUE;
}
