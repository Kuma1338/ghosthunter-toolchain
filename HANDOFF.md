# GhostHunter 容器 ESP 项目交接文档

> 本文档自包含。接手者无需重新逆向，所有偏移和机制均已实测验证。
> 最后更新：2026-09-24（第四session：游戏更新→偏移重猎→调试函数族被服务器封禁）
> **所有文件路径均为项目根目录相对路径**（工具源码里的绝对路径以实际部署盘符为准）。
>
> 🔧 **2026-09-22 的 `TODO-FIX.md` 工作单中，任务1/2/3/4 已在 2026-09-22~24 修完并验证**
> （槽解除武装、SEH 日志去重、doneSeq 顺序、DLL 版本标记）。任务 0/5/6 未做。

## 📖 术语表（先读这个，全文用词以此为准）

| 文档术语 | 精确定义 | 对应实现/热键 |
|---|---|---|
| **ESP** | 屏幕覆盖层（分层窗口）显示容器/掉落物方框+名字+距离，纯客户端读内存 | overlay.exe |
| **隔空摸容器** | 按键后批量触发地图内金/红容器的交互流程（SetPre+技能激活→开箱→自动拾取掉落物） | M4 键，xixing.cpp runPipeline |
| **latch / 句柄锁存** | 玩家手动交互一次后，从 RPC 记录环捕获 GAS 交互技能的 SpecHandle（小整数，每局变化） | watchManualOpen |
| **自动拾取** | 对地面掉落物逐个发送拾取 RPC（红/金品质优先） | pickRedGoldDrops |
| **增伤** | 调用 ServeraddATK(Value) 使服务器上调角色攻击数值 | F9 键（当前 +200000）⚠️ 见 S4 |
| **回血** | 调用 ServeraddHP(Value) | F10 键 ⚠️ 见 S4 |
| **后门函数** | 游戏客户端编译内置的 Server* 系列调试 RPC（addATK/addHP/WHOSYOURDADDY/ResetCD/AddItem/AddAttribute…），历史版本服务器直接执行，**2026-09-24 更新后被服务器忽略** | NOS_PlayerHuman 类链 |
| **反作弊（交互节奏）** | 短时间内（估计 1~2 分钟，未精确测定）连续交互约 8 次即被踢出对局；打怪/其他行为是否重置计数未验证 | — |
| **吸怪** | （已否决的方案）把怪物 actor 移到玩家附近——服务器权威复制会立即拉回 | — |
| **12 字节跳转替换** | 进程内 hook 技术：备份目标函数前 12 字节，替换为 mov rax,imm64; jmp rax | xixing_dll.cpp |
| **跳转替换（13 字节）** | 同上，用于 UActorComponent::CallRemoteFunction（RPC 记录） | installCRFHook |
| **队列** | 共享内存 16 槽命令环，overlay/工具把 UFunction 调用排入，DLL 在游戏主线程执行 | XIXING_SHARED_V1 |
| **RPC 记录环** | DLL 捕获组件层 RPC 流量的环形缓冲（含 TryActivateAbility 的句柄参数） | XIXING_RPC_V1 |

---

# ⭐⭐⭐ 第四 Session（2026-09-24）：游戏更新 → 偏移重猎 → 后门函数族被服务器封禁

**背景**：游戏客户端更新（重编译），全部模块偏移失效。重猎后全链复活（ESP/队列/记录环正常），但 F9/F10 失效——探针证实**新服务器忽略整个后门函数族**。

## S4.1 新偏移表（2026-09-24 游戏版本，全部实测验证）

| 全局/函数 | 新偏移 | 旧偏移 | 位移规律 |
|---|---|---|---|
| ProcessEvent | **+0x15E51E0** | +0x15E5170 | .text 统一 +0x70 |
| CallRemoteFunction | **+0x27EEBA0** | +0x27EEB30 | .text 统一 +0x70 |
| GNames (FNamePool) | **+0xAFCAF40** | +0xAFC9F40 | .data 统一 +0x1000 |
| GWorld | **+0xB11BB68** | +0xB11AB68 | .data 统一 +0x1000 |
| GWorld2 / GEngine | +0xB118F20 / +0xB11EB40 | — | 同 +0x1000 |

- FNamePool 编码**未变**（`len<<6 \| hash<<1 \| wide`）
- 新对局角色类 = `BP_YiMeiDaoZhang_C`（新角色），**pawn 组件偏移全部未变**（ASC+0xC38、IS_Interact+0x1328、AsyncTask+0x1C60、Inventory+0x10E0、SpawnedAttributes+0x1080）
- DLL 已升版为 **xixing8.dll**（改名注入可绕过旧 DLL 占位，无需重启游戏）
- **下次游戏更新的标准流程**（工具已固化，约 10 分钟）：`tools/offset_hunt*.py` ①特征码扫 .text 拿候选 → ②虚表引用计数消歧（真函数有数千个引用）→ ③堆内容扫描找名字池（引擎固定注册序 `None→ByteProperty→IntProperty`；**池块在低地址内存区 ~0x216...，不在 0x7FF 高堆**）→ ④类名=='World' 严格锁 GWorld → ⑤互证（池解出世界名+关卡类名）

## S4.2 后门函数族已被服务器封禁（本轮最重要结论）

**证据链**（全部客户端侧正常）：
- ServeraddATK/ServeraddHP/WHOSYOURDADDY/ResetCD 函数仍存在，NetServer 标志在，参数布局未变（Value@0 i32）
- 队列调用 result=1（真实执行、RPC 已发往服务器）
- **但实测**：无敌（WHOSYOURDADDY）照常掉血、CD（ResetCD）照常转动、增伤无伤害变化

**结论**：服务器端更新后**忽略整个 Server\* 开发者测试族**（大概率本次更新主要目的即此）。S3.4 函数清单保留作参考，但全部标记为服务器侧失效。

**2026-09-24 全量漏洞面审计（终审）——调试面已全面焊死**：

- **开发者测试族（14 个旧洞全部已补）**：addATK/addHP/addMP/addYangQi/replyHP/WYD/ResetCD/AddItem/Money/LingBi/Soul/PropItem/RCharge/Teleport —— 服务器全部忽略（客户端执行正常 result=1，游戏内零效果）
- **ActiveCustomTest 门控无效**：先开门再打 WYD/addATK 的组合实测无效果——不是门控问题，是函数族整体被服务器弃用
- **换名变体也死**：ServerAddHealth(无参)/ServerreplyMP/SkillNoCDMode —— 全灭
- **本地调试函数全部空转**：ShowCheatPanel/GM/AddFullHealth/SetPerspective/ChangeUTP/SeqJumpToEnd —— result=1 但无任何效果（内部有权限标志检查，标志从未被激活）
- **CheatPanel(pawn+0x778)/CheatManager(PC+0x410) 均为空指针**——面板从未创建、管理器从未实例化；但 **CheatManager 类对象完整在内存**（57 个函数：God/Fly/Ghost/Teleport/Summon/Slomo 全家，CheatClass@PC+0x418 可达），客户端无法正规实例化
- **ClientCheatFly/Ghost/Walk（S→C 移动模式落地函数）本地直调无效**——飞行未生效（空转或被即时纠正，与吸怪同款服务器权威）

