# QuLing Tianshi (驱灵天师) Client-Side Security Research

[中文版 (Chinese)](README.md)

Security research toolchain and findings for **QuLing Tianshi (驱灵天师)**, a modified Unreal Engine 4.27 Chinese PVE ghost-hunting game (process name `GhostHunterClientSteam-Win64-Shipping.exe`). The research uses **read-only memory analysis + in-process function invocation (RPC replay)** to evaluate the client's attack surface and the strength of server-side validation.

> This repository is intended as a vulnerability disclosure to the game's developers. All findings were verified on the live game; reproduction tools and remediation advice are included.

## Timeline

| Date | Event |
|---|---|
| 2026-09-21 ~ 09-22 | Initial research period: the entire developer test RPC family was callable (ATK boost / HP boost / item grant / invincibility / cooldown reset / teleport) |
| 2026-09-24 | Official update: all of the above sealed (this toolchain verified the fixes the same day, see below) |

## Findings

### 1. Issues fixed by the 2026-09-24 update (verified)

| Vulnerability | Mechanism | Verification |
|---|---|---|
| Developer backdoor RPC family | The shipping client embeds 14+ debug RPCs (`ServeraddATK`, `ServeraddHP`, `ServerWHOSYOURDADDY`, `ServerResetCD`, `ServerAddItem`, `ServerTeleport`, ...) that the old server executed directly | Full parameter-matrix testing + `ActiveCustomTest` gate-combination probes — all now inert |
| Local debug functions | `ShowCheatPanel` / `GM` / `AddFullHealth` / `SkillNoCDMode` etc. no-op behind an unactivated privilege flag | Direct queue invocation — no effect |
| Generic command channel | `ServerExecRPC(FString Msg)` command-string entry; CheatManager-class commands filtered by server-side privilege | Slomo / Disconnect — no effect |
| Main-city test layer | `Debug_AddItemToDepotBackpack` / `RandomConstructTestData` (random item-table draw → PB send) | Direct call + command-string variants — zero output |

### 2. Issues still alive (recommend the developers address these)

1. **Plaintext sensitive data on the client** — `GNames` (name pool), `GWorld`, actor layouts, and container quality markers are fully readable and structurally stable in client memory, making ESP (wallhack-style overlays) trivial. After the 09-24 update the offsets merely shifted uniformly (+0x70 in .text / +0x1000 in .data); re-location takes about ten minutes with the included tooling.
2. **No behavioral validation on interact RPCs** — `ServerSetPreBeInteractComponent` + `TryActivateAbility` can be scripted to batch-trigger interactions (auto-opening chests/mining/gathering). The server only range-checks (~70–90 m) with no frequency or behavioral-pattern analysis.
3. **No distance/frequency validation on pickup RPC** — `Server_RequestPickupByUI` can be called in bulk for every drop in range.
4. **Anti-cheat (Hercules) blind spots** — DLL injection (CreateRemoteThread + LoadLibraryW), `ProcessEvent` inline hooks (12-byte jump replacement), and shared-memory command queues run entirely undetected.
5. **Weak anti-farming rule** — roughly 8 interactions within a short window (measured ≈1–2 minutes) triggers a kick. The rule is a simple time-window counter: combat does **not** reset the count (measured); no deeper behavioral analysis exists.


### 3. Other client-side content found during research (for the developers' reference)

All of the following was sealed or neutralized by the 2026-09-24 update, but **the code is still compiled into the shipping client** — consider removing it from builds entirely:

| Finding | Details |
|---|---|
| GM panel function | `ShowCheatPanel` (local; the pawn has a `CheatPanel` slot but no instance is ever created) — gated behind an unactivated privilege flag |
| Full CheatManager class | UE's native cheat-manager class is fully compiled into the client: 57 functions (`God / Fly / Ghost / Teleport / Summon / Slomo / ChangeSize / PlayersOnly` ...); runtime instance is null but the class object is reachable |
| Server→client cheat handlers | `ClientCheatFly / ClientCheatGhost / ClientCheatWalk` present on the pawn |
| Test-mode gate function | `ActiveCustomTest` (C→S, no params; likely a "custom test mode" switch — now ignored by the server) |
| Generic command-string channel | `ServerExecRPC(FString Msg)` on the PlayerController |
| Main-city test layer | `Debug_AddItemToDepotBackpack` (add items to depot), `RandomConstructTestData` (draw N random items from the item table → construct PB data → send), `SendTestPBData_01` |
| Editor/debug leftovers | `EditorCheatSpawnGhost` (spawn ghost), `GM` (local), `Server_SimCrash`, `ServerKillSelf` |
| Full developer backdoor RPC family | 14+ (`ServeraddATK / ServeraddHP / ServerWHOSYOURDADDY / ServerResetCD / ServerAddItem / ServerAddMoney / ServerAddSoul / ServerAddLingBi / ServerTeleport` ...), full signatures in HANDOFF.md S3.4 |
| Full GAS attribute table | 100+ attributes (damage% / melee·ranged·AOE coefficients / attack speed / chest-open speed / five-element attack & defense / full crit set) fully readable in client memory (HANDOFF.md S3.5) |
| ServeraddHP semantics | Adds directly to the HP pool — one call of +200000 doubled the bar in the old build (not a full heal) |
| Trade-house market prices | Server-side only, never cached on the client (full-memory scan found zero hits) |

