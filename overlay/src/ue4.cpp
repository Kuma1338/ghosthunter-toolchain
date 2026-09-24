#include <windows.h>
// portable: log next to this module
static const char* logPathPortable(const wchar_t* fname) {
    static char buf[MAX_PATH] = {};
    if (!buf[0]) {
        wchar_t w[MAX_PATH]; GetModuleFileNameW(nullptr, w, MAX_PATH);
        wchar_t* slash = wcsrchr(w, L'\\');
        if (slash) wcscpy_s(slash + 1, MAX_PATH - (slash + 1 - w), fname);
        WideCharToMultiByte(CP_UTF8, 0, w, -1, buf, MAX_PATH, 0, 0);
    }
    return buf;
}
#include "ue4.h"
#include <cmath>
#include <cstring>
#include <algorithm>

// ---------------- name -> quality table ----------------
// User-confirmed in-game observations. TB containers show a fixed name per model;
// the quality belongs to the NAME. Update as more names get identified.
struct NameQuality { const wchar_t* name; Quality q; };
static const NameQuality gNameQuality[] = {
    // purple (confirmed)
    { L"朱漆立柜",   Quality::Purple },
    { L"雕花木箱",   Quality::Purple },
    { L"压花皮箱",   Quality::Purple },
    // gold (confirmed)
    { L"金丝楠木箱柜", Quality::Gold },
    { L"鎏金兽首百宝箱", Quality::Gold },
    { L"紫漆牡丹箱", Quality::Gold },
    // red (confirmed)
    { L"花梨木龙纹箱", Quality::Red },
    { L"赤血龙木柜", Quality::Red },
    // white (confirmed)
    { L"老榆木箱",   Quality::White },
};

Quality UE4::qualityForName(const std::wstring& name) {
    if (name.empty()) return Quality::Unknown;
    for (const auto& nq : gNameQuality) {
        if (name == nq.name) return nq.q;
    }
    return Quality::White;   // unknown names: assume low tier (hidden by gold/red filter)
}

// ---------------- FName pool (custom header: (len<<6)|(hash<<1)|wide) ----------------
std::string UE4::fname(uint32_t idx) {
    if (!gnames) return {};
    uint32_t block = idx >> 16;
    uint32_t offset = (idx & 0xFFFF) * 2;
    uintptr_t blk = mem.readPtr(gnames + 0x10 + (uint64_t)block * 8);
    if (!blk) return {};
    uint16_t hdr = 0;
    if (!mem.readT(blk + offset, hdr)) return {};
    bool wide = hdr & 1;
    uint32_t len = hdr >> 6;
    if (len == 0 || len > 250) return {};
    std::string out(len, '\0');
    if (wide) {
        std::vector<char> raw(len * 2);
        if (!mem.read(blk + offset + 2, raw.data(), raw.size())) return {};
        int n = 0;
        for (uint32_t i = 0; i + 1 < raw.size(); i += 2) {
            unsigned ch = (unsigned char)raw[i] | ((unsigned char)raw[i + 1] << 8);
            if (ch < 0x80) out[n++] = (char)ch;
            else if (ch < 0x800) { out[n++] = (char)(0xC0 | (ch >> 6)); out[n++] = (char)(0x80 | (ch & 0x3F)); }
            else { out[n++] = (char)(0xE0 | (ch >> 12)); out[n++] = (char)(0x80 | ((ch >> 6) & 0x3F)); out[n++] = (char)(0x80 | (ch & 0x3F)); }
        }
        out.resize(n);
    } else {
        if (!mem.read(blk + offset + 2, out.data(), len)) return {};
    }
    return out;
}

bool UE4::init() {
    gnames = mem.moduleBase + offs::GNAMES;
    std::string n0 = fname(0);
    if (n0 != "None") return false;
    nameCache.clear();
    qualityCache.clear();
    cached.clear();
    cameraOffsetsResolved = false;
    cameraManager = 0;
    lastScanTick = 0;
    return true;
}