**追加终审（同日第二轮扫描——PC 层 + 主城层 + Exec 通道）**：
- **ServerExecRPC(Msg:FString)**（PC 层通用命令字符串入口）：客户端执行干净（result=1），`Slomo 0.5` 无效果；`Disconnect` 发出后玩家一度卡在切图加载页（疑似生效但未复现确认）。若通道活着，也只是 PC 级 exec 可达——CheatManager 级命令（God/Fly/Slomo）被服务器特权过滤（服务器侧 PC 无 CheatManager）
- **主城 PC（BP_PC_MainCity_C）测试层**：`Debug_AddItemToDepotBackpack(DebugItemArray:TArray)`（直调+命令串双路）与 `RandomConstructTestData(ItemCount:i32)`（内部逻辑=物品表随机抽取→GetItemConfig→ConstructPBData→PB 发送）——全部 result=1 但零效果。空转模式与 pawn 本地调试函数一致（权限标志未激活或服务器拒收 PB）
- **FUNC_Exec 面枚举**：无游戏自定义可执行命令（全部为标准 BlueprintCallable 面 + 主城测试层）
- **结论固化：2026-09-24 更新把开发者/测试面从 RPC、本地函数、Exec 通道到主城测试层全部焊死**。无服务器配合的情况下，数值类功能（攻击力/生命值/物品/CD/移动模式）在新版本不可达

**幸存且可用**：ESP（纯客户端读）、隔空摸容器 M4（真实游戏系统：SetPre+TryActivateAbility+拾取）、全套工具链基础设施。

**未验证的线索**（下个 AI 从这里继续）：
1. **`ActiveCustomTest`** [C→S, 无参]——疑似"激活自定义测试模式"的门控函数（新版本加的）。已三连发 `ActiveCustomTest → WHOSYOURDADDY → addATK(200000)`（全部 result=1），**等用户实测是否解锁**。若解锁 → 每次热键前先调它，全族复活
2. `Server_SkillNoCDMode` [C→S]——技能无 CD 模式（未测）
3. `ShowCheatPanel` [本地]——游戏内置测试面板 UI，本地调用可弹出（未测，面板按钮可能走正规通道）
4. `ClientCheatFly/Ghost/Walk` [S→C]——服务器下发的调试命令处理器（本地调用仅影响客户端，移动受服务器权威约束）
5. `EditorCheatSpawnGhost` [C→S]、`GM` [本地]
6. `ServerAddAttribute`（S3.6 攻坚配方仍有效——但同族大概率同样被封）

## S4.3 本 session 修复的旧 bug（继 TODO-FIX 之后）

1. **RPC 环条目布局错位**：xixing.h 的 RpcEntry 曾是 v4 无 tick 布局（parms@+16），xixing5+ DLL 写 parms@+20 → latch 把 GetTickCount 当句柄。已对齐（含 tick）
2. **验证目标污染**：金/红排序下最近的"红目标"可能是灵丛（需持续输入的采集节点），单发激活完不成 → 验证永远失败 → M4 看似失灵。已改为验证只认箱类（BP_YiGui/HeZi/BaoXiang），Target.tool 标记 + 箱优先排序
3. **相机有限值垃圾**：悬空 PCM 读出有限乱值时 NaN 恢复判据失效 → ESP 消失但 HUD 正常。已加 POV-距-pawn>50m 贴身校验 + camdiag 逐步遥测
4. **队列计数器竞态污染**：Python 工具非原子写 pending 与 DLL 排水竞争 → pending 涨到 36 亿 → 全部提交被拒。已强制复位。**铁律：队列只允许 overlay 一个生产者，Python fire() 不再直写共享内存**
5. **吸取路径补全**：0 目标时不再早退，直接跑自动拾取（免句柄）

---

# ⭐⭐ 第三 Session（2026-09-21 下午）：怪物解决方案 + GAS 属性系统完全解剖

**背景**：用户要求"僵尸不主动来找我 / 范围伤害 / 僵尸受击范围变大 / 增伤"。吸怪→失败；肥胶囊→死路；**F9 增伤已交付**；期间完整解剖了 GAS 属性系统 + 找到一整个后门函数清单。

## S3.1 F9 增伤（✅ 已验证交付）

```
pawn.ServeraddATK(99999)   [this=pawn，经队列 ProcessEvent，客户端→主机]
→ 实测 ~26000 伤害/击，小怪一刀秒（用户确认伤害数字）
```
- overlay 已实现：F9 热键 → `XiXing::fireDevRpc(mem, ue, "ServeraddATK", 99999)`（xixing.cpp:563，找 UFunction 用 `pawn+0x10` 类指针，parms 为 `int32@0`）
- ⚠️ **它改的不是 GAS 的 Atk 属性**（实测打完 GAS Atk 值纹丝不动，见 3.5）——它的效果只能用伤害数字观测。同理 GAS Atk 读数不变 ≠ 未生效
- ⚠️ 未在新游戏进程复测（当天游戏重启 3+ 次，最后一次确认生效在 pid 3676 时代）

**裸写 float 的两个致命误解**：
1. **碰撞形状（SphylElem）在怪物生成时就烤入物理引擎**——裸写 CapsuleRadius 根本不会更新碰撞形状 → 受击判定完全不变
2. 但**半高 float 被客户端角色移动组件实时读取**——写 300 让它按"胶囊底要贴地"把怪顶上天 2 米，复制又拽回来 → **怪悬空抽搐**（用户目击实证）

- 污染自愈：怪物死亡即销毁（对局内数量 3→8→3 波动实证），新对局全新生成，无对象池残留（实测胶囊回 r=42/h=96）✅
- 若坚持走此路：必须经队列调 **`UCapsuleComponent::SetCapsuleSize`**（引擎 API，会重建物理形状）——未测试。且注意肥胶囊会物理阻挡玩家 + 命中判定是否客户端侧存疑

## S3.4 后门函数清单（pawn 类链 `NOS_PlayerHuman` 上）
⚠️ **2026-09-24 起本节全部函数被服务器忽略（见 S4.2 探针证据）——清单仅作签名参考，勿再当作可用杠杆。**

全部用 `this=pawn` + 队列调用。签名 = 参数名@偏移(大小)。RPC 方向判定：UFunction flags@+0xB0 低 dword **0x200000=NetServer（客户端可调）**、0x1000000=NetClient、0x4000=Multicast、无=本地。

