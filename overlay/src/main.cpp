// GhostHunter container ESP overlay - external memory overlay, DWM-composited layered window.
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <objidl.h>
#include <gdiplus.h>
#include <string>
#include <vector>
#include <cmath>
#include <algorithm>
#include <cstdio>
#include "mem.h"
#include "ue4.h"
#include "xixing.h"

#pragma comment(lib, "gdiplus.lib")
#pragma comment(lib, "user32.lib")
#pragma comment(lib, "gdi32.lib")
#pragma comment(lib, "psapi.lib")

// ---------------- config ----------------
namespace cfg {
    static bool showGold   = true;   // quality 3
    static bool showRed    = true;   // quality 4
    static bool showPurple = false;  // quality 2 (off by default per requirement)
    static bool showBlue   = false;  // quality 1
    static bool showWhite  = false;  // quality 0
    static bool showDistance = true;
    static float maxDist = 12000.f;  // hide beyond this (unreal units)
    static float fontSize = 15.0f;
}

static const wchar_t* GAME_PROC = L"GhostHunterClientSteam-Win64-Shipping.exe";  // game itself, not the launcher

static bool g_running = true;
static Mem mem;
static UE4* ue = nullptr;
static XiXing* xixing = nullptr;
static int g_fps = 0;
static volatile bool g_atkBoost = false;   // F9 pressed
static volatile bool g_invuln   = false;   // F10 pressed (invuln / defense)
static volatile bool g_money    = false;   // F3 pressed (add money)
static volatile bool g_lingbi   = false;   // F4 pressed (add lingbi)
static volatile bool g_soul     = false;   // F5 pressed (add soul)

// quality -> display color
static Gdiplus::Color qualityColor(Quality q) {
    switch (q) {
        case Quality::Red:    return Gdiplus::Color(255, 235, 80, 80);   // red
        case Quality::Gold:   return Gdiplus::Color(255, 245, 200, 90);  // gold
        case Quality::Purple: return Gdiplus::Color(255, 190, 110, 235); // purple
        case Quality::Blue:   return Gdiplus::Color(255, 110, 170, 255); // blue
        default:              return Gdiplus::Color(255, 230, 230, 230); // white
    }
}
static bool qualityEnabled(Quality q) {
    switch (q) {
        case Quality::Red:    return cfg::showRed;
        case Quality::Gold:   return cfg::showGold;
        case Quality::Purple: return cfg::showPurple;
        case Quality::Blue:   return cfg::showBlue;
        default:              return cfg::showWhite;
    }
}

// ---------------- game window tracking ----------------
struct GameWin {
    HWND hwnd = nullptr;
    RECT client{};       // screen coords of client area
};

static BOOL CALLBACK enumWndProc(HWND h, LPARAM lp) {
    DWORD pid = 0; GetWindowThreadProcessId(h, &pid);
    if (pid == mem.procId && IsWindowVisible(h)) {
        wchar_t cls[64] = {};
        GetClassNameW(h, cls, 64);
        // UE main window class is "UnrealWindow"
        if (wcscmp(cls, L"UnrealWindow") == 0) {
            *(HWND*)lp = h;
            return FALSE;
        }
    }
    return TRUE;
}

static GameWin findGameWindow() {
    GameWin gw;
    HWND h = nullptr;
    EnumWindows(enumWndProc, (LPARAM)&h);
    if (h) {
        gw.hwnd = h;
        RECT r; GetClientRect(h, &r);
        POINT p1{0, 0}, p2{r.right, r.bottom};
        ClientToScreen(h, &p1); ClientToScreen(h, &p2);
        gw.client = {p1.x, p1.y, p2.x, p2.y};
    }
    return gw;
}

// ---------------- world to screen (UE rotator, degrees) ----------------
struct Vec3d { double x, y, z; };

