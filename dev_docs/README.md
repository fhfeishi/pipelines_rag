# 开发文档中心

维护日期：2026-09-16。当前整理范围为 static1；其他项目和仓库根目录的通用笔记暂不迁移。

## 从哪里读起

| 你要解决的问题 | 唯一维护入口 |
|---|---|
| 怎么安装、启动、使用 | [static1 用户README](../static1/README.md) |
| 下一步做什么、如何评价智能与控制成本 | [规划 roadmap](static1_plan/roadmap.md) |
| 哪些已做、哪些待办 | [进度 status](static1_plan/status.md) |
| 系统职责与整体工作流 | [HLD](static1_design/HLD.md) |
| 模块、数据、接口、状态与故障契约 | [LLD](static1_design/LLD.md) |
| 完整API、检索pipeline与上游设计映射 | [API_PIPELINE](static1_design/API_PIPELINE.md) |
| 当前代码结构、实现边界 | [implementation](static1_implement/implementation.md) |
| 做了什么、测试过什么、为何这样做 | [dev_logs](static1_implement/dev_logs.md) |
| 过去怎么分析的 | [历史原始笔记](deprecated/agentic-rag-static-history.md) |

## 目录结构

```text
dev_docs/
  README.md
  static1_plan/
    roadmap.md          下一阶段方案、实验、验收与回退
    status.md           当前进度和优先级
  static1_design/
    HLD.md              系统/应用架构
    LLD.md              模块/接口/数据/状态
    API_PIPELINE.md     API与工作流程、上游映射
  static1_implement/
    implementation.md   代码结构与实现边界
    dev_logs.md         按节点追加的执行证据
  deprecated/
    README.md
    agentic-rag-static-history.md
```

使用static1_implement而不是static_implement，与项目名称保持一致，给未来其他项目保留空间。

## 维护约定

1. **规划不等于实现**：roadmap记录拟议方案和验收，status记录状态；通过验证后才改为已完成。
2. **设计分层**：系统边界变更更新HLD；模块/状态/契约变更更新LLD；API变化同步API_PIPELINE。不在三个文件反复复制整套说明。
3. **记录以证据为准**：dev_logs每个关键节点记录【任务】【实现情况】【分析、评价】【验证】，区分实际执行与未验证事项，不重写历史结果。
4. **归档不是删除**：被替代的完整版本才放deprecated，注明来源、归档日期与替代入口；当前有效设计即使较旧也不自动归档。
5. **引用跟随迁移**：相对链接以所在文件为基准。代码路径用仓库相对路径，或明确写“相对static1”。历史源码快照不能当作当前配置。
6. **兼容但不双写**：原文件路径保留简短迁移提示；新修改只写本目录正文，README只写使用说明。
7. **不改变代码规则作用域**：static1/AGENTS.md原位保留；本次没有搬迁源码、数据库或配置。
8. **小步交付**：变更目标、非目标、验收证据和未解决边界写清楚；文档迁移检查链接及正文保留，不用重复运行未改代码的模型测试。

## 本次迁移映射

| 原路径 | 现路径 |
|---|---|
| agentic-rag-static.md | static1_plan/roadmap.md |
| static1/Strata.md | static1_plan/status.md |
| static1/HLD.md | static1_design/HLD.md |
| static1/LLD.md | static1_design/LLD.md |
| static1/API_PIPELINE.md | static1_design/API_PIPELINE.md |
| static1/IMPLEMENTATION.md | static1_implement/implementation.md |
| static1/dev_logs.md | static1_implement/dev_logs.md |
| agentic-rag-static-history.md | deprecated/agentic-rag-static-history.md |

agentic-rag-static-2.md保持合并导航，不产生第二份规划。原路径均保留可点击迁移入口；本表的原路径相对仓库根目录，现路径相对dev_docs。
