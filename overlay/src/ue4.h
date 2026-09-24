#pragma once
// UE4 data extraction layer for GhostHunterClientSteam (modified UE 4.27).
// All offsets verified via live memory analysis (see HANDOFF.md).
#include "mem.h"
#include <string>
#include <vector>
#include <unordered_map>

// ---------------- module-relative offsets ----------------
namespace offs {
    constexpr uintptr_t GNAMES   = 0xAFCAF40;  // FNamePool
    constexpr uintptr_t GWORLD   = 0xB11BB68;  // UWorld* (active world; the other global +0xB117F20 goes stale)
    constexpr uintptr_t GWORLD2  = 0xB118F20;  // secondary world global (fallback)
    constexpr uintptr_t GENGINE  = 0xB11EB40;  // UEngine*
    // world / level
    constexpr uintptr_t WorldPersistentLevel = 0x30;
    constexpr uintptr_t WorldStreamingLevels = 0x90;   // TArray<ULevelStreaming*>
    constexpr uintptr_t WorldGameInstance    = 0x228;
    constexpr uintptr_t StreamingLoadedLevel = 0x190;  // ULevelStreaming::LoadedLevel
    constexpr uintptr_t LevelActors          = 0xA0;   // TArray<AActor*>
    // uobject
    constexpr uintptr_t ObjClass   = 0x10;
    constexpr uintptr_t ObjName    = 0x18;   // FName comparison index (u32)
    constexpr uintptr_t ActorRoot  = 0x1B8;  // AActor::RootComponent
    // reflection (custom layouts!)
    constexpr uintptr_t StructSuperStruct      = 0x40;
    constexpr uintptr_t StructChildren         = 0x48;  // UStruct::Children (UField list: functions)
    constexpr uintptr_t StructChildProperties  = 0x50;
    constexpr uintptr_t FieldNext              = 0x18;  // FProperty::Next
    constexpr uintptr_t UFieldNext             = 0x28;  // UField::Next (children/function chain - verified live)
    constexpr uintptr_t FieldName              = 0x20;  // FName idx (u32)
    constexpr uintptr_t FieldOffsetInternal    = 0x44;  // FProperty::Offset_Internal
    constexpr uintptr_t StructPropStruct       = 0x70;  // FStructProperty -> UScriptStruct*
    // scene component (double precision build!)
    constexpr uintptr_t SceneBoundsOrigin      = 0x110; // BoxSphereBounds::Origin (3 doubles, WORLD space)
    // TreasureBox family (BP_YiGui_C / BP_HeZi_C / BP_BaoXiang_C)
    constexpr uintptr_t TBNameText  = 0x450;  // FInteractItemAssetData::Name (FText, quality-resolved)
    constexpr uintptr_t TBState     = 0x2B8;  // CurrentBaoXiangState (byte; 3 = looted)
    constexpr uintptr_t TBCom       = 0x338;  // BP_BeInteractCom
    // Tool family (BP_Mining_C / BP_Collect_C, base BP_ToolBase_C)
    constexpr uintptr_t ToolCom        = 0x2C8;  // BP_BeInteractCom
    constexpr uintptr_t ToolSpawnIndex = 0x310;  // quality selection 1..5
    // Drop items (BP_DropInteract_C -> BP_DropItem_C): DTItemData inline @ +0x450 (KxItemTable)
    constexpr uintptr_t DropItemId  = 0x458;  // int
    constexpr uintptr_t DropName    = 0x460;  // FText ItemName
    constexpr uintptr_t DropColor   = 0x4BA;  // byte ItemColor (palette: 0白 1蓝 2紫 3金 4红)
    constexpr uintptr_t DropPicked  = 0x740;  // bool bServerPickedUp
    // interaction component (IS_BeInteractComponent)
    constexpr uintptr_t ComTextMap   = 0x7B0;  // TMap<FName, FText> keyed Default,1..5 (element stride 0x20)
    constexpr uintptr_t ComColorMap  = 0x800;  // TMap<FName, FLinearColor> palette
    // player pawn (BP_PlayerHumanBase_C; reflection-resolved at runtime, this is fallback)
    constexpr uintptr_t PawnBeInteractCom = 0x1320;  // player's own interact component
    // FText: { ITextData* } ; history string at +0x20 -> FString {ptr,num,max}
    constexpr uintptr_t FTextStringPtr = 0x20;
}