static bool w2s(const Vec3d& world, const CameraData& cam, int sw, int sh, float& sx, float& sy) {
    const double DEG = 3.14159265358979323846 / 180.0;
    double pitch = cam.rot[0] * DEG, yaw = cam.rot[1] * DEG, roll = cam.rot[2] * DEG;
    double cp = cos(pitch), sp = sin(pitch);
    double cyaw = cos(yaw), syaw = sin(yaw);
    double cr = cos(roll),  sr = sin(roll);

    Vec3d axisX{ cp * cyaw, cp * syaw, sp };                                            // forward
    Vec3d axisY{ sr * sp * cyaw - cr * syaw, sr * sp * syaw + cr * cyaw, -sr * cp };    // right
    Vec3d axisZ{ -cr * sp * cyaw - sr * syaw, -cr * sp * syaw + sr * cyaw, cr * cp };   // up

    Vec3d d{ world.x - cam.loc[0], world.y - cam.loc[1], world.z - cam.loc[2] };
    double dx = d.x * axisX.x + d.y * axisX.y + d.z * axisX.z;
    double dy = d.x * axisY.x + d.y * axisY.y + d.z * axisY.z;
    double dz = d.x * axisZ.x + d.y * axisZ.y + d.z * axisZ.z;
    if (dx < 1.0) return false;

    double halfFovH = tan(cam.fov * 0.5 * DEG);
    double cx = sw * 0.5, cyv = sh * 0.5;
    double scale = cx / halfFovH;
    sx = (float)(cx + dy / dx * scale);
    sy = (float)(cyv - dz / dx * scale);
    return sx > -200 && sx < sw + 200 && sy > -200 && sy < sh + 200;
}

// ---------------- overlay window ----------------
static HWND g_overlay = nullptr;
static HDC g_screenDC = nullptr;
static HDC g_memDC = nullptr;
static HBITMAP g_dib = nullptr;
static void* g_dibBits = nullptr;
static int g_w = 0, g_h = 0;

static void ensureSurface(int w, int h) {
    if (w == g_w && h == g_h && g_dib) return;
    if (g_dib) { DeleteObject(g_dib); g_dib = nullptr; }
    if (g_memDC) { DeleteDC(g_memDC); g_memDC = nullptr; }
    g_w = w; g_h = h;
    g_memDC = CreateCompatibleDC(g_screenDC);
    BITMAPINFO bi = {};
    bi.bmiHeader.biSize = sizeof(BITMAPINFOHEADER);
    bi.bmiHeader.biWidth = w;
    bi.bmiHeader.biHeight = -h;   // top-down
    bi.bmiHeader.biPlanes = 1;
    bi.bmiHeader.biBitCount = 32;
    bi.bmiHeader.biCompression = BI_RGB;
    g_dib = CreateDIBSection(g_screenDC, &bi, DIB_RGB_COLORS, &g_dibBits, nullptr, 0);
    SelectObject(g_memDC, g_dib);
}

static LRESULT CALLBACK wndProc(HWND h, UINT msg, WPARAM wp, LPARAM lp) {
    switch (msg) {
        case WM_DESTROY: g_running = false; PostQuitMessage(0); return 0;
        case WM_ERASEBKGND: return 1;
    }
    return DefWindowProcW(h, msg, wp, lp);
}

