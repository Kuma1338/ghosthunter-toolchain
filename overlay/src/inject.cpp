// inject.cpp - inject xixing.dll into the game process. Part of the toolchain.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <tlhelp32.h>
#include <psapi.h>
#include <cstdio>
#include <cstring>

#pragma comment(lib, "psapi.lib")

static DWORD findPid(const wchar_t* namePart) {
    HANDLE snap = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
    if (snap == INVALID_HANDLE_VALUE) return 0;
    PROCESSENTRY32W pe{};
    pe.dwSize = sizeof(pe);
    DWORD pid = 0;
    if (Process32FirstW(snap, &pe)) {
        do {
            if (wcsstr(pe.szExeFile, namePart)) {
                pid = pe.th32ProcessID;
                break;
            }
        } while (Process32NextW(snap, &pe));
    }
    CloseHandle(snap);
    return pid;
}

int wmain() {
    DWORD pid = findPid(L"GhostHunterClientSteam-Win64-Shipping.exe");
    if (!pid) { wprintf(L"game not running\n"); return 1; }
    wprintf(L"game pid: %u\n", pid);

    HANDLE h = OpenProcess(PROCESS_ALL_ACCESS, FALSE, pid);
    if (!h) { wprintf(L"OpenProcess failed: %lu\n", GetLastError()); return 1; }

    // check if already injected (module with our name present)
    HMODULE mods[1024]; DWORD cb = 0;
    if (EnumProcessModules(h, mods, sizeof(mods), &cb)) {
        int n = cb / sizeof(HMODULE);
        for (int i = 0; i < n; i++) {
            wchar_t name[256]{};
            GetModuleFileNameExW(h, mods[i], name, 256);
            if (wcsstr(name, L"xixing.dll")) { wprintf(L"already injected\n"); CloseHandle(h); return 0; }
        }
    }

    const wchar_t* dllPath = L"D:\\gh_tools\\overlay\\build\\xixing.dll";
    size_t bytes = (wcslen(dllPath) + 1) * sizeof(wchar_t);
    void* remote = VirtualAllocEx(h, nullptr, bytes, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    if (!remote) { wprintf(L"VirtualAllocEx failed\n"); CloseHandle(h); return 1; }
    if (!WriteProcessMemory(h, remote, dllPath, bytes, nullptr)) { wprintf(L"WPM failed\n"); CloseHandle(h); return 1; }

    HMODULE k32 = GetModuleHandleW(L"kernel32.dll");
    FARPROC loadLibrary = GetProcAddress(k32, "LoadLibraryW");
    HANDLE t = CreateRemoteThread(h, nullptr, 0, (LPTHREAD_START_ROUTINE)loadLibrary, remote, 0, nullptr);
    if (!t) { wprintf(L"CreateRemoteThread failed: %lu\n", GetLastError()); CloseHandle(h); return 1; }
    WaitForSingleObject(t, 10000);
    CloseHandle(t);
    VirtualFreeEx(h, remote, 0, MEM_RELEASE);
    CloseHandle(h);
    wprintf(L"injected\n");
    return 0;
}