enum class Quality : int {
    White = 0, Blue = 1, Purple = 2, Gold = 3, Red = 4, Divine = 5, Unknown = -1
};

struct ContainerEsp {
    std::wstring name;
    Quality quality = Quality::Unknown;
    double pos[3]{};
    double dist = 0;
    uintptr_t actor = 0;
    bool isTool = false;   // true = mining/collect node (no 0x2B8 open-state)
};

struct CameraData {
    double loc[3]{};
    float rot[3]{};
    float fov = 90.f;
    bool valid = false;
};

struct DropEsp {
    uintptr_t actor = 0;
    double pos[3]{};
    bool picked = false;
};

class UE4 {
public:
    explicit UE4(const Mem& m) : mem(m) {}

    bool init();
    bool pollWorld();
    const std::vector<ContainerEsp>& containers();   // cached, refreshes every 2s
    CameraData getCamera();

    // --- XiXing support ---
    uintptr_t getPawn();                                    // live pawn (reflection; cached per world)
    uintptr_t findFunction(uintptr_t cls, const char* fn);  // UFunction* by name (cached per world)
    uintptr_t pawnClass();                                  // pawn's class (for function lookup)
    const std::vector<DropEsp>& drops();                    // fast drop scan (~400ms cadence)
    std::string funcNameOf(uintptr_t ufunc);                // resolve UFunction -> name (for logger)
    std::string classNameOf(uintptr_t obj);                 // object's class name (validation)
    uintptr_t worldPtr() const { return world; }            // world-change proxy for external caches
    bool readActorPos(uintptr_t actor, double pos[3]);      // RootComponent bounds origin (world space)
    uintptr_t readPtrAt(uintptr_t addr) const { return mem.readPtr(addr); }

    std::string worldName;
    int lastScanCount = 0;

private:
    const Mem& mem;
    uintptr_t gnames = 0;

    // container class pointers (per-world; classes reload with the map)
    uintptr_t clsTB[3] = {0,0,0};
    uintptr_t clsTool[2] = {0,0};   // Mining, Collect
    uintptr_t clsDrop = 0;          // BP_DropInteract_C

    // camera chain offsets (reflection-resolved)
    uintptr_t offPlayerCameraManager = 0;
    uintptr_t offCameraCachePrivate  = 0;
    uintptr_t offCachePOV            = 0;
    uintptr_t offPOVLocation = 0, offPOVRotation = 0, offPOVFOV = 0;
    bool cameraOffsetsResolved = false;
    int povLocStep = 8, povRotStep = 8;

    // pawn (per-world cache; re-resolved when stale)
    uintptr_t pawn = 0;
    uintptr_t pawnCls = 0;
    uintptr_t offPCPawn = 0;          // PlayerController::Pawn (reflection)

    // UFunction cache: key = owning class ptr, value = {name -> UFunction*}
    std::unordered_map<uintptr_t, std::unordered_map<std::string, uintptr_t>> funcCache;

    std::unordered_map<uintptr_t, std::wstring> nameCache;
    std::unordered_map<uintptr_t, Quality> qualityCache;   // tool actors: quality from SpawnIndex
    std::vector<ContainerEsp> cached;
    std::vector<DropEsp> cachedDrops;
    DWORD lastScanTick = 0;
    DWORD lastLevelTick = 0;
    DWORD lastDropTick = 0;

    uintptr_t world = 0;
    std::vector<uintptr_t> levels;
    uintptr_t cameraManager = 0;

    std::string fname(uint32_t idx);
    uintptr_t findField(uintptr_t cls, const char* fieldName);
    uintptr_t findFieldInStruct(uintptr_t scriptStruct, const char* fieldName);
    bool resolveCameraOffsets(uintptr_t pc, uintptr_t pcm);
    uintptr_t findClassByName(const char* name);
    void refreshLevels();
    void fullScan();
    std::wstring readFText(uintptr_t actor, uintptr_t fieldOff);
    std::wstring readTextMapEntry(uintptr_t com, int elementIndex);
    Quality qualityForName(const std::wstring& name);
};