### Remediation advice (mapped to each live finding)

**1. Plaintext sensitive data (ESP)**
- Root fix (server): sync container quality data **on demand** (when the player approaches/looks at a target) instead of keeping the full map's quality markers resident in client memory
- Client mitigation: per-launch randomized offsets for key globals (GNames/GWorld) and encrypted quality markers — raising re-location cost from "ten minutes" to "per launch"
- Verified status: after the client rebuild, offsets merely shifted uniformly (+0x70/+0x1000); signature scan + vtable-ref counting + name-pool content scan re-locates everything in ~10 minutes (see `tools/offset_hunt*.py`)

**2. No behavioral validation on interact RPCs**
- Server-side: keep the existing ~70-90 m range check, add **view-direction validation** (player must roughly face the target)
- Target-distribution analysis: batch interaction with homogeneous target clusters (same container/gather type) within a short window = anomaly
- Tighter per-time-window interaction caps with per-account sliding windows

**3. No distance/frequency validation on pickup RPC**
- Validate pickup distance against the **server-tracked** player position (never trust client-reported position)
- Rate cap (e.g. ≤5/s) + cross-validation against movement trajectory (consecutive pickups faster than physically possible = anomaly)

**4. Anti-cheat (Hercules) blind spots**
- Injection detection: monitor `CreateRemoteThread` + `LoadLibraryW` combinations; scan for unsigned modules
- **Inline-hook integrity checks**: periodic hash verification of the first N bytes of critical functions (`ProcessEvent` / `CallRemoteFunction`) — this catches the 12/13-byte jump replacement used in this research
- Named-kernel-object scanning: non-game named shared-memory sections (this toolchain communicates over fixed-name sections, trivially discoverable)

**5. Weak anti-farming rule**
- Beyond the time-window counter: interaction **target-diversity** weighting (consecutive homogeneous targets count heavier) and movement-trajectory cross-validation (teleport-like target switching = anomaly)
- Hot-updateable rule parameters (window length / caps) so fixed values cannot be probed

**General**
- Remove all debug/test content from the inventory above **at build time** (build-configuration stripping) — runtime gating has been shown to invite per-layer bypass attempts (this research probed every layer)

## Repository layout

```
├── README.md            ← this file (Chinese version: README.md)
├── README_EN.md         ← English version
├── HANDOFF.md           ← full technical knowledge base (offsets/protocols/mechanisms/dead ends, 588 lines, Chinese)
├── USAGE.md             ← tool usage guide (Chinese)
├── overlay/             ← C++ tools (layered-window ESP + remote-container-touch (隔空摸容器) pipeline + injected DLL source)
│   ├── src/             │   main.cpp / ue4.cpp / xixing.cpp / xixing_dll.cpp ...
│   └── build*.bat       │   one-shot MSVC build
├── tools/               ← Python research toolchain (zero dependencies)
│   ├── uemem.py         │   pure-RPM UE4 reflection reader (adapted to the modified layout)
│   ├── offset_hunt*.py  │   post-update offset re-hunt methodology (signature scan / vtable-ref counting / heap content scan)
│   ├── xxstar.py etc.   │   interact-pipeline reproduction scripts
│   └── dump_*.py        │   RPC signature / attribute-system dump tools
└── xxstart.bat          ← one-click launcher
```

## Key technical points (details in HANDOFF.md, Chinese)

- The game's UE4.27 layout is modified: FNamePool header `(len<<6)|(hash<<1)|wide`, globally shifted FField offsets, two GWorld globals
- Offset hunting methodology: ProcessEvent signature scan + vtable-reference-count disambiguation + name-pool content scan (fixed engine registration order `None→ByteProperty→IntProperty`)
- Game-thread command queue: 16-slot shared-memory SPSC ring; the DLL executes UFunction calls on the game thread inside the `ProcessEvent` hook
- Full GAS attribute-system table, the 56-byte `FGameplayAttribute` layout, and the flip-latch mechanism for capturing interact ability handles

## Reproduction

```
1. Build:    cd overlay && build.bat          (MSVC 2022; produces overlay.exe + DLL)
2. Run:      xxstart.bat                       (or manually: start overlay\build\overlay.exe)
3. In match: F2 filters / M4 auto-interact pipeline / hotkeys in USAGE.md
4. After a game update: python tools/offset_hunt.py   (~10 min to re-locate all offsets)
```

## Disclaimer

This project is for security research and vulnerability-disclosure purposes only. Do not use it to undermine game fairness or in any way that violates the game's terms of service.