```
✅ ServeraddATK(Value@0, i32)                    增伤（F9 已验证）
?  ServeraddHP / ServeraddMP / ServeraddYangQi / ServerreplyHP (Value@0, i32)
?  ServerAddAttribute(Attribute@0 56B, Method@0x38 u8, Value@0x3C f32, Type@0x40 u8)
     ⚠️ 未打通，攻坚状态见 3.6 —— 通用 GAS 属性调试接口，价值最高
?  ServerWHOSYOURDADDY()                          无敌（无参数）
?  ServerResetCD() / ReSetCD()                    技能CD秒重置（无参数）
?  ServerAddPerspective(PerspectiveData@0, 32B)   透视
?  ServerAddItem(ItemId@0 i32, Count@4, GNum@8, BType@C) / ServerAddItemAutoGNum
?  ServerAddLingBi / ServerAddMoney / ServerAddPropItem / ServerAddRCharge / ServerAddSoul
-  AddModDamage(Damage@0, 16B) / RemoveModDamage / GetAllDamageAdditionScale  (local, NOS_PlayerBase)
-  PDC_CheckDamagePredicted_NoAdd(UD@0, ServerHitActor@8) → bool   (local)
     预测伤害闸门——疑似客户端距离/命中校验，若信任其结果，patch 返回值=远距命中（未探）
-  NormalAttack() (local, NOS_PlayerBase)
```
**pawn 类**：对局内 = `BP_JianYan_C`（链：BP_JianYan_C → BP_PlayerHumanBase_C → NOS_PlayerHuman → NOS_PlayerBase → NOS_Character → KxCharacter → Character）
主城 = `BP_CharacterBase_MainCity_C`（**必须排除**——类不同，调试接口函数不在它身上）

## S3.5 GAS 属性系统（完整解剖，全表）

```
ASC = pawn+0xC38  (NOS_AbilitySystemComponent)
属性集实例 = ASC.SpawnedAttributes @ +0x1080（TArray<UAttributeSet*>）
  对局里只有 1 个：NOS_HumanAttributeSet 实例（继承链含 AttributeSetBase 全部属性）
属性 = FGameplayAttributeData，16字节/个（CurrentValue@+0, BaseValue@+4）
```

**NOS_AttributeSetBase 属性表**（偏移=实例内 Offset_Internal，全 16B/个）：
```
+0x030 CurHealth            +0x040 MaxHealth         +0x050 YangQi
+0x060 YingQi               +0x070 MaxYingQi         +0x080 YingQiRecovery
+0x090 HpRecovery           +0x0A0 CurSpeedScale     +0x0B0 JogSpeed
+0x0C0 JumpHeight           +0x0D0 Damage            +0x0E0 Atk
+0x0F0 DamageReduction      +0x100 Shield            +0x110 MaxShieldByHealthPercent
+0x120 ShieldCostDuration   +0x130 WuXingAtk         +0x140 GoldAtk
+0x150 WaterAtk             +0x160 WoodAtk           +0x170 FireAtk
+0x180 SoilAtk              +0x190 WuXingDef         +0x1A0 GoldDef
+0x1B0 WaterDef             +0x1C0 WoodDef           +0x1D0 FireDef
+0x1E0 SoilDef              +0x1F0 YingYangAtk       +0x200 YingAtk
+0x210 YangAtk              +0x220 YingYangDef       +0x230 YangDef
+0x240 YingDef              +0x250 YiShangPercent(易伤) +0x260 ZengShangPercent(增伤)
+0x270 AtkCriticalProbability +0x280 AtkCriticalDamage
+0x290 YangQiCriticalProbability +0x2A0 YangQiCriticalDamage
+0x2B0 CriticalstrikeResist +0x2C0 CriticaldamageResist
+0x2D0 ArmorPiercing        +0x2E0 ArmorIgnore
+0x2F0 MeleeCoefficient     +0x300 RangeCoefficient  +0x310 AOECoefficient
+0x320 FinalCoefficient     +0x330 InGameAdditionSetCustomMap(80B)  +0x380 bInited(1B)
```

**NOS_HumanAttributeSet 追加**：
```
+0x388 exp                  +0x398 Resilience         +0x3A8 MaxResilience
+0x3B8 Stamina              +0x3C8 MaxStamina         +0x3D8 StaminaRecovery
+0x3E8 Souls                +0x3F8 StaminaCost        +0x408 StaminaDashCost
+0x418 Weight               +0x428 CurMP              +0x438 MaxMp
+0x448 MpRecovery           +0x458 CouchSpeed         +0x468 CrawlSpeed
+0x478 DamageSpeed          +0x488 HeaveDamageSpeed   +0x498 HumanSprintSpeedScale
+0x4A8 Treatment            +0x4B8 TreatmentTime      +0x4C8 AttackSpeed ★
+0x4D8 InteractiveSpeed     +0x4E8 CdData             +0x4F8 LevelinGame
+0x508 NumberManage         +0x518 CDManage           +0x528 ReceivedTreatment
+0x538 StaminaUseRatio      +0x548 DodgeStaminaUseRatio  +0x558 TapStaminaUseRatio
+0x568 HeavyBlowStaminaUseRatio +0x578 AttackStaminaUseRatio
+0x588 SarCdData            +0x598 StuntCdData        +0x5A8 ItemCoefficient
+0x5B8 WeaponCoefficient    +0x5C8 BookCoefficient    +0x5D8 MainStarCoefficient
+0x5E8 HumanCoefficient     +0x5F8 ThingCoefficient   +0x608 DashDistance ★
+0x618 DashSpeed            +0x628 DeBuffDuration     +0x638 Buffduration
+0x648 DroppercentUP        +0x658 AcceptanceUP       +0x668 RescueSpeed
+0x678 AbsorbSpeed          +0x688 OpenSpeed ★(开箱速度)  +0x698 SoulProportion
+0x6A8 CurSkillCharge       +0x6B8 MaxSkillCharge     +0x6C8 OneBOOM
+0x6D8 TwoBOOM              +0x6E8 ThreeBOOM          +0x6F8 FourBOOM
+0x708 FiveBOOM             +0x718 ExecuteGhostScale
```
⚠️ **没有 AttackRange/触及距离属性**——近战范围不在 GAS 里（在技能/蒙太奇 notify 配置里，未探）。
实用组合（用户目标=僵尸别打断开箱）：`OpenSpeed`↑ + `AttackSpeed`↑ + `ZengShangPercent`/`FinalCoefficient`/`MeleeCoefficient`↑ + F9 一刀秒。

## S3.6 ServerAddAttribute 攻坚状态（未打通，弹药已备齐）

**调用配方（已全部实现，attrfire.py）**：
```
parms 0x48 字节：
  +0x00 FString{char*→UTF-16字符串(VirtualAllocEx), len, cap}   ← 属性名，UTF-16！
  +0x10 FProperty*（目标属性的反射节点，类链 ChildProperties 现场解析）
  +0x18..0x2F TFieldPath 弱句柄区（从真实模板整块照抄）
  +0x30 AttributeOwner（模板里的属性集 Class 指针）
  +0x38 Method(u8)  +0x3C Value(f32)  +0x40 Type(u8)
```
**FGameplayAttribute 结构（56B，魔改）**：`FString AttributeName@0(16B,UTF-16)` + `TFieldPath<FProperty>@0x10(32B，FProperty*在+0x10)` + `AttributeOwner@0x30(8B=属性集Class)`。
真实模板挖法：pawn 上的 AsyncTask（`pawn+0x1C60` = NOS_AsyncTaskAttributeChanged 'Async Task_HealthChanged'）内存里扫 0x800 字节——找"指向的+0x20 解析为属性集属性名"的指针，其-0x10 处即完整 FGameplayAttribute（CurHealth 模板在 +0x48 稳定出现，Resilience@+0x288 时有时无——**布局每局漂移，禁止硬编码**）。