// ---------------- reflection ----------------
uintptr_t UE4::findField(uintptr_t cls, const char* fieldName) {
    if (!cls) return 0;
    for (int depth = 0; cls && depth < 8; depth++) {
        uintptr_t pc = mem.readPtr(cls + offs::StructChildProperties);
        for (int i = 0; pc && i < 200; i++) {
            uint32_t nidx = mem.readU32(pc + offs::FieldName);
            if (fname(nidx) == fieldName) return pc;
            pc = mem.readPtr(pc + offs::FieldNext);
        }
        cls = mem.readPtr(cls + offs::StructSuperStruct);
    }
    return 0;
}

uintptr_t UE4::findFieldInStruct(uintptr_t scriptStruct, const char* fieldName) {
    if (!scriptStruct) return 0;
    uintptr_t pc = mem.readPtr(scriptStruct + offs::StructChildProperties);
    for (int i = 0; pc && i < 200; i++) {
        uint32_t nidx = mem.readU32(pc + offs::FieldName);
        if (fname(nidx) == fieldName) return pc;
        pc = mem.readPtr(pc + offs::FieldNext);
    }
    return 0;
}

// ---------------- world / levels ----------------
static void ueLog(const char* msg) {
    FILE* f = nullptr;
    if (fopen_s(&f, logPathPortable(L"overlay.log"), "a") == 0 && f) {
        fprintf(f, "[%u] %s\n", GetTickCount(), msg);
        fclose(f);
    }
}

bool UE4::pollWorld() {
    uintptr_t base = mem.moduleBase;
    uintptr_t newWorld = mem.readPtr(base + offs::GWORLD);
    if (!newWorld || mem.readPtr(newWorld + offs::WorldPersistentLevel) < 0x10000) {
        uintptr_t alt = mem.readPtr(base + offs::GWORLD2);
        if (alt > 0x10000 && mem.readPtr(alt + offs::WorldPersistentLevel) > 0x10000) newWorld = alt;
    }
    if (!newWorld || newWorld < 0x10000) {
        world = 0; levels.clear(); worldName.clear(); cameraManager = 0;
        return false;
    }
    if (newWorld != world) {
        // world recreated (map reload / re-entry): drop ALL per-world caches
        world = newWorld;
        cameraManager = 0;
        cameraOffsetsResolved = false;
        nameCache.clear();
        qualityCache.clear();
        cached.clear();
        cachedDrops.clear();
        lastScanTick = 0;
        lastLevelTick = 0;
        lastDropTick = 0;
        clsTB[0] = clsTB[1] = clsTB[2] = 0;
        clsTool[0] = clsTool[1] = 0;
        pawn = 0; pawnCls = 0; offPCPawn = 0;
        funcCache.clear();
        worldName = fname(mem.readU32(world + offs::ObjName));
        {
            char b[96];
            sprintf_s(b, "world changed: %p %s", (void*)world, worldName.c_str());
            ueLog(b);
        }
        refreshLevels();
    } else if (worldName.empty()) {
        worldName = fname(mem.readU32(world + offs::ObjName));
    }
    // levels change rarely (streaming); refresh at 1s cadence, not per frame
    DWORD now = GetTickCount();
    if (now - lastLevelTick > 1000) {
        lastLevelTick = now;
        refreshLevels();
    }
    return true;
}

void UE4::refreshLevels() {
    levels.clear();
    if (uintptr_t pl = mem.readPtr(world + offs::WorldPersistentLevel)) levels.push_back(pl);
    uintptr_t arr = mem.readPtr(world + offs::WorldStreamingLevels);
    uint32_t num = 0;
    mem.readT(world + offs::WorldStreamingLevels + 8, num);
    if (arr && num && num < 500) {
        std::vector<uintptr_t> entries(num);
        if (mem.read(arr, entries.data(), num * 8)) {
            for (uintptr_t lvs : entries) {
                if (lvs < 0x10000) continue;
                uintptr_t loaded = mem.readPtr(lvs + offs::StreamingLoadedLevel);
                if (loaded > 0x10000) levels.push_back(loaded);
            }
        }
    }
}

