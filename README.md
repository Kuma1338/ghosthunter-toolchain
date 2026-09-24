# 《驱灵天师》客户端安全研究

[English version](README_EN.md)

针对《驱灵天师》（`GhostHunterClientSteam`，魔改 UE4.27 的抓鬼题材 PVE 网游）的客户端侧安全研究工具链与发现汇总。研究手段为**只读内存分析 + 进程内函数调用（RPC 重放）**，用于评估客户端暴露面与服务器端校验强度。

> 本仓库面向官方漏洞修复提交。所有发现均已在实机验证，附复现工具与修复建议。

## 时间线

| 日期 | 事件 |
|---|---|
| 2026-09-21 ~ 09-22 | 初始研究期：后门 RPC 全族可用（攻击力 / 生命值 / 物品 / 无敌 / CD / 传送） |
| 2026-09-24 | 官方发布更新：上述全部封堵（本工具链在同日验证了修复效果，见下表） |

## 发现汇总

### 一、已被 2026-09-24 更新修复的问题（已验证）

| 漏洞 | 原理 | 验证方式 |
|---|---|---|
| 开发者后门 RPC 族 | 客户端编译内置 `ServeraddATK / ServeraddHP / ServerWHOSYOURDADDY / ServerResetCD / ServerAddItem / ServerTeleport` 等 14+ 个调试 RPC，旧版服务器直接执行 | 全参数矩阵实测 + `ActiveCustomTest` 门控组合测试，现全部无效 |
| 本地调试函数 | `ShowCheatPanel / GM / AddFullHealth / SkillNoCDMode` 等本地函数空转（权限标志未激活） | 队列直调，全部无效果 |
| 通用命令通道 | `ServerExecRPC(FString Msg)` 命令字符串入口，CheatManager 级命令被服务器特权过滤 | Slomo/Disconnect 无效果 |
| 主城测试层 | `Debug_AddItemToDepotBackpack / RandomConstructTestData`（物品表随机抽取→PB 发送） | 直调+命令串双路，零产出 |

### 二、仍然存活的问题（建议官方关注）

1. **敏感数据客户端明文** —— `GNames`（名字池）、`GWorld`、actor 结构、容器品质标记在客户端内存中完全可读且结构稳定，ESP（方框透视）零门槛实现。本次更新后偏移仅整体平移（代码段 +0x70 / 数据段 +0x1000），重定位成本约十分钟。
2. **交互 RPC 无行为校验** —— `ServerSetPreBeInteractComponent` + `TryActivateAbility` 可被脚本化批量触发（自动开箱/采矿/采集），服务器仅校验距离（约 70-90m），无频率与行为模式分析。
3. **拾取 RPC 无距离/频率校验** —— `Server_RequestPickupByUI` 可对范围内掉落物逐个批量调用。
4. **反作弊（Hercules）检测盲区** —— DLL 注入（CreateRemoteThread + LoadLibraryW）、`ProcessEvent` inline hook（12 字节跳转替换）、共享内存命令队列全程无告警。
5. **反 farm 规则过弱** —— 短时间内（估计 1~2 分钟，未精确测定）连续交互约 8 次即被踢出对局。疑似简单的时间窗计数，无更细的行为模式分析；打怪或其他行为是否重置计数**均未经验证**。


### 三、研究过程中发现的其他客户端内容（供官方排查参考）

以下内容在 2026-09-24 更新后均已被封堵或空转，但**相关代码仍编译在正式客户端中**，建议从构建中彻底移除：

| 发现 | 说明 |
|---|---|
| GM 面板函数 | `ShowCheatPanel`（本地函数；pawn 上有 `CheatPanel` 挂点但从未创建实例）——被未激活的权限标志门控 |
| 完整 CheatManager 类 | UE 原生作弊管理器类完整编译在客户端，含 57 个函数（`God / Fly / Ghost / Teleport / Summon / Slomo / ChangeSize / PlayersOnly` 等），运行时实例为空但类对象可达 |
| 服务器→客户端作弊命令处理器 | `ClientCheatFly / ClientCheatGhost / ClientCheatWalk` 存在于 pawn 上 |
| 测试模式门控函数 | `ActiveCustomTest`（C→S，无参，疑似"自定义测试模式"开关——服务器已忽略） |
| 通用命令字符串通道 | `ServerExecRPC(FString Msg)`（PC 层） |
| 主城测试层 | `Debug_AddItemToDepotBackpack`（往仓库加物品）、`RandomConstructTestData`（从物品表随机抽取 N 件 → 构造 PB 数据发送）、`SendTestPBData_01` |
| 编辑器/调试残留 | `EditorCheatSpawnGhost`（生成鬼怪）、`GM`（本地函数）、`Server_SimCrash`、`ServerKillSelf` |
| 开发者后门 RPC 全家族 | 14+ 个（`ServeraddATK / ServeraddHP / ServerWHOSYOURDADDY / ServerResetCD / ServerAddItem / ServerAddMoney / ServerAddSoul / ServerAddLingBi / ServerTeleport` …），完整签名见 HANDOFF.md S3.4 |
| GAS 属性系统全表 | 100+ 个属性（增伤% / 近战·远程·AOE 系数 / 攻速 / 开箱速度 / 五行攻防 / 暴击全套）在客户端内存完整可读（HANDOFF.md S3.5） |
| ServeraddHP 语义 | 往血池直接加值——旧版一次调用 +200000 即血条翻倍，非"回满" |
| 交易行行情数据 | 仅存服务器端，客户端不落地（研究中全量扫描零命中） |