**已排除**：
- UTF-16 bug（第一轮 8 发全是 ASCII 乱码名 → 修成宽字符后 8 发仍无效）
- 队列故障（hookOk 武装/pump 心跳/doneSeq 递增/Cmd.result=1 已调用无 SEH，全部确认健康）
- parms 布局错（与 UFunction 参数偏移逐一核对）

**剩余假设（按可能性排序，接手从这里开始）**：
1. AttributeOwner 应指向属性集**实例**而非 Class（模板是 AsyncTask 上下文，照抄可能不对）
2. TFieldPath 的 RPC 序列化需要有效的 owner 弱句柄（抄来的指向 AsyncTask 的对象）
3. Method/Type 枚举有效值不在 0-3（UEnum::Names 偏移未逆向分析——对象 +0x30 的 TArray 读出的是邻居内存；暴力法：对象头 0x70 字节内每个 (ptr,count) 候选 × 元素尺寸 8/12/16/24 宽容解析）
4. 主机要求 GM/开发者标志（**先打 `ServerWHOSYOURDADDY()`（无参数）验证整个 Server* 族是否还被接受**——它上午 ServeraddATK 生效过，若 WYD 也无效=主机当天热更封了调试接口族）
5. 旁路：`ServeraddATK` 是否仍生效需新进程复测（观测=伤害数字，不是 GAS 值）

## S3.7 队列/RPC环 诊断手册（当前 xixing8.dll，源码 xixing_dll.cpp，VERSION 3；环布局自 xixing5 起含 tick 字段）

```
XIXING_SHARED_V1 (21160B):
  +0x00 magic 'XIXG' | +0x04 version=3 | +0x08 pending | +0x0C doneSeq
  +0x10 hookOk(=magic即武装) | +0x14 pump(心跳，活着就涨) | +0x18 logCount | +0x20 logFilterThis
  +0x28 Cmd cmds[16]  (stride 40: op,pad,this,func,paramsPtr,result)
     ★ result: 0=未处理 1=已调用 0xDEAD=SEH —— 排查第一步永远读这里
提交协议（xxstar.py fire()）：slot=(doneSeq+pending)%16 → 写槽 → pending+1
  paramsPtr = VirtualAllocEx 的游戏内缓冲（UE4 FString 等参数必须指向游戏内存）

XIXING_RPC_V1 (24+512×144):
  +0x00 'XIXR' | +0x08 count | +0x10 rpcFilterThis（0=不过滤）
  条目 stride 144: funcPtr, thisPtr, tick(u32), parms[124]
  ⚠️ 只记录组件 RPC（UActorComponent::CallRemoteFunction）——pawn/actor 的 RPC 不经过它，永不进环
  ⚠️ overlay 会把 filter 设为 ASC 指针（只记 GAS 流量）——通用调试前先写 0 清掉
```

**进程注意**：tasklist 里有两个 GhostHunter——启动器(28MB) + 本体 `-Win64-Shipping`(1.5GB+)，`/FI` 精确匹配本体。游戏当天重启 3+ 次：**一切进程内状态（注入、队列、全部堆地址）随重启清零**，overlay.exe 自动重连注入（pid 变化检测）。

## S3.8 GNames 枚举技术（名字↔索引互查）

- 块指针 @ `gnames+0x10+b*8`，块 0x20000B，条目 2 字节对齐，头 u16：`wide=bit0, len=>>6`
- 顺序扫全表可得 名字→FName index 映射：`index = (block<<16)|(offset/2)`
- 用途：构造 FString/FName 参数、全游戏名字搜索（曾用它找属性名候选）

---

## ⭐ 隔空摸容器（2026-09-21 session 2 已全部打通）

**目标**：按指定键 → 全图金色+红色容器主机侧打开（真实掉落）→ 掉落物自动拾取到脚底。

**已验证配方（全链路实测）**：
```
开箱 = IS_Interact.ServerSetPreBeInteractComponent(箱组件)      [客户端→主机 RPC，经游戏线程队列]
     + ASC.ServerTryActivateAbility(交互技能句柄, InputPressed=1) [GAS 技能激活]
     → 主机验证 → 通道 ~5-6s → 箱开(state=3) → 真实掉落物生成 ✅
自动拾取 = pawn.Server_RequestPickupByUI(掉落物actor, 0, 0)          [客户端→主机 RPC] ✅
传送 = pawn.K2_SetActorLocation(掉落物, 脚底坐标, bTeleport=1)    [视觉用，可选] ✅
```

**关键机制（这一天踩出来的全部真相）**：
- 交互系统 = GAS（Gameplay Ability System）。F 键 = native 输入处理器 → 组件 RPC `ServerTryActivateAbility`，**不走 ProcessEvent、不走 AActor::CallRemoteFunction**，只走 `UActorComponent::CallRemoteFunction`（虚表[76]覆写，module+0x27EEB30）
- RPC 方向语义（UFunction flags @+0xB0 低 dword）：**0x00200000=NetServer（客户端可调→主机执行）**、0x01000000=NetClient（主机→客户端）、0x4000=Multicast、无 net 标志=纯本地
- 交互技能**句柄每局漂移**（GAS 授予顺序）：见过 0x53/0x55/0x7E。**校准**：xixing3.dll 的 RPC 记录仪 + 玩家按一次 F → `xxcalib.py` 提取（规则：TryActivate 且 InputPressed=1）
- 箱型差异：竹编立柜族 F = 纯 TryActivate；match-A 某箱型出现过 `TryActivateAbilityWithEventData(0x6E/0x31)` 变体（带 EventTag）——重放基础版已足够
- pawn 是主机权威对象：客户端写 pre/curStart（复制属性）**不会**上行；真正上行的是 RPC

**工具链（tools/，纯 Python，无 CE 依赖）**：
- `uemem.py` — 纯 RPM 的 UE 反射读取器（pawn/世界/actor/UFunction/属性解析），`uemem.FORCE_PID` 可锁定进程
- `xxinject.py <pid> [dll]` — 注入器（ctypes，注意 GetProcAddress 必须设 restype=c_void_p 否则 64 位地址截断=游戏崩溃）
- `xxfire8/xxfinal.py` — 单箱重放（SetPre+TryActivate）验证用
- `xxcalib.py` — 从 RPC 记录环提取交互句柄 → xx_handle.txt
- `xxstar.py` — **完整隔空摸容器**：扫描全图金红箱 → 逐个 SetPre+激活（每箱 ~6s 通道）→ 逐个自动拾取掉落物
- `xxvacuum.py` — 独立自动拾取（逐个+活性检查）
- `xixing_watch/xxsnap*.py` — ProcessEvent 高频轮询监听/内存快照（分析用）

