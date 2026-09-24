r"""Direct DLL injector with explicit pid (avoids zombie-process matches)."""
import ctypes
import ctypes.wintypes as wt
import os
import sys

k32 = ctypes.WinDLL("kernel32")
k32.OpenProcess.restype = wt.HANDLE
k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
k32.VirtualAllocEx.restype = wt.LPVOID
k32.VirtualAllocEx.argtypes = [wt.HANDLE, wt.LPVOID, ctypes.c_size_t, wt.DWORD, wt.DWORD]
k32.WriteProcessMemory.restype = wt.BOOL
k32.WriteProcessMemory.argtypes = [wt.HANDLE, wt.LPVOID, wt.LPCVOID, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
k32.CreateRemoteThread.restype = wt.HANDLE
k32.CreateRemoteThread.argtypes = [wt.HANDLE, wt.LPVOID, ctypes.c_size_t, wt.LPVOID, wt.LPVOID, wt.DWORD, wt.LPVOID]

pid = int(sys.argv[1])
dll_path = sys.argv[2] if len(sys.argv) > 2 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "overlay", "build", "xixing8.dll")

h = k32.OpenProcess(0x1F0FFF, False, pid)
assert h, f"open {pid} failed"
buf = ctypes.create_unicode_buffer(dll_path)
n = ctypes.sizeof(buf)
remote = k32.VirtualAllocEx(h, None, n, 0x3000, 0x04)
assert remote, "alloc failed"
w = ctypes.c_size_t()
assert k32.WriteProcessMemory(h, remote, buf, n, ctypes.byref(w)), "write failed"
k32.GetProcAddress.restype = ctypes.c_void_p
k32.GetProcAddress.argtypes = [wt.HMODULE, ctypes.c_char_p]
k32.GetModuleHandleW.restype = wt.HMODULE
k32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
ll = k32.GetProcAddress(k32.GetModuleHandleW("kernel32.dll"), b"LoadLibraryW")
assert ll, "GetProcAddress failed"
print(f"LoadLibraryW @ {ll:X}")
t = k32.CreateRemoteThread(h, None, 0, ll, remote, 0, None)
assert t, "thread failed"
ctypes.windll.kernel32.WaitForSingleObject(t, 10000)
exit_code = wt.DWORD()
ctypes.windll.kernel32.GetExitCodeThread(t, ctypes.byref(exit_code))
print(f"inject target={pid} dll={dll_path}")
print(f"LoadLibraryW remote return = {exit_code.value:#x} " + ("(SUCCESS)" if exit_code.value else "(FAILED - NULL)"))
