#pragma once
// External memory access layer with a read/write API boundary; the current path uses ReadProcessMemory.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <cstdint>
#include <string>
#include <psapi.h>

class Mem {
public:
    bool attach(const wchar_t* procNamePart) {
        DWORD pids[1024];
        DWORD needed = 0;
        if (!EnumProcesses(pids, sizeof(pids), &needed)) return false;
        int count = needed / sizeof(DWORD);
        for (int i = 0; i < count; i++) {
            HANDLE h = OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ | PROCESS_VM_WRITE |
                                   PROCESS_VM_OPERATION | PROCESS_DUP_HANDLE, FALSE, pids[i]);
            if (!h) continue;
            wchar_t name[256] = {};
            HMODULE mods[2]; DWORD cbNeeded = 0;
            if (EnumProcessModulesEx(h, mods, sizeof(mods), &cbNeeded, LIST_MODULES_64BIT) && cbNeeded >= sizeof(HMODULE)) {
                GetModuleFileNameExW(h, mods[0], name, 256);
                if (wcsstr(name, procNamePart)) {
                    procHandle = h;
                    procId = pids[i];
                    moduleBase = (uintptr_t)mods[0];
                    MODULEINFO mi{};
                    GetModuleInformation(h, mods[0], &mi, sizeof(mi));
                    moduleSize = mi.SizeOfImage;
                    return true;
                }
            }
            CloseHandle(h);
        }
        return false;
    }

    void detach() {
        if (procHandle) { CloseHandle(procHandle); procHandle = nullptr; }
        procId = 0; moduleBase = 0; moduleSize = 0;
    }

    bool attached() const { return procHandle != nullptr; }

    bool read(uintptr_t addr, void* buf, size_t size) const {
        SIZE_T got = 0;
        if (!ReadProcessMemory(procHandle, (LPCVOID)addr, buf, size, &got)) return false;
        return got == size;
    }

    bool write(uintptr_t addr, const void* buf, size_t size) const {
        SIZE_T got = 0;
        if (!WriteProcessMemory(procHandle, (LPVOID)addr, buf, size, &got)) return false;
        return got == size;
    }

    // allocate RW memory inside the target (params buffers for the game-thread queue)
    uintptr_t allocRemote(size_t size) const {
        return (uintptr_t)VirtualAllocEx(procHandle, nullptr, size, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    }
    void freeRemote(uintptr_t addr) const {
        if (addr) VirtualFreeEx(procHandle, (void*)addr, 0, MEM_RELEASE);
    }

    // safe read: returns false without touching buf on failure
    template <typename T>
    bool readT(uintptr_t addr, T& out) const {
        return read(addr, &out, sizeof(T));
    }

    uintptr_t readPtr(uintptr_t addr) const {
        uintptr_t v = 0;
        read(addr, &v, sizeof(v));
        return v;
    }
    uint32_t readU32(uintptr_t addr) const {
        uint32_t v = 0;
        read(addr, &v, sizeof(v));
        return v;
    }
    double readF64(uintptr_t addr) const {
        double v = 0;
        read(addr, &v, sizeof(v));
        return v;
    }
    float readF32(uintptr_t addr) const {
        float v = 0;
        read(addr, &v, sizeof(v));
        return v;
    }

    HANDLE procHandle = nullptr;
    DWORD procId = 0;
    uintptr_t moduleBase = 0;
    uintptr_t moduleSize = 0;
};