**xixing3.dll（overlay/build/，v3b）**：
- hook1: ProcessEvent（module+0x15E5170，12 字节签名校验跳转替换）= 游戏线程命令队列（共享内存 XIXING_SHARED_V1，v2 兼容布局）
- hook2: UActorComponent::CallRemoteFunction（module+0x27EEB30，13 字节跳转替换）= ASC RPC 记录环（独立段 XIXING_RPC_V1，512×{func,this,parms[128]}）
- v3b 可与已注入的 v2 **共存**（检测到 v2 补丁自动放弃 hook1，hook2 独立安装）
- 注入命令：`python tools/xxinject.py <pid> "overlay/build/xixing3.dll"`

## 掉落物品品质映射（实证，2026-09-21 收官验证）

**字节 @ 掉落物+0x4BA = 品质 + 2**（HANDOFF 旧表错位，以此为准）：
```
字节 2=白  3=蓝  4=紫  5=金  6=红   （c6=红由"透雕云龙双凤耳熏炉"实证）
```
- 掉落物名字 FText（+0x460）是已知的 PUA 乱码变体，读不出 —— 用品质字节筛选
- 传送掉落物（K2_SetActorLocation）是**纯客户端视觉**：重连后物品回原位（Ethan 实证）→ 永久废弃
- 背包负重大限时拾取失败，掉落物留原地 —— 属正常游戏行为

## 最终使用流程（用户确认的交付形态：走到哪吸到哪）

```
进对局后：
1. xxstart.bat           # 一键：ESP overlay + DLL注入 + 记录仪
   （或手动：start overlay\build\overlay.exe 然后python tools/xxdeploy.py）
2. 玩家按F开任意一个箱（本局校准源，句柄每局漂移）
3. 走到目标区域 → python tools/xxstar.py   # 吸当前气泡：验证句柄→开全部金红箱
                                           # →红金掉落物进背包
   （可反复在不同区域运行；怪物仇恨期间手动手持黑驴蹄按住左键）
4. F9 = 一键增伤（ServeraddATK 99999，~26000/击小怪一刀秒，session 3 交付）
   M4 = 隔空摸容器启动 | M5 = 中止 | F2 = ESP 品质过滤 | END = 退出
```
- ESP overlay：F2 切品质过滤，END 退出（F5/F6/F8 旧方案已拆除，2026-09-21）
- xxstar 每次运行自动验证句柄（拿最近的金红箱试开，失败自动试下一候选）
- 摸容器=罚站不能动（用户情报）：脚本期间玩家保持静止，通道期间移动=主机取消

## 怪物功能状态（2026-09-21 session 3，详见顶部第三session章节）

**F9 增伤已交付**（ServeraddATK 99999 → ~26000/击一刀秒）。吸怪/肥胶囊死路（机制见 S3.2/S3.3）。GAS 属性系统 + 后门函数清单已完全解剖（S3.4/S3.5），ServerAddAttribute 通用属性调试接口攻坚至一半（S3.6）。

**玩家自带潜行系统**（未自动化）：pawn 上 EnableStealth@+0x691（复制状态）、OnStealthSwitch/OnStealthCancel@+0x670/680 委托、HideCount@+0x1C9C；函数族 KxCharacter::SetStealth（local）+ SetStealthMaterial + HideWeapon。

**黑驴蹄（清仇恨道具）**：手持按住左键，每秒清除 810m 内鬼怪仇恨。技能句柄已抓（0x77，跨对局稳定）。**裸 RPC 激活 = 崩溃**（崩因#8：道具驱动技能需要本地手持上下文+预测数据，绕过即内伤延迟死 —— 与输入驱动技能[交互]本质不同）。**实战方案：跑 xxstar 时手动手持+按住左键**。

**未探路径**（按优先级）：`ServerWHOSYOURDADDY`/`ServerResetCD` 无参调试接口实测（验证调试接口族存活+无敌/CD清零直接可用）→ ServerAddAttribute 剩余假设（3.6）→ 近战触及距离（技能实例/蒙太奇 notify 半径，GAS 无此属性）→ PDC_CheckDamagePredicted_NoAdd 闸门 patch → bIsHideInShelter（pawn+0x1101）、阵营系统（CampProfile@+0xFE0）。

## ESP/开箱覆盖范围

重度流送地图（~193 流送关卡）：**只加载玩家周围气泡**，远处容器 actor 客户端未实例化（xxstar 只能开气泡内的）。全图方案 = ServerTeleport（NOS_PlayerHuman 上有此 C->S RPC）逐区域瞬移+等流送（10-30s/区域）+ 开箱 —— v2 功能。

## 掉落物价值字段

DTItemData 内联 @ 掉落物+0x450：ItemId@+0x458、Name@+0x460（PUA 乱码不可读）、品质@+0x4BA。价值字段在 +0x458..0x4BA 之间的 0x60 字节内未定位（用户可对着游戏标价指认，如熏炉=110 万）。

## 🚫 崩溃黑名单（血的教训，勿再犯）

1. **队列调 TryInteractComplete**：合成调用破坏 RoleSign 状态 → SEH 风暴 → 延迟崩溃
2. **强杀 CE（调试器挂着游戏时）**：残留 INT3 无人处理 → 游戏陪葬。必须先 Lua 移除全部断点
3. **CE 断点跨游戏重启残留**：进程死后 CE 的 bp 列表还在，新进程一 arm 就全打上 → 冻结。每次 arm 前清 XXTRACE/XXRPC 全家
4. **CE 断点打在组件 RPC 热路径**（UActorComponent::CallRemoteFunction）：VEH 往返饿死输入线程 → "能按 ESC 但不能动"。**结论：不再用 CE 断点，全部走进程内 hook（xixing3.dll）**
5. **连发引用已销毁 actor**：拾取 RPC 会立刻销毁掉落物，连发批量命令后面的引用已释放内存 → 崩溃。必须逐个+活性检查
6. **ctypes GetProcAddress 不设 restype**：地址截断 → 远程线程跳垃圾地址 → 随机崩溃（曾误判为游戏不稳定）
7. overlay 挂着僵尸游戏进程时新 DLL 重武装共享块 → overlay 用旧进程的 paramsBase 指针发命令 → 新进程解引用崩溃（跨进程污染；overlay 需加 PID 校验）
8. **道具驱动技能的裸 RPC 激活**（如黑驴蹄 TryActivate）：缺手持上下文/本地预测 → SEH 内伤 → 延迟崩溃。输入驱动技能（交互）则完全安全
9. **崩溃会话的队列残留**：崩溃时压在队列里的命令下局被排空 → 死指针 → 崩（已修：xxdeploy.py 部署时强制清零 pending）
10. **裸写胶囊 float（session 3）**：碰撞形状构造时已烤入物理引擎，裸写 CapsuleRadius 受击判定不变；但 CapsuleHalfHeight 被客户端贴地逻辑实时读 → 怪悬空抽搐（不崩溃但画面破坏）。正确姿势=队列调 SetCapsuleSize 让引擎重建形状
11. **硬编码堆地址跨会话/跨对局使用（session 3）**：FProperty 节点、属性集实例、UFunction、AsyncTask 内部布局——每次游戏重启+每局都变。全部必须现场反射解析（SpawnedAttributes/类链 ChildProperties/模板扫描），attrfire.py 是全动态实现的范本
12. **UE4 字符串参数是 UTF-16（TCHAR=wchar_t）**：构造 FString 参数（RPC parms）必须写宽字符——ASCII 单字节=主机收到乱码名静默忽略（session 3 用模板原始字节 `43 00 75 00...` 实证）