// ---------------- drawing helpers ----------------
static void drawEsp(Gdiplus::Graphics& g, const std::vector<ContainerEsp>& containers,
                    const CameraData& cam, int sw, int sh) {
    using namespace Gdiplus;
    static FontFamily ff(L"Microsoft YaHei");
    static Font font(&ff, cfg::fontSize, FontStyleRegular, UnitPixel);
    StringFormat sfCenter; sfCenter.SetAlignment(StringAlignmentCenter);

    // first pass: project + sort by distance (closest drawn first, wins de-clutter)
    struct ProjMark { const ContainerEsp* c; float sx, sy; double dist; };
    std::vector<ProjMark> marks;
    for (const auto& c : containers) {
        if (!qualityEnabled(c.quality)) continue;
        double dx = c.pos[0] - cam.loc[0], dy = c.pos[1] - cam.loc[1], dz = c.pos[2] - cam.loc[2];
        double dist = sqrt(dx * dx + dy * dy + dz * dz);
        if (dist > cfg::maxDist || dist < 1) continue;
        Vec3d world{ c.pos[0], c.pos[1], c.pos[2] };
        float sx, sy;
        if (!w2s(world, cam, sw, sh, sx, sy)) continue;
        marks.push_back({ &c, sx, sy, dist });
    }
    std::sort(marks.begin(), marks.end(), [](const ProjMark& a, const ProjMark& b) { return a.dist < b.dist; });

    int shown = 0;
    std::vector<POINT> drawnAt;   // visual de-clutter: skip markers piling on the same spot
    for (const auto& m : marks) {
        const ContainerEsp& c = *m.c;
        float sx = m.sx, sy = m.sy;
        double dist = m.dist;

        // skip if too close to an already drawn marker (adjacent ore nodes / stacked boxes)
        bool crowded = false;
        for (const auto& p : drawnAt) {
            if (abs(p.x - (LONG)sx) < 22 && abs(p.y - (LONG)sy) < 22) { crowded = true; break; }
        }
        if (crowded) continue;
        drawnAt.push_back({(LONG)sx, (LONG)sy});
        if (drawnAt.size() > 400) drawnAt.erase(drawnAt.begin());

        Color col = qualityColor(c.quality);

        // marker box sized by distance
        float size = (float)std::max(6.0, 26.0 - dist / 260.0);
        Rect box((int)(sx - size), (int)(sy - size), (int)(size * 2), (int)(size * 2));

        Pen penGlow(Color(160, 0, 0, 0), 4.5f);
        Pen pen(col, 2.0f);
        g.DrawRectangle(&penGlow, box);
        g.DrawRectangle(&pen, box);

        SolidBrush b(col);
        g.FillRectangle(&b, (int)sx - 1, (int)sy - 1, 2, 2);

        // name
        if (!c.name.empty()) {
            wchar_t buf[128];
            if (cfg::showDistance)
                swprintf(buf, 128, L"%s %.0fm", c.name.c_str(), dist / 100.0);
            else
                swprintf(buf, 128, L"%s", c.name.c_str());
            PointF pt(sx, sy - size - cfg::fontSize - 6);
            SolidBrush shadow(Color(200, 0, 0, 0));
            SolidBrush nameBrush(col);
            g.DrawString(buf, -1, &font, PointF(pt.X + 1, pt.Y + 1), &sfCenter, &shadow);
            g.DrawString(buf, -1, &font, pt, &sfCenter, &nameBrush);
        }
        shown++;
    }

    // hud line: bottom-left with translucent plate (avoid the game's own top-left UI)
    wchar_t hud[192];
    swprintf(hud, 192, L"[鬼猎ESP] %d帧 | 地图:%hs  箱:%d 显示:%d  %s%s%s  (F2切换/END退出)",
             g_fps, ue->worldName.c_str(), (int)containers.size(), shown,
             cfg::showGold ? L"金" : L"", cfg::showRed ? L"红" : L"",
             cfg::showPurple ? L"紫" : L"");
    SolidBrush plate(Color(140, 0, 0, 0));
    SolidBrush textHud(Color(255, 170, 255, 170));
    PointF hp(12, (float)sh - 34);
    RectF textRect(4, (float)sh - 40, 980, 30);
    g.FillRectangle(&plate, textRect);
    g.DrawString(hud, -1, &font, hp, &textHud);

    // XiXing status line (above the ESP hud line)
    if (xixing) {
        XxStats& xs = xixing->stats;
        wchar_t xh[192];
        if (!xs.injected) {
            swprintf(xh, 192, L"[管线] 注入中...");
        } else if (!xs.queueAlive) {
            swprintf(xh, 192, L"[管线] 队列等待");
        } else if (xs.running) {
            wchar_t ph[96];
            xs.getPhase(ph, 96);
            swprintf(xh, 192, L"[管线] %s | 箱 %d | 物品 %d",
                     ph, xs.boxesDone, xs.dropsPicked);
        } else {
            swprintf(xh, 192, L"[管线] 就绪 - M4 侧键启动 (先按 F 开一箱锁句柄)");
        }
        Color xc = xs.running ? Color(255, 120, 255, 170) :
                   (xs.injected && xs.queueAlive ? Color(255, 120, 220, 255) : Color(255, 255, 170, 90));
        SolidBrush xb(xc);
        PointF xp(12, (float)sh - 58);
        RectF xRect(4, (float)sh - 64, 980, 30);
        g.FillRectangle(&plate, xRect);
        g.DrawString(xh, -1, &font, xp, &xb);
    }
}

// premultiply the DIB's straight alpha in place (required by UpdateLayeredWindow ULW_ALPHA)
static void premultiply() {
    uint32_t* px32 = (uint32_t*)g_dibBits;
    for (int i = 0; i < g_w * g_h; i++) {
        uint32_t p = px32[i];
        uint32_t a = p >> 24;
        if (a == 0 || a == 255) continue;
        uint32_t b = ((p & 0x000000FF) * a) / 255;
        uint32_t gr = ((p & 0x0000FF00) * a) / 255 & 0x0000FF00;
        uint32_t r = ((p & 0x00FF0000) * a) / 255 & 0x00FF0000;
        px32[i] = (a << 24) | r | gr | b;
    }
}

// ---------------- frame loop ----------------
static void dbglog(const char* msg) {
    FILE* f = nullptr;
    if (fopen_s(&f, "D:\\gh_tools\\overlay\\overlay.log", "a") == 0 && f) {
        fprintf(f, "[%u] %s\n", GetTickCount(), msg);
        fclose(f);
    }
}