// ---------------- text ----------------
std::wstring UE4::readFText(uintptr_t actor, uintptr_t fieldOff) {
    uintptr_t td = mem.readPtr(actor + fieldOff);
    if (td < 0x10000) return {};
    uintptr_t sp = mem.readPtr(td + offs::FTextStringPtr);
    if (sp < 0x10000) return {};
    wchar_t buf[33] = {};
    if (!mem.read(sp, buf, sizeof(buf) - sizeof(wchar_t))) return {};
    buf[32] = 0;
    return std::wstring(buf);
}

// TEXT TMap element: {FName key(8), FText value(16), next/pad(8)} stride 0x20
std::wstring UE4::readTextMapEntry(uintptr_t com, int elementIndex) {
    uintptr_t arr = mem.readPtr(com + offs::ComTextMap);
    if (arr < 0x10000) return {};
    uintptr_t td = mem.readPtr(arr + elementIndex * 0x20 + 8);
    if (td < 0x10000) return {};
    uintptr_t sp = mem.readPtr(td + offs::FTextStringPtr);
    if (sp < 0x10000) return {};
    wchar_t buf[33] = {};
    if (!mem.read(sp, buf, sizeof(buf) - sizeof(wchar_t))) return {};
    buf[32] = 0;
    return std::wstring(buf);
}

// ---------------- camera ----------------
bool UE4::resolveCameraOffsets(uintptr_t pc, uintptr_t pcm) {
    uintptr_t pcClass = mem.readPtr(pc + offs::ObjClass);
    uintptr_t f = findField(pcClass, "PlayerCameraManager");
    if (!f) return false;
    offPlayerCameraManager = mem.readU32(f + offs::FieldOffsetInternal);

    uintptr_t pcmClass = mem.readPtr(pcm + offs::ObjClass);
    f = findField(pcmClass, "CameraCachePrivate");
    if (!f) return false;
    offCameraCachePrivate = mem.readU32(f + offs::FieldOffsetInternal);
    uintptr_t cacheStruct = mem.readPtr(f + offs::StructPropStruct);
    f = findFieldInStruct(cacheStruct, "POV");
    if (!f) return false;
    offCachePOV = mem.readU32(f + offs::FieldOffsetInternal);
    uintptr_t povStruct = mem.readPtr(f + offs::StructPropStruct);
    f = findFieldInStruct(povStruct, "Location");
    if (!f) return false;
    offPOVLocation = mem.readU32(f + offs::FieldOffsetInternal);
    povLocStep = mem.readU32(f + 0x34) == 24 ? 8 : 4;
    f = findFieldInStruct(povStruct, "Rotation");
    if (!f) return false;
    offPOVRotation = mem.readU32(f + offs::FieldOffsetInternal);
    povRotStep = mem.readU32(f + 0x34) == 24 ? 8 : 4;
    f = findFieldInStruct(povStruct, "FOV");
    if (!f) return false;
    offPOVFOV = mem.readU32(f + offs::FieldOffsetInternal);
    cameraOffsetsResolved = true;
    return true;
}