## 一、项目范围与边界

**目标**：对 `GhostHunterClientSteam`（魔改 UE4.27 的中文抓鬼题材 PVE 游戏）做**读写**内存分析 + 进程内 hook：
在屏幕覆盖层上显示高品质战利品容器/矿石/采集物的 ESP（方框 + 名字 + 距离），
并通过重放客户端→主机 RPC 实现开箱、拾取、增伤。

> ⚠️ **2026-09-22 订正**：本节旧版写着"写入接口尚未接入 / 不做任何写入 / 此边界不可越过"。
> **那句话已完全失效，勿再引用。** 写入早就接上了（`mem.h:52 write()` 被 `xixing.cpp:109` 调用），
> F9 增伤、自动交互开箱、attrfire 写属性全是写操作。照旧边界做判断会得出完全相反的结论。
> （`mem.h:2` 的注释同样陈旧，勿引用。）

**当前实际在做的写操作**：
- 注入进程内 hook DLL（ProcessEvent + `UActorComponent::CallRemoteFunction` 双钩）
- 经游戏线程队列重放 C→S RPC：开箱 `ServerSetPreBeInteractComponent` + `ServerTryActivateAbility`、
  拾取 `Server_RequestPickupByUI`、增伤 `ServeraddATK`
- `WriteProcessMemory` 写 RPC 参数缓冲、`VirtualAllocEx` 在游戏进程内分配
- `tools/fatcapsule.py` 写怪物碰撞胶囊尺寸（实验，已判死路，见 S3.3）
- `tools/attrfire.py` 写 GAS 属性（`ServerAddAttribute` 攻坚中，未打通，见 S3.6）

**机制性死路（已实测，勿重试）**：搬动怪物/掉落物（主机权威，S3.2）、
裸写胶囊改受击判定（碰撞形状生成时已烤入物理引擎，S3.3）、传送掉落物（纯客户端视觉，重连复原）。

**ESP 侧**：默认显示 金+红 品质；F2 循环切换；END 退出。
掉落物（BP_DropInteract_C）不做 ESP（游戏内自带发光，用户明确不要）。

**交付物**：`overlay\build\overlay.exe` —— 零依赖单文件，双击即用，自动识别游戏并自动注入 DLL。

## 二、环境

| 项 | 状态 |
|----|------|
| 游戏进程 | `GhostHunterClientSteam-Win64-Shipping.exe`（注意：另有同名启动器进程 `GhostHunterClientSteam.exe`，attach 必须精确匹配本体，否则抓错进程） |
| 游戏窗口 | 类名 `UnrealWindow`，全屏 2560x1440 |
| 游戏架构 | 主机权威、双精度坐标（LWC-style）、多结构反 dump 魔改、CPAD 加固节 |
| CE MCP | 管道 `\.\pipe\CE_MCP_Bridge_v99`；直连客户端 `tools/ce.py`（零依赖，`python ce.py <method> '<json>'`）；已注册到 `.zcode/config.json`（mcp<2 + pywin32） |
| 编译 | `overlay/build.bat`：MSVC 14.44 (VS2022 BuildTools) + Win SDK 10.0.26100 + `/utf-8 /std:c++20`，产出 `build/overlay.exe` |

## 三、UE4 逆向成果（核心资产，全部实测）

### S3.1 模块相对偏移（game.exe 基址 + 偏移）

| 全局 | 偏移 | 说明 |
|------|------|------|
| GNames (FNamePool) | +0xAFC9F40 | +0x10 起 Blocks[8192]，块大小 0x20000 |
| GWorld（活跃） | **+0xB11AB68** | **重进对局后只有这个会更新** |
| GWorld（次要） | +0xB117F20 | 会过期悬空，仅作 fallback（两者都读，取 PersistentLevel 有效的） |
| GEngine | +0xB11DB40 | |

### S3.2 魔改布局（与原版 UE4.27 不同！）

- **FNamePool 条目头 2 字节**：`(len << 6) | (hash << 1) | wide`（原版是 `(len<<1)|wide`）
  - FName index → 字节地址：`block = idx>>16`，`offset = (idx&0xFFFF)*2`；条目 = 头 2B + 字符
- **UStruct**：SuperStruct@+0x40、Children@+0x48、ChildProperties@+0x50、Size@+0x58
- **FProperty(FField)**：ClassPrivate@+0x08、Next@+0x18、FName@+0x20(idx u32)、ArrayDim@+0x30、ElemSize@+0x34、**Offset_Internal@+0x44**；FStructProperty 的 UScriptStruct*@+0x70
- **UObject 标准**：vtable@0、Flags@8、InternalIndex@C、**Class@+0x10、NameFNameIdx@+0x18(u32)**、Outer@+0x20
- **TArray**：{ptr, num32, max32} 16 字节
- **FText**：{TextData*}；TextData+0x20 → FString{ptr,num,max} → UTF-16LE 字符
  - 注意：存在其他 FText 变体（掉落物名字变体读出来是 PUA 乱码，已在代码里过滤）

### S3.3 世界链路（本游戏偏移）

```
GWorld → World+0x30 PersistentLevel (ULevel*)
       → World+0x90 StreamingLevels (TArray, ~193 个，对局容器大部分在流送关卡里!)
       → World+0x228 OwningGameInstance
ULevelStreaming+0x190 → LoadedLevel (ULevel*)
Level+0xA0 → Actors (TArray<AActor*>)
Actor+0x1B8 → RootComponent
SceneComponent+0x110 → Bounds.Origin（3×double，世界坐标，已含流送关卡变换——用它！）
                   ⚠️ +0x148 RelativeLocation 恒为 0，勿用
```

### S3.4 相机链路（反射按属性名查找，免疫偏移变化）

```
World+0x228 GameInstance → 类链反射找 "LocalPlayers"(+0x38, TArray)
→ LocalPlayers[0] → 类链找 "PlayerController"(+0x30)
→ PlayerController → 类链找 "PlayerCameraManager"(+0x360)
→ PCM → 类链找 "CameraCachePrivate"(+0x15D0) → 结构内 "POV"(+0x10)
→ FMinimalViewInfo: Location@+0x00 (3×double), Rotation@+0x18 (3×double), FOV@+0x30 (float)
```
⚠️ PCM 指针会悬空（切图后）→ POV 读出 **NaN**（NaN 在 C++ `||` 里是真值，会骗过有效性检查！必须用 `std::isfinite` 判断）。坏相机 0.5s 后自动丢弃重找（已实现）。

### S3.5 品质体系（核心机制，用户目视确认）