static void renderFrame() {
    // 1. attach to the game first (findGameWindow needs the pid)
    static DWORD lastAttach = 0;
    static DWORD lastAliveCheck = 0;
    static bool attachedOnce = false;
    if (!mem.attached()) {
        if (GetTickCount() - lastAttach > 2000) {
            lastAttach = GetTickCount();
            if (mem.attach(GAME_PROC)) {
                ue->init();
                dbglog("attached to game");
                attachedOnce = true;
            } else {
                if (!attachedOnce) dbglog("attach failed (game not found yet)");
            }
        }
    } else if (GetTickCount() - lastAliveCheck > 3000) {
        // DEAD-PROCESS GUARD: a stale handle is non-null but invalid - the
        // attach loop above would never re-run after a game restart. Detect
        // the dead process and detach so the loop reconnects to the new one.
        lastAliveCheck = GetTickCount();
        DWORD code = 0;
        if (GetExitCodeProcess(mem.procHandle, &code) && code != STILL_ACTIVE) {
            // User preference: game closed -> the overlay EXITS (no auto
            // re-attach). Auto-re-injecting into every restarted game was
            // what accumulated stale hook state -> the freeze/crash loop.
            // Clean model: relaunch the overlay by hand after re-entering.
            dbglog("game process died - overlay exiting (relaunch after re-entering)");
            mem.detach();
            g_running = false;
        }
    }

    using namespace Gdiplus;

    if (!mem.attached()) {
        // draw a waiting hint on a fixed-size surface so the window is at least visible
        ensureSurface(600, 200);
        SetWindowPos(g_overlay, HWND_TOPMOST, 40, 40, 600, 200, SWP_NOACTIVATE | SWP_SHOWWINDOW);
        static bool loggedWait = false;
        if (!loggedWait) { dbglog("waiting for game process"); loggedWait = true; }
        // wrap the DIB memory as an ARGB bitmap so GDI+ maintains a real alpha channel
        Bitmap bmp(g_w, g_h, g_w * 4, PixelFormat32bppARGB, (BYTE*)g_dibBits);
        Graphics g(&bmp);
        g.Clear(Color(0, 0, 0, 0));
        FontFamily ff(L"Microsoft YaHei");
        Font f(&ff, 16, FontStyleRegular, UnitPixel);
        SolidBrush waitBrush(Color(255, 255, 120, 120));
        g.DrawString(L"等待游戏进程...", -1, &f, PointF(10, 10), &waitBrush);
        premultiply();
        POINT srcPt{0, 0};
        SIZE size{g_w, g_h};
        BLENDFUNCTION bf{AC_SRC_OVER, 0, 255, AC_SRC_ALPHA};
        UpdateLayeredWindow(g_overlay, g_screenDC, nullptr, &size, g_memDC, &srcPt, 0, &bf, ULW_ALPHA);
        Sleep(200);
        return;
    }

    // 2. locate the game window
    GameWin gw = findGameWindow();
    if (!gw.hwnd) { static bool loggedNoWin = false; if (!loggedNoWin) { dbglog("game window not found"); loggedNoWin = true; } Sleep(200); return; }
    int w = gw.client.right - gw.client.left;
    int h = gw.client.bottom - gw.client.top;
    if (w <= 0 || h <= 0) { Sleep(100); return; }
    {
        static bool loggedWin = false;
        if (!loggedWin) { char b[96]; sprintf_s(b, "game window %dx%d @%d,%d", w, h, gw.client.left, gw.client.top); dbglog(b); loggedWin = true; }
    }

    ensureSurface(w, h);
    SetWindowPos(g_overlay, HWND_TOPMOST,
                 gw.client.left, gw.client.top, w, h,
                 SWP_NOACTIVATE | SWP_SHOWWINDOW);

    // wrap the DIB memory as an ARGB bitmap so GDI+ maintains a real alpha channel.
    // (a DC-based Graphics treats the DIB as 32bppRGB and leaves the alpha bytes garbage,
    //  which makes UpdateLayeredWindow composite random transparency - looks like doubled draws)
    Bitmap bmp(w, h, w * 4, PixelFormat32bppARGB, (BYTE*)g_dibBits);
    Graphics g(&bmp);
    g.SetSmoothingMode(SmoothingModeAntiAlias);
    g.SetTextRenderingHint(TextRenderingHintAntiAlias);
    g.Clear(Color(0, 0, 0, 0));   // fully transparent

    // M5 abort touches no ue state -> safe even mid-pipeline. Handle it first.
    if (GetAsyncKeyState(VK_XBUTTON2) & 1) xixing->abort();

    // Render-thread-PRIVATE snapshot of the last live ESP, published every
    // non-pipeline frame. During an M4 run the render must NOT touch the
    // worker's scan caches (that data race crashed the game -> froze input),
    // so it draws the SNAPSHOT containers (boxes are static in the world)
    // projected with the LIVE camera. getCamera() is pointer-reads-only once
    // the camera is resolved (which it is before M4), so calling it here does
    // NOT mutate any shared cache -> the ESP tracks camera rotation, no race.
    static std::vector<ContainerEsp> espSnap;
    static CameraData camSnap;

    if (xixing->stats.running) {
        CameraData cam = ue->getCamera();
        const CameraData& useCam = cam.valid ? cam : camSnap;
        if (useCam.valid && !espSnap.empty()) {
            drawEsp(g, espSnap, useCam, w, h);   // snapshot boxes + LIVE progress line
        } else {
            wchar_t ph[96]; xixing->stats.getPhase(ph, 96);
            SolidBrush pb(Color(255, 130, 220, 130));
            FontFamily pf(L"Microsoft YaHei");
            Font pfont(&pf, 16, FontStyleRegular, UnitPixel);
            g.DrawString(L"M4 自动交互运行中...", -1, &pfont, PointF(20, 40), &pb);
            if (ph[0]) g.DrawString(ph, -1, &pfont, PointF(20, 66), &pb);
        }
    } else if (ue->pollWorld()) {
        if (GetAsyncKeyState(VK_XBUTTON1) & 1) xixing->trigger(mem, *ue);
        if (!xixing->stats.running) {
            if (g_atkBoost) {
                g_atkBoost = false;
                xixing->fireDevRpc(mem, *ue, "ServeraddATK", 200000);
            }
            if (g_invuln) {
                g_invuln = false;
                xixing->fireDevRpc(mem, *ue, "ServeraddHP", 200000);   // F10 = heal to full
            }
            if (g_money)  { g_money  = false; xixing->fireDevRpc(mem, *ue, "ServerAddMoney",  1000000); }  // F3 +100w
            if (g_lingbi) { g_lingbi = false; xixing->fireDevRpc(mem, *ue, "ServerAddLingBi", 100000); }   // F4 +10w
            if (g_soul)   { g_soul   = false; xixing->fireDevRpc(mem, *ue, "ServerAddSoul",   100000); }   // F5 +10w
            xixing->tick(mem, *ue);
            CameraData cam = ue->getCamera();
            if (cam.valid) {
                const std::vector<ContainerEsp>& containers = ue->containers();
                static bool loggedEsp = false;
                if (!loggedEsp) {
                    char b[96];
                    sprintf_s(b, "camera ok, containers=%d", ue->lastScanCount);
                    dbglog(b);
                    loggedEsp = true;
                }
                espSnap = containers;   // publish for the M4 stand-down path
                camSnap = cam;
                drawEsp(g, containers, cam, w, h);
            } else {
                static bool loggedCam = false;
                if (!loggedCam) { dbglog("world loaded, camera not found"); loggedCam = true; }
                SolidBrush b(Color(255, 255, 200, 120));
                FontFamily ff(L"Microsoft YaHei");
                Font f(&ff, 16, FontStyleRegular, UnitPixel);
                g.DrawString(L"世界已加载，正在等待相机就绪（约10~30秒）...", -1, &f, PointF(20, 40), &b);
            }
        }
    } else {
        static bool loggedLobby = false;
        if (!loggedLobby) { dbglog("no world (lobby)"); loggedLobby = true; }
        SolidBrush b(Color(255, 255, 200, 120));
        FontFamily ff(L"Microsoft YaHei");
        Font f(&ff, 16, FontStyleRegular, UnitPixel);
        g.DrawString(L"大厅中 / 无对局...", -1, &f, PointF(20, 40), &b);
    }

    // premultiply alpha into the DIB (GDI+ wrote straight alpha)
    premultiply();

    POINT srcPt{0, 0};
    SIZE size{w, h};
    BLENDFUNCTION bf{AC_SRC_OVER, 0, 255, AC_SRC_ALPHA};
    UpdateLayeredWindow(g_overlay, g_screenDC, nullptr, &size, g_memDC, &srcPt, 0, &bf, ULW_ALPHA);

    // fps counter
    static DWORD fpsTick = GetTickCount();
    static int fpsFrames = 0;
    static int fpsShown = 0;
    fpsFrames++;
    DWORD now = GetTickCount();
    if (now - fpsTick >= 500) {
        fpsShown = fpsFrames * 1000 / (now - fpsTick);
        fpsFrames = 0;
        fpsTick = now;
    }
    g_fps = fpsShown;
}