CameraData UE4::getCamera() {
    CameraData cam;
    // throttled failure-point telemetry: which step of the resolution fails
    // (persistent "camera not found" was undiagnosable from outside)
    #define CAMDIAG(why) do { \
        static DWORD lastDiag_ = 0; \
        if (GetTickCount() - lastDiag_ > 5000) { \
            lastDiag_ = GetTickCount(); \
            char db_[96]; \
            sprintf_s(db_, "camdiag: %s", why); \
            ueLog(db_); \
        } \
        return cam; \
    } while (0)

    if (!world) CAMDIAG("no world");
    if (!cameraManager) {
        // World -> GameInstance -> LocalPlayers[0] -> PlayerController -> PlayerCameraManager
        uintptr_t gi = mem.readPtr(world + offs::WorldGameInstance);
        if (gi < 0x10000) CAMDIAG("no GI");
        uintptr_t f = findField(mem.readPtr(gi + offs::ObjClass), "LocalPlayers");
        if (!f) CAMDIAG("no LocalPlayers field");
        uintptr_t lpOff = mem.readU32(f + offs::FieldOffsetInternal);
        uintptr_t lp = mem.readPtr(mem.readPtr(gi + lpOff));
        if (lp < 0x10000) CAMDIAG("no LP0");
        f = findField(mem.readPtr(lp + offs::ObjClass), "PlayerController");
        if (!f) CAMDIAG("no PlayerController field");
        uintptr_t pc = mem.readPtr(lp + mem.readU32(f + offs::FieldOffsetInternal));
        if (pc < 0x10000) CAMDIAG("no PC");
        if (!cameraOffsetsResolved) {
            uintptr_t fPcm = findField(mem.readPtr(pc + offs::ObjClass), "PlayerCameraManager");
            if (!fPcm) CAMDIAG("no PCM field");
            offPlayerCameraManager = mem.readU32(fPcm + offs::FieldOffsetInternal);
            uintptr_t pcm0 = mem.readPtr(pc + offPlayerCameraManager);
            if (pcm0 < 0x10000) CAMDIAG("no PCM0");
            if (!resolveCameraOffsets(pc, pcm0)) CAMDIAG("resolveCameraOffsets failed");
        }
        uintptr_t pcm = mem.readPtr(pc + offPlayerCameraManager);
        if (pcm > 0x10000) cameraManager = pcm;
        if (!cameraManager) CAMDIAG("cameraManager unset");
    }
    uintptr_t pov = cameraManager + offCameraCachePrivate + offCachePOV;
    if (povLocStep == 8) {
        double l[3], rr[3];
        if (!mem.read(pov + offPOVLocation, l, sizeof(l))) return cam;
        if (!mem.read(pov + offPOVRotation, rr, sizeof(rr))) return cam;
        cam.loc[0] = l[0]; cam.loc[1] = l[1]; cam.loc[2] = l[2];
        cam.rot[0] = (float)rr[0]; cam.rot[1] = (float)rr[1]; cam.rot[2] = (float)rr[2];
    } else {
        float l[3], rr[3];
        if (!mem.read(pov + offPOVLocation, l, sizeof(l))) return cam;
        if (!mem.read(pov + offPOVRotation, rr, sizeof(rr))) return cam;
        cam.loc[0] = l[0]; cam.loc[1] = l[1]; cam.loc[2] = l[2];
        cam.rot[0] = rr[0]; cam.rot[1] = rr[1]; cam.rot[2] = rr[2];
    }
    mem.readT(pov + offPOVFOV, cam.fov);
    if (cam.fov < 20 || cam.fov > 170) cam.fov = 90;
    // NaN/garbage guard: a stale camera manager reads NaN; NaN is truthy, so check finiteness
    bool finite = std::isfinite(cam.loc[0]) && std::isfinite(cam.loc[1]) && std::isfinite(cam.loc[2])
               && std::isfinite((double)cam.rot[0]) && std::isfinite((double)cam.rot[1]) && std::isfinite((double)cam.rot[2]);
    static DWORD badCamSince = 0;
    if (!finite) {
        if (!badCamSince) badCamSince = GetTickCount();
        if (GetTickCount() - badCamSince > 500) {   // stale for 0.5s -> drop and re-resolve
            cameraManager = 0;
            cameraOffsetsResolved = false;
            badCamSince = 0;
        }
        CAMDIAG("POV not finite (NaN) - recovering");
    }
    badCamSince = 0;
    cam.valid = cam.loc[0] != 0 || cam.loc[1] != 0 || cam.loc[2] != 0;
    // stale-camera guard #2: a FREED camera manager can read FINITE garbage
    // (the NaN check above never triggers then, and every box projects off-
    // screen = "ESP lost" with a healthy-looking HUD). Sanity: the POV must
    // stay near the pawn. 50m covers all normal gameplay camera distances.
    if (cam.valid) {
        uintptr_t p = getPawn();
        double ppos[3];
        if (p && readActorPos(p, ppos)) {
            double dx = cam.loc[0] - ppos[0], dy = cam.loc[1] - ppos[1], dz = cam.loc[2] - ppos[2];
            if (dx * dx + dy * dy + dz * dz > 2500.0 * 2500.0) {   // > 50m away
                cam.valid = false;
                if (!badCamSince) badCamSince = GetTickCount();
                if (GetTickCount() - badCamSince > 500) {
                    cameraManager = 0;             // stale PCM - drop and re-resolve
                    cameraOffsetsResolved = false;
                    badCamSince = 0;
                }
                {
                    static DWORD lastCamDbg = 0;
                    if (GetTickCount() - lastCamDbg > 3000) {
                        lastCamDbg = GetTickCount();
                        char db[224];
                        sprintf_s(db, "camdbg: pcm=%llX occ=%X opov=%X step=%d cam=(%.0f,%.0f,%.0f) pawn=%llX ppos=(%.0f,%.0f,%.0f) dist=%.0f",
                                  (unsigned long long)cameraManager, offCameraCachePrivate, offCachePOV, povLocStep,
                                  cam.loc[0], cam.loc[1], cam.loc[2], (unsigned long long)p,
                                  ppos[0], ppos[1], ppos[2], sqrt(dx*dx + dy*dy + dz*dz));
                        ueLog(db);
                    }
                }
                CAMDIAG("POV >50m from pawn - stale PCM guard");
            }
        }
    }
    return cam;
}

