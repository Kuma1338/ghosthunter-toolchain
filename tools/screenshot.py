import os
r"""Screenshot helper: capture primary screen to overlay/screen.png"""
import ctypes
from ctypes import wintypes
from PIL import Image

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

w = user32.GetSystemMetrics(0)
h = user32.GetSystemMetrics(1)
hdc = user32.GetDC(0)
memdc = gdi32.CreateCompatibleDC(hdc)
bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
gdi32.SelectObject(memdc, bmp)
gdi32.BitBlt(memdc, 0, 0, w, h, hdc, 0, 0, 0x00CC0020)

class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", ctypes.c_uint), ("biWidth", ctypes.c_int), ("biHeight", ctypes.c_int),
                ("biPlanes", ctypes.c_ushort), ("biBitCount", ctypes.c_ushort), ("biCompression", ctypes.c_uint),
                ("biSizeImage", ctypes.c_uint), ("biXPelsPerMeter", ctypes.c_int), ("biYPelsPerMeter", ctypes.c_int),
                ("biClrUsed", ctypes.c_uint), ("biClrImportant", ctypes.c_uint)]
bi = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
buf = ctypes.create_string_buffer(w * h * 4)
gdi32.GetDIBits(memdc, bmp, 0, h, buf, ctypes.byref(bi), 0)

img = Image.frombytes('RGBA', (w, h), buf.raw, 'raw', 'BGRA', 0, 1)
img.save(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'overlay', 'screen.png'))
print(f"captured {w}x{h}")

user32.ReleaseDC(0, hdc)
gdi32.DeleteObject(bmp)
gdi32.DeleteDC(memdc)