// hotkey thread: F2 cycle filters, F6 XiXing, F8 capture, END quit
static DWORD WINAPI hotkeyThread(LPVOID) {
    int mode = 0;  // 0: gold+red  1: gold+red+purple  2: all  3: red only
    while (g_running) {
        if (GetAsyncKeyState(VK_END) & 1) { g_running = false; break; }
        if (GetAsyncKeyState(VK_F2) & 1) {
            mode = (mode + 1) % 4;
            switch (mode) {
                case 0: cfg::showGold = true;  cfg::showRed = true;  cfg::showPurple = false; cfg::showBlue = false; cfg::showWhite = false; break;
                case 1: cfg::showGold = true;  cfg::showRed = true;  cfg::showPurple = true;  cfg::showBlue = false; cfg::showWhite = false; break;
                case 2: cfg::showGold = true;  cfg::showRed = true;  cfg::showPurple = true;  cfg::showBlue = true;  cfg::showWhite = true;  break;
                case 3: cfg::showGold = false; cfg::showRed = true;  cfg::showPurple = false; cfg::showBlue = false; cfg::showWhite = false; break;
            }
        }
        // F9 = one-shot damage boost (ServeraddATK via the game-thread queue)
        if (GetAsyncKeyState(VK_F9) & 1) g_atkBoost = true;
        // F10 = heal to full (ServeraddHP - same NetServer family as F9's ServeraddATK)
        if (GetAsyncKeyState(VK_F10) & 1) g_invuln = true;
        if (GetAsyncKeyState(VK_F3) & 1) g_money  = true;   // F3 = +100w money
        if (GetAsyncKeyState(VK_F4) & 1) g_lingbi = true;   // F4 = +10w lingbi
        if (GetAsyncKeyState(VK_F5) & 1) g_soul   = true;   // F5 = +10w soul
        Sleep(30);
    }
    return 0;
}