// ---------------- containers ----------------
uintptr_t UE4::findClassByName(const char* name) {
    for (uintptr_t level : levels) {
        uintptr_t arr = mem.readPtr(level + offs::LevelActors);
        uint32_t num = 0;
        mem.readT(level + offs::LevelActors + 8, num);
        if (!arr || !num || num > 200000) continue;
        std::vector<uintptr_t> actors(num);
        if (!mem.read(arr, actors.data(), num * 8)) continue;
        for (uintptr_t a : actors) {
            if (a < 0x10000) continue;
            uintptr_t cls = mem.readPtr(a + offs::ObjClass);
            if (!cls) continue;
            if (fname(mem.readU32(cls + offs::ObjName)) == name) return cls;
        }
    }
    return 0;
}

static bool readBoundsOrigin(const Mem& mem, uintptr_t actor, double out[3]) {
    uintptr_t root = mem.readPtr(actor + offs::ActorRoot);
    if (root < 0x10000) return false;
    double v[3];
    if (!mem.read(root + offs::SceneBoundsOrigin, v, sizeof(v))) return false;
    out[0] = v[0]; out[1] = v[1]; out[2] = v[2];
    return true;
}

const std::vector<ContainerEsp>& UE4::containers() {
    if (!world) { cached.clear(); return cached; }
    DWORD now = GetTickCount();
    if (now - lastScanTick > 2000) {
        lastScanTick = now;
        fullScan();
    }
    return cached;
}