调色板（交互组件 IS_BeInteractComponent+0x7B0 Text TMap / +0x800 Color TMap，键=FName "Default","1","2","3","4"）：
**Default=白(1,1,1)、1=蓝(0.41,0.67,1.0)、2=紫(0.47,0.34,0.63)、3=金(0.81,0.74,0.33)、4=红(0.86,0.32,0.31)**。最高就是红，没有更高档。

**三类目标，三种品质来源：**

| 家族 | 类 | 品质来源 | 名字来源 |
|------|----|---------|---------|
| TB 容器 | BP_YiGui_C / BP_HeZi_C / BP_BaoXiang_C（→BP_TreasureBoxBase_C→NOS_BaoXiang） | **名字↔品质一一对应**（硬编码表） | +0x450 FText（品质已解析的显示名） |
| 矿石/采集 | BP_Mining_C / BP_Collect_C（→BP_ToolBase_C） | **SpawnIndex@+0x310 − 1**（1白2蓝3紫4金5红） | 交互组件 TEXT map 元素[SpawnIndex] |
| 掉落物 | BP_DropInteract_C（→BP_DropItem_C） | DTItemData.ItemColor@+0x4BA | **不做 ESP（用户要求）** |

- TB 容器 +0x460 的 Color40 是**型号索引不是品质**（曾误判，勿回退）
- TB 容器 State@+0x2B8：**3 = 已被开过**（过滤掉）
- 开箱后 2 秒内从 ESP 消失（缓存刷新周期）

**已确认名字→品质表（用户目视确认，共 9 个）：**
```
金：金丝楠木箱柜、鎏金兽首百宝箱、紫漆牡丹箱
红：花梨木龙纹箱、赤血龙木柜
紫：朱漆立柜、雕花木箱、压花皮箱
白：老榆木箱（不显示）
```
**待确认（看到问用户）**：竹编立柜、老旧木箱、朱砂封符木箱、紫檀素面箱、铁包角木箱、旧漆木匣、黑胡桃木箱、旧皮箱、胡桃木箱、樟木箱。未确认名字默认白色（被金/红过滤器隐藏，安全）。

**各家族名字表（TEXT map 里的完整列表，品质序）**：
- YiGui：竹编立柜/朱漆立柜/金丝楠木箱柜/赤血龙木柜
- BaoXiang：老旧木箱/朱砂封符木箱/雕花木箱/鎏金兽首百宝箱
- HeZi：紫檀素面箱/铁包角木箱/压花皮箱/老榆木箱/旧漆木匣/黑胡桃木箱/紫漆牡丹箱/胡桃木箱/樟木箱/…/花梨木龙纹箱(第11档)
- 矿：青辉杂石矿/翠晶簇生矿/幻彩幽髓晶矿/鎏金玄髓精矿/血髓玄魄神矿
- 灌木：素白野山花灌木/凝霜簇花灌木/绯云灵蕊灌木/赤霞仙蕴灌木/朱焰血珠灵丛
- ⚠️ YiGui 的型号序恰好=品质序（1-4），但 BaoXiang/HeZi 不是（雕花木箱第3档=紫、鎏金兽首第4档=金）——别用型号推导品质

### S3.6 世界生命周期（踩坑重点）

- 玩家流：大厅 → **Maincity_01（主城）** → JN_TownMain（对局图）。主城无战利品容器（boxes:0 正常）
- **同图重进：world 指针变但地图名不变** → 必须用指针变化检测（不能用名字），并重置：类指针缓存（UClass 对象每局重建！）、名字缓存、相机缓存
- 进图后容器/相机就绪需 10~30 秒（流送加载），期间只有 HUD 是正常现象

## 四、overlay 代码结构（overlay/src/）

| 文件 | 职责 |
|------|------|
| `mem.h` | 读写内存访问层（当前读取路径基于 ReadProcessMemory）；attach 按完整进程名匹配（含 EnumProcessModulesEx 取基址） |
| `ue4.h/cpp` | 数据层：fname 解析、反射查找(findField/findFieldInStruct)、pollWorld（指针变化检测+1s 关卡刷新）、getCamera（反射链+NaN 防护+自愈）、fullScan（2s 缓存，TB/Tool 两族，名字缓存重试不设死节点）、名字→品质表 |
| `main.cpp` | 呈现层：分层窗口（WS_EX_LAYERED\|TRANSPARENT\|TOPMOST\|NOACTIVATE，UpdateLayeredWindow+预乘alpha）、GDI+ 绘制、W2S（UE rotator 双精度）、视觉去重（按距离排序，22px 内只画最近）、F2/END 热键、单实例互斥锁、HUD（左下角+底板） |

**性能架构**（曾踩 64fps 坑）：全量扫描 2s 一次、关卡列表 1s 一次、每帧访问相机（~8 次内存操作）+绘制。2560x1440 下 ~144fps。

**渲染关键**（曾踩"颜色重叠"坑）：**必须**用 `Gdiplus::Bitmap(w,h,stride,PixelFormat32bppARGB,bits)` 包装 DIB 内存创建 Graphics；用 DC 创建的 Graphics 会把 DIB 当 32bppRGB，alpha 字节全是垃圾 → UpdateLayeredWindow 混合出重影。绘制后手动预乘 alpha。

## 五、构建与迭代流程

```bash
# 修改代码后：
cd overlay && cmd //c build.bat     # ~10 秒
taskkill //IM overlay.exe //F && start build/overlay.exe

# 实时查看 overlay 状态日志：
type overlay\overlay.log
# 关键日志行：attached / world changed / camera ok, containers=N / no world (lobby)

# 补充品质表：编辑 ue4.cpp 顶部 gNameQuality[] 数组 → 重新编译
```

**CE 查询（通过管道）**：
```bash
python tools/ce.py read_memory '{"address": "0x7FF7AC81AB68", "size": 8}'
# 复杂扫描用 evaluate_lua（Lua 脚本模板见 tools/*.lua）
```

## 六、踩坑清单（接手必读）

1. **CE evaluate_lua 的 Lua 源码不能含中文**（CE 端 JSON 解析直接 Parse error）——注释和字符串必须 ASCII
2. GWorld 有两个全局，重进对局后 +0xB117F20 悬空不更新，用 +0xB11AB68
3. 进程有两个（启动器/本体），名字匹配必须含 `-Win64-Shipping`
4. NaN 是真值——相机/位置有效性判断必须 isfinite
5. UClass 对象每局重建，类指针缓存必须在世界变化时重置
6. TB 的 Color40(+0x460) 是型号索引；Tool 家族品质 = SpawnIndex−1（元素序号=品质+1）
7. 主城（Maincity_01）没有容器，boxes:0 正常
8. 游戏读取偶发瞬断（切图/GC），所有内存读取要有重试（tools/ue.py 的 read 已带）
9. 游戏品质封顶红色；"神级"档已移除（曾有，清理于 2026-09-21）
10. GDI+ 画 DIB 必须走 ARGB Bitmap 包装（见 S3.6/四）

## 七、待办（按优先级，2026-09-21 session 3 更新）

**怪物/属性线（当前主线）**：

