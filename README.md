# 《驱灵天师》客户端安全研究

[English version](README_EN.md)

针对《驱灵天师》（`GhostHunterClientSteam`，魔改 UE4.27 的抓鬼题材 PVE 网游）的客户端侧安全研究工具链与发现汇总。研究手段为**只读内存分析 + 进程内函数调用（RPC 重放）**，用于评估客户端暴露面与服务器端校验强度。

> 本仓库面向官方漏洞修复提交。所有发现均已在实机验证，附复现工具与修复建议。

## 时间线

| 日期 | 事件 |
|---|---|
| 2026-09-21 ~ 09-22 | 初始研究期：开发者测试 RPC 全族可用（攻击力 / 生命值 / 物品 / 无敌 / CD / 传送） |
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
5. **反 farm 规则过弱** —— "连续约 8 次交互不打怪踢出"可通过穿插一次攻击轻易绕过。

### 修复建议

- **服务器端**：交互行为模式分析（频率、路径连贯性、目标分布）；拾取距离与频率校验。
- **客户端**：关键内存数据混淆/加密（至少延迟静态偏移分析）；注入与 inline hook 检测；移除或按构建期裁剪调试函数与测试层。

## 仓库结构

```
├── README.md            ← 本文档
├── HANDOFF.md           ← 完整技术知识库（偏移表/协议/机制/死路清单，588 行）
├── USAGE.md             ← 工具使用说明
├── overlay/             ← C++ 工具（分层窗口 ESP + 自动交互管线 + 注入 DLL 源码）
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
3. 对局内: F2 过滤 / M4 自动交互管线 / 热键见 USAGE.md
4. 游戏更新后: python tools/offset_hunt.py  (约 10 分钟重定位全部偏移)
```

## 免责声明

本项目仅用于安全研究与向官方提交漏洞修复参考。请勿用于破坏游戏公平性或任何违反游戏服务条款的用途。