void UE4::fullScan() {
    cached.clear();

    // resolve container classes (per-world)
    if (!clsTB[0]) {
        clsTB[0] = findClassByName("BP_YiGui_C");
        clsTB[1] = findClassByName("BP_HeZi_C");
        clsTB[2] = findClassByName("BP_BaoXiang_C");
        clsTool[0] = findClassByName("BP_Mining_C");
        clsTool[1] = findClassByName("BP_Collect_C");
        clsDrop = findClassByName("BP_DropInteract_C");
    }

    for (uintptr_t level : levels) {
        uintptr_t arr = mem.readPtr(level + offs::LevelActors);
        uint32_t num = 0;
        mem.readT(level + offs::LevelActors + 8, num);
        if (!arr || !num || num > 200000) continue;
        std::vector<uintptr_t> actors(num);
        if (!mem.read(arr, actors.data(), num * 8)) continue;

        for (uintptr_t a : actors) {
            if (a < 0x10000) continue;
            uintptr_t cls = mem.readPtr(a + offs::ObjClass);
            if (!cls) continue;

            bool isTB = cls == clsTB[0] || cls == clsTB[1] || cls == clsTB[2];
            bool isTool = cls == clsTool[0] || cls == clsTool[1];
            if (!isTB && !isTool) continue;   // drops excluded: they glow on their own

            ContainerEsp c;
            c.actor = a;
            c.isTool = isTool;

            if (isTB) {
                // looted containers: state 3
                uint8_t state = 0;
                if (mem.readT(a + offs::TBState, state) && state == 3) continue;
                auto it = nameCache.find(a);
                if (it != nameCache.end()) c.name = it->second;
                else {
                    c.name = readFText(a, offs::TBNameText);
                    if (c.name.empty()) continue;   // not ready yet - retry next scan (don't cache)
                    nameCache[a] = c.name;
                }
                c.quality = qualityForName(c.name);
            } else {
                // mining / collect: TEXT map element k <-> palette quality k-1
                // (element1=白 青辉杂石矿, element2=蓝 翠晶簇生矿, element3=紫 幻彩幽髓晶矿,
                //  element4=金 鎏金玄髓精矿, element5=红 血髓玄魄神矿) - user confirmed
                int si = 0;
                if (!mem.readT(a + offs::ToolSpawnIndex, si)) continue;
                if (si < 1 || si > 5) continue;
                c.quality = (Quality)(si - 1);
                auto it = nameCache.find(a);
                if (it != nameCache.end()) c.name = it->second;
                else {
                    uintptr_t com = mem.readPtr(a + offs::ToolCom);
                    if (com < 0x10000) continue;
                    c.name = readTextMapEntry(com, si);
                    if (c.name.empty()) continue;   // not ready yet - retry next scan (don't cache)
                    nameCache[a] = c.name;
                }
            }
            if (!readBoundsOrigin(mem, a, c.pos)) continue;
            cached.push_back(std::move(c));
        }
    }
    lastScanCount = (int)cached.size();
}

// ---------------- XiXing: pawn / UFunction / drops ----------------

static bool ptrSane(uintptr_t p) { return p > 0x10000 && p < 0x7FFFFFFFFFFF; }

bool UE4::readActorPos(uintptr_t actor, double pos[3]) {
    if (!ptrSane(actor)) return false;
    return readBoundsOrigin(mem, actor, pos);
}

uintptr_t UE4::getPawn() {
    if (!world) return 0;
    // cached reflection offsets are per-world stable; pointers re-read each call
    uintptr_t gi = mem.readPtr(world + offs::WorldGameInstance);
    if (!ptrSane(gi)) return 0;
    uintptr_t f = findField(mem.readPtr(gi + offs::ObjClass), "LocalPlayers");
    if (!f) return 0;
    uintptr_t lpOff = mem.readU32(f + offs::FieldOffsetInternal);
    uintptr_t lpArr = mem.readPtr(gi + lpOff);
    if (!ptrSane(lpArr)) return 0;
    uintptr_t lp0 = mem.readPtr(lpArr);
    if (!ptrSane(lp0)) return 0;
    uintptr_t fpc = findField(mem.readPtr(lp0 + offs::ObjClass), "PlayerController");
    if (!fpc) return 0;
    uintptr_t pc = mem.readPtr(lp0 + mem.readU32(fpc + offs::FieldOffsetInternal));
    if (!ptrSane(pc)) return 0;
    if (!offPCPawn) {
        uintptr_t fp = findField(mem.readPtr(pc + offs::ObjClass), "Pawn");
        if (!fp) {
            fp = findField(mem.readPtr(pc + offs::ObjClass), "AcknowledgedPawn");
            if (!fp) return 0;
        }
        offPCPawn = mem.readU32(fp + offs::FieldOffsetInternal);
    }
    uintptr_t p = mem.readPtr(pc + offPCPawn);
    if (!ptrSane(p) || !ptrSane(mem.readPtr(p + offs::ObjClass))) return 0;
    pawn = p;
    return pawn;
}