### 修复建议（逐条对应"仍然存活"的问题）

**1. 敏感数据客户端明文（ESP）**
- 服务器侧治本：未交互容器的品质数据**按需下发**（玩家靠近/注视时才同步），客户端内存中不再常驻全图品质标记
- 客户端侧缓解：关键全局（GNames/GWorld）做**每次启动随机化偏移**，品质标记加密存储——至少将静态偏移分析成本从"十分钟"提升到"每次启动重做"
- 已验证现状：客户端重编译后偏移仅整体平移（+0x70/+0x1000），特征码扫描 + 虚表引用计数 + 名字池内容扫描约十分钟内完成重定位（工具链见 `tools/offset_hunt*.py`）

**2. 交互 RPC 无行为校验（隔空摸容器）**
- 服务器端校验玩家位置与交互目标距离（现有约 70-90m 距离检查保留）+ **视角朝向校验**（玩家必须大致面向目标）
- 交互目标分布分析：短时间内对**同质目标集群**（同类容器/同类采集点）的批量交互标记为异常
- 单位时间交互次数上限收紧，并对同 IP/账号做滑动窗口统计

**3. 拾取 RPC 无距离/频率校验**
- 服务器端用**服务器记录的玩家位置**校验拾取距离（勿信任客户端上报位置）
- 拾取频率上限（如 ≤5 次/秒）+ 与移动轨迹交叉验证（连续拾取间隔小于玩家移动所需时间 = 异常）

**4. 反作弊（Hercules）检测盲区**
- 注入检测：监控 `CreateRemoteThread` + `LoadLibraryW` 组合调用；扫描进程内非签名模块
- **inline hook 完整性校验**：对 `ProcessEvent` / `CallRemoteFunction` 等关键函数的前 N 字节做周期性哈希校验（本研究使用的 12/13 字节跳转替换可被此法捕获）
- 命名内核对象扫描：非游戏创建的命名共享内存段（本工具链使用固定命名 `XIXING_SHARED_V1` 类命名段通信，极易扫描发现）

**5. 反 farm 规则过弱**
- 在时间窗计数基础上增加：交互**目标多样性**要求（连续同质目标计数权重放大）、移动轨迹交叉验证（目标间瞬移式切换 = 异常）
- 规则参数（窗口长度/次数上限）服务端热更新，避免被摸清固定值

**通用建议**
- 上节清单中的全部调试/测试内容（GM 面板函数、CheatManager 类、后门 RPC、主城测试层、编辑器残留）**按构建配置裁剪，从正式版中彻底移除**——运行时门控已被证明可被逐项绕过尝试（本研究对每层都做了验证）

## 仓库结构

```
├── README.md            ← 本文档
├── HANDOFF.md           ← 完整技术知识库（偏移表/协议/机制/死路清单，588 行）
├── USAGE.md             ← 工具使用说明
├── overlay/             ← C++ 工具（分层窗口 ESP + 隔空摸容器 + 注入 DLL 源码）
│   ├── src/             │   main.cpp / ue4.cpp / xixing.cpp / xixing_dll.cpp ...
│   └── build*.bat       │   MSVC 一键编译
├── tools/               ← Python 研究工具链（无第三方依赖）
│   ├── uemem.py         │   纯 RPM 的 UE4 反射读取器（魔改布局适配）
│   ├── offset_hunt*.py  │   游戏更新后的偏移重猎方法论（特征码/虚表计数/堆内容扫描）
│   ├── xxstar.py 等     │   交互管线复现脚本
│   └── dump_*.py        │   RPC 签名/属性系统 dump 工具
└── xxstart.bat          ← 一键启动
```

## 核心技术点（详见 HANDOFF.md）

- 该游戏 UE4.27 布局魔改：FNamePool 头格式 `(len<<6)|(hash<<1)|wide`、FField 字段偏移整体平移、双 GWorld 全局
- 偏移猎取方法论：ProcessEvent 特征码扫描 + 虚表引用计数消歧 + 名字池内容定位（引擎固定注册序 `None→ByteProperty→IntProperty`）
- 游戏线程命令队列：共享内存 16 槽 SPSC 环，DLL 在 `ProcessEvent` 钩子内于游戏主线程执行 UFunction 调用
- GAS 属性系统全表、`FGameplayAttribute` 56 字节结构、交互技能句柄的 flip-latch 捕获机制

## 复现

```
1. 编译:  cd overlay && build.bat          (MSVC 2022, 自动产出 overlay.exe + DLL)
2. 运行:  xxstart.bat                       (或手动 start overlay\build\overlay.exe)
3. 对局内: F2 过滤 / M4 隔空摸容器 / 热键见 USAGE.md
4. 游戏更新后: python tools/offset_hunt.py  (约 10 分钟重定位全部偏移)
```

## 免责声明

本项目仅用于安全研究与向官方提交漏洞修复参考。请勿用于破坏游戏公平性或任何违反游戏服务条款的用途。