int WINAPI wWinMain(HINSTANCE hInst, HINSTANCE, PWSTR, int) {
    // single instance guard: a second copy silently exits
    HANDLE mutex = CreateMutexW(nullptr, TRUE, L"GH_ESP_OVERLAY_SINGLETON");
    if (mutex && GetLastError() == ERROR_ALREADY_EXISTS) {
        return 0;
    }

    WNDCLASSW wc = {};
    wc.lpfnWndProc = wndProc;
    wc.hInstance = hInst;
    wc.lpszClassName = L"GH_ESP_OVERLAY";
    RegisterClassW(&wc);

    g_overlay = CreateWindowExW(
        WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOPMOST | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW,
        wc.lpszClassName, L"GH ESP",
        WS_POPUP,
        100, 100, 800, 600,
        nullptr, nullptr, hInst, nullptr);
    ShowWindow(g_overlay, SW_SHOWNOACTIVATE);

    g_screenDC = GetDC(nullptr);

    Gdiplus::GdiplusStartupInput gdiplusStartup;
    ULONG_PTR gdiplusToken;
    Gdiplus::GdiplusStartup(&gdiplusToken, &gdiplusStartup, nullptr);

    static UE4 ueInst(mem);
    ue = &ueInst;
    static XiXing xxInst;
    xixing = &xxInst;

    // hotkey thread: F2 cycle filters, END quit
    CreateThread(nullptr, 0, hotkeyThread, nullptr, 0, nullptr);

    // frame loop
    while (g_running) {
        MSG msg;
        while (PeekMessageW(&msg, nullptr, 0, 0, PM_REMOVE)) {
            if (msg.message == WM_QUIT) g_running = false;
            TranslateMessage(&msg);
            DispatchMessageW(&msg);
        }
        renderFrame();
        Sleep(1);   // high frame rate target (~200hz); message pump + render dominate
    }

    Gdiplus::GdiplusShutdown(gdiplusToken);
    ReleaseDC(nullptr, g_screenDC);
    if (g_dib) DeleteObject(g_dib);
    if (g_memDC) DeleteDC(g_memDC);
    DestroyWindow(g_overlay);
    mem.detach();
    return 0;
}