uintptr_t UE4::pawnClass() {
    uintptr_t p = getPawn();
    if (!p) { pawnCls = 0; return 0; }
    pawnCls = mem.readPtr(p + offs::ObjClass);
    return ptrSane(pawnCls) ? pawnCls : 0;
}

// UFunction lookup: walk the class chain; per class walk Children (UField chain,
// Next @ +0x28 - verified live). All UFunctions share one meta UClass named
// "Function"; we establish its pointer once per world and then pointer-compare.
uintptr_t UE4::findFunction(uintptr_t cls, const char* fn) {
    if (!ptrSane(cls) || !fn) return 0;
    auto cit = funcCache.find(cls);
    if (cit != funcCache.end()) {
        auto fit = cit->second.find(fn);
        if (fit != cit->second.end()) return fit->second;   // 0 = known-missing
    }
    static uintptr_t funcMetaClass = 0;   // UClass* named "Function" (per world; funcCache clear signals reset)
    if (!funcCache.size()) funcMetaClass = 0;   // world changed -> re-establish
    uintptr_t result = 0;
    uintptr_t c = cls;
    for (int depth = 0; ptrSane(c) && depth < 16; depth++) {
        uintptr_t ch = mem.readPtr(c + offs::StructChildren);
        for (int i = 0; ptrSane(ch) && i < 600; i++) {
            uintptr_t chCls = mem.readPtr(ch + offs::ObjClass);
            if (!ptrSane(chCls)) break;
            if (funcMetaClass == 0) {
                if (fname(mem.readU32(chCls + offs::ObjName)) == "Function") funcMetaClass = chCls;
            }
            if (chCls == funcMetaClass) {
                std::string nm = fname(mem.readU32(ch + offs::ObjName));
                if (nm == fn) { result = ch; break; }
            }
            ch = mem.readPtr(ch + offs::UFieldNext);
        }
        if (result) break;
        c = mem.readPtr(c + offs::StructSuperStruct);
    }
    funcCache[cls][fn] = result;
    return result;
}

std::string UE4::funcNameOf(uintptr_t ufunc) {
    if (!ptrSane(ufunc)) return {};
    return fname(mem.readU32(ufunc + offs::ObjName));
}

std::string UE4::classNameOf(uintptr_t obj) {
    if (!ptrSane(obj)) return {};
    uintptr_t cls = mem.readPtr(obj + offs::ObjClass);
    if (!ptrSane(cls)) return {};
    return fname(mem.readU32(cls + offs::ObjName));
}

// fast drop scan (~400ms cadence): position + picked flag only
const std::vector<DropEsp>& UE4::drops() {
    DWORD now = GetTickCount();
    if (now - lastDropTick < 400) return cachedDrops;
    lastDropTick = now;
    cachedDrops.clear();
    if (!world) return cachedDrops;
    if (!clsDrop) clsDrop = findClassByName("BP_DropInteract_C");
    if (!clsDrop) return cachedDrops;
    for (uintptr_t level : levels) {
        uintptr_t arr = mem.readPtr(level + offs::LevelActors);
        uint32_t num = 0;
        mem.readT(level + offs::LevelActors + 8, num);
        if (!arr || !num || num > 200000) continue;
        std::vector<uintptr_t> actors(num);
        if (!mem.read(arr, actors.data(), num * 8)) continue;
        for (uintptr_t a : actors) {
            if (a < 0x10000) continue;
            if (mem.readPtr(a + offs::ObjClass) != clsDrop) continue;
            DropEsp d;
            d.actor = a;
            uint8_t picked = 0;
            mem.readT(a + offs::DropPicked, picked);
            d.picked = picked != 0;
            if (!readBoundsOrigin(mem, a, d.pos)) continue;   // not spawned yet
            cachedDrops.push_back(d);
        }
    }
    return cachedDrops;
}
