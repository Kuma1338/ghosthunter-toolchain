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
5. **Weak anti-farming rule** — the "≈8 consecutive interactions without combat → kick" rule is trivially bypassed by dealing one hit in between.

### Remediation advice

- **Server-side**: behavioral-pattern analysis for interactions (frequency, path coherence, target distribution); distance and rate validation for pickups.
- **Client-side**: obfuscation/encryption of key in-memory data (at minimum to defeat static offset analysis); injection and inline-hook detection; strip or build-gate debug functions and test layers.

## Repository layout

```
├── README.md            ← this file (Chinese version: README.md)
├── README_EN.md         ← English version
├── HANDOFF.md           ← full technical knowledge base (offsets/protocols/mechanisms/dead ends, 588 lines, Chinese)
├── USAGE.md             ← tool usage guide (Chinese)
├── overlay/             ← C++ tools (layered-window ESP + auto-interact pipeline + injected DLL source)
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