> ⚠️ 下面第 2 条（ServerAddAttribute）**先别做**。`TODO-FIX.md` 证明命令队列会静默丢命令
> 且 `attrfire.py` 从不回读 `result`——那 8 发矩阵的 "no effect" 结论不可采信。
> 顺序应为：`TODO-FIX.md` 任务 0～4 → 再回来做第 2 条。

1. 实测 `ServerWHOSYOURDADDY()` / `ServerResetCD()`（无参，队列直调）——一步验证调试接口族是否存活；无敌直接解决"僵尸骚扰"（攻击/CD）
   （**加上 `result` 回读后再打**，否则分不清"没执行"和"主机拒绝"，见 `TODO-FIX.md` 任务 0）
2. `ServerAddAttribute` 剩余假设（S3.6 清单）：owner 用实例指针试一次 → UEnum 值暴力逆向分析 → ServeraddATK 新进程复测（观测=伤害数字）
3. 组合打通后批量打 OpenSpeed/AttackSpeed/ZengShangPercent/FinalCoefficient → overlay 热键化（F10 一键全家桶）
4. 近战触及距离：技能实例/蒙太奇 notify 半径（GAS 无此属性；ASC.ActivatableAbilities@+0x418 属性已定位但 FGameplayAbilitySpec 步长未破——0x90 无效，需暴力扫 Items 缓冲区找合法 ability 指针）
5. PDC_CheckDamagePredicted_NoAdd（预测伤害闸门）patch 研究

**ESP 线（旧）**：
6. 补品质表：剩 10 个名字（见 3.5），用户看到会报——收到后改 `gNameQuality[]` 重编译
7. （可选）进图加载期提高扫描频率；旋转柜家族（~55/局）Text/Color 空，需调研初始化时机
8. （可选）ServerTeleport 全图开箱（v2 功能，见"ESP/开箱覆盖范围"）

## 八、文件清单

```
\
├── HANDOFF.md          ← 本文档（自包含交接）
├── NOTES.md            ← 逆向笔记（更细的原始记录，含早期探索过程）
├── xxstart.bat         ← 一键启动：overlay.exe + xxdeploy.py（进对局后跑）
├── auto-interact.bat            ← 一键跑 tools\xxstar.py（吸当前区域）
├── .zcode\config.json  ← CE MCP 注册（CE 时代遗产）
├── overlay\
│   ├── build.bat           ← 编译 overlay.exe
│   ├── build_dll.bat / build_dll3.bat / build_dll4.bat / build_dll6.bat ← 各代 DLL 编译脚本
│   ├── build_inject.bat    ← 编译 inject.exe
│   ├── overlay.log         ← overlay.exe 的日志
│   ├── xixing.log          ← **DLL 自己的日志**（和上面不是一个文件！排查 hook/队列/SEH 看这个）
│   ├── build\overlay.exe   ← 交付物（ESP + M4自动交互 + F9增伤热键）
│   ├── build\inject.exe    ← 独立注入器
│   ├── build\xixing.dll    ← 一代（09-21 06:55，历史）
│   ├── build\xixing3.dll   ← 三代（09-21 10:23，历史）
│   ├── build\xixing4.dll   ← 历史版本（09-21 12:02，v4 无 tick 环布局）
│   ├── build\xixing5.dll   ← 历史版本（09-22 03:50，环布局改为含 tick）
│   ├── build\xixing6.dll   ← 历史版本（09-22 05:01）
│   ├── build\xixing7.dll   ← 历史版本（09-24，游戏更新前最后一代）
│   ├── build\xixing8.dll   ← **当前版本：overlay.exe 硬编码注入的就是它**（xixing.cpp:17 DLL_PATH），
│   │                          含 2026-09-24 新偏移（PE +0x15E51E0 / CRF +0x27EEBA0）
│   └── build\CURRENT_DLL.txt ← 当前 DLL 名标记（改 DLL 版本时必须同步更新！）
│   └── src\{main.cpp, ue4.cpp/h, mem.h, xixing.cpp/h, xixing_dll.cpp, inject.cpp}
│         xixing_dll.cpp = DLL 源码（Shared/RpcShared 布局的唯一权威定义）
├── tools\              ← 全部纯 Python（ctypes），无 CE 依赖
│   ├── uemem.py        ← RPM 反射读取器（pawn/世界/actor/UFunction/属性；FORCE_PID 锁进程）
│   ├── xxstar.py       ← 完整隔空摸容器（含 fire() 队列提交协议——唯一权威实现）
│   ├── xxdeploy.py     ← 一键部署（找pid→注入→等pawn→武装记录仪→清残留队列）
│   ├── xxinject.py     ← DLL 注入器（GetProcAddress 必须 restype=c_void_p）
│   ├── attrfire.py     ← ServerAddAttribute 攻坚（UTF-16/全动态/自验证矩阵）★session 3
│   ├── matchprobe.py   ← 对局探针（等pawn→胶囊残留检查→pawn类函数dump）
│   ├── dump_pawn_fns.py    ← pawn 类函数全量 dump（出土后门函数清单）
│   ├── dump_rpc_params.py  ← UFunction 参数布局/属性集/模板挖掘
│   ├── fatcapsule.py / capwatch.py ← 肥胶囊实验（死路留档，含 one-shot/--watch）
│   ├── xxcalib.py      ← RPC 环提取交互句柄 → xx_handle.txt
│   ├── xx_handle.txt   ← xxcalib.py 输出：本局交互技能句柄（每局漂移，不可跨局复用）
│   ├── func_scan_result.txt ← pawn 类函数 dump 结果
│   ├── xxfire*.py / xxfinal.py / xxvacuum.py / xxsnap*.py ← 阶段性实验（历史）
│   ├── ce.py / ue.py / lua_run.py / *.lua ← CE 时代遗产（现全部走 DLL，仅留档）
│   └── screenshot.py   ← 截屏
├── sdk\                ← HerculesPlugin：游戏所用反作弊插件的完整 SDK（UE 插件源码+各平台库，逆向参考材料）
└── tmp_*.txt           ← 分析过程临时数据（可删；tmp_matchprobe.txt=session3 对局dump）
```

⚠️ **DLL 版本陷阱（2026-09-22 发现；2026-09-24 已修复——CURRENT_DLL.txt 标记 + DLL_PATH 同步更新，现为 xixing8）**。以下为当时的历史记录：
`xixing.cpp:17` 写死注入 `xixing4.dll`（09-21 12:02），而源码 `xixing_dll.cpp` 已改到 09-22 05:01、
编译产物是 `xixing6.dll`。overlay.exe 09-22 06:51 重编过，**仍然指向 dll4**。
结果：overlay 自动注入旧 DLL，手工 `xxinject.py` 注入新 DLL，**两代在同一进程里都装过**
（`xixing.log` 实证：t=195711312 装了 9 次新的，t=199379390 之后又装了 18 次旧的）。

`xixing.log` 里不打版本号，只能靠字符串差异分辨：
带 `(v4-proven)` 字样 = dll5/6，不带 = dll4。
**改 DLL 后必须同步改 `xixing.cpp:17` 再重编 overlay**，否则改动根本不会上线。
