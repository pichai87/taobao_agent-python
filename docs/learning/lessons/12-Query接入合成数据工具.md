# 第 12 课：让现有 Query 查询合成订单

> 历史课程提示：第 17 课已按你的决定移除主命令的教学数据模式及 `demo_query_tool.py`。本课记录当时的实现，下面的 `TAOBAO_DEMO_DATA=1` 命令现在会提示模式已移除；合成订单仍用于离线测试。当前运行方法见 README 与第 17 课。

先看结果：现在可以明确开启“教学数据模式”，让已有的 Query 节点查 6 笔合成订单。问“2026-10-22 GMV 多少？”时，本地测试得到 **200.00 元**，回答会标出“教学合成数据”。默认模式仍不取数。

## 1. 本课解决的问题

第 11 课的 SQL 构建器和门禁只在测试中执行，Query 仍只会报告指标定义。本课让 Query 通过一个本地 Python 查询函数使用它们，打通“已识别的日期 → 指标规则 → 安全检查 → 合成数据结果”。当前没有淘宝真实订单，所以必须明确标出结果来自教学样例。

## 2. 在整体架构中的位置

```text
用户问题 → Intent → Supervisor → Query
                              ├─ 普通模式：仍只报告指标定义
                              └─ TAOBAO_DEMO_DATA=1：查内置指标 → 本地查询工具
                                  → 构建 SQL → 后置门禁 → 只读 SQLite → 回答
```

LangGraph 图仍是原来的节点和条件路由，本课只是让 Query 在受限情形下执行下一步。原文要求 Query 按指标知识构建和执行 SQL，并由 SQL Validator Gate 把关；本课的合成数据和开关是**必要工程补充**，不是原文规定的数据源。

## 3. 修改文件、类和关键方法

| 文件 | 本课变化 |
| --- | --- |
| `src/taobao_agent/demo_query_tool.py` | 新增 `query_demo_metric()`；在项目样例上执行经过门禁的查询，失败时抛 `DemoQueryError` |
| `src/taobao_agent/query_agent.py` | `answer_query()` 在教学模式下判断问法、调用本地工具、格式化“分”为“元” |
| `src/taobao_agent/workflow.py` | 主命令读取 `TAOBAO_DEMO_DATA`；已有 Query 节点接收开关 |
| `tests/test_demo_query_tool.py`、`tests/test_workflow.py` | 验证只读连接、数值、默认不取数、输入限制和主图/CLI 交接 |
| README、学习路线、学习记录 | 更新运行方式、进度和限制 |

没有增加第二张 Agent 图，也没有新增 Python 依赖。

## 4. 调用链、参数、State 和返回值

主命令读到 `TAOBAO_DEMO_DATA=1` 时，先加载 `examples/demo_metrics.json`，再创建模型并运行原图。Intent 只调用模型一次，产出 `IntentResult`，其中 `metric_name="GMV"`、`curr=date(2026, 10, 22)`。Supervisor 选择 `query`。Query 节点把 Intent 和指标目录交给 `answer_query(..., demo_data=True)`。

`answer_query()` 查到 `MetricDefinition` 后，把它和 `curr` 交给 `query_demo_metric()`。工具返回整数 `20000`（单位“分”）；Query 将它格式化为 `200.00 元`，写进原有 `State.answer`，最终由 `WorkflowResult.answer` 输出。`WorkflowState` 没增加字段，`metric_catalog` 与教学开关仍是建图时传入的外部配置。

## 5. 关键代码怎样执行

工具每次新建一个内存 SQLite 数据库，运行项目自带的 `demo_shop.sql` 插入合成订单，然后执行 `PRAGMA query_only=ON`，令该连接拒绝写入。它取得合成表的实际列名，只把其中预先批准的金额、日期和支付状态字段交给第 11 课门禁。SQL 与参数通过门禁后，工具才调用 `connection.execute()`。

Query 仅处理主意图为取数、有单个明确日期、无比较日期、范围、分组或次要任务的 GMV。输入超出范围时给出提示，**不会把部分结果冒充完整回答**。没有设置开关时，即使目录里有同名指标，也维持之前“只找到定义，尚未接入查询”的行为。教学模式不能同时使用自定义指标目录，以免把其他来源的定义误认为合成数据口径。

## 6. 设计原因与替代方案

- **原文要求**：Query 从指标知识进入 SQL 构建与执行，返回取数结果；SQL 执行前通过校验。
- **必要工程补充**：用项目自带合成数据和单次命令开关建立可验证的取数链，避免无真实表时猜业务数据。`sqlite3` 是 Python 标准库，不需要数据库服务。
- **替代方案**：让 LLM 自主 Tool Calling 或直接连接远程数据库。现在只有一条明确的查询路径，同进程 Python 函数更容易检查数据流和失败行为；真实库的账号、权限、表语义尚未准备好。

本课的工具不是另一个 Agent，也没有让模型生成任意 SQL。工具的内存库只在这次调用中存在；演示模式依赖源码目录中的 `examples/`，打包后的独立安装尚未验证。门禁只覆盖当前模板；合成数据正确不证明淘宝业务结果可靠。

## 7. 实际运行验证和已知限制

在项目根目录执行：

```bash
UV_CACHE_DIR=/private/tmp/taobao-agent-uv-cache uv run --locked --offline python -m unittest discover -s tests -v
```

目的：运行本课与既有测试，不需要 API Key。实际 **51 个测试通过，退出码 0**。其中假模型测试证明 Intent → Supervisor → Query → 本地工具的调用链，并核对 200.00 元；工具测试证明只读连接拒绝插入。它不能证明真实模型一定把问题识别正确。`uv sync --locked --offline` 解析 45 个包、检查 44 个已安装包，退出码 0；`taobao-agent --help` 退出码 0，只检查依赖与命令入口。

如果你准备好 Plus Key，可在项目根目录、存有该环境变量的同一终端运行：

```bash
env -u TAOBAO_METRIC_CATALOG_PATH TAOBAO_DEMO_DATA=1 DASHSCOPE_API_KEY="$DASHSCOPE_PLUS_API_KEY" DASHSCOPE_MODEL=qwen-plus uv run taobao-agent '2026-10-22 GMV 多少？'
```

目的：发送**一次真实模型请求**识别指标与固定日期，再由本地工具查询合成数据。`env -u` 只为这条命令移除旧的自定义指标目录；`TAOBAO_DEMO_DATA=1` 也只对这一条命令生效。若模型正确识别，预期 `next_expert=query`，`answer` 包含“教学合成数据”和“200.00 元”。**本次未在当前工具环境运行在线调用**，所以这只是待你验证的预期；请只反馈不含 Key 的 JSON。一次成功也不代表其他日期或问法都可靠。

**软件提醒**：仍只使用 Python 自带 `sqlite3` 的内存数据库，无需安装 MySQL、PostgreSQL 或 Navicat。若以后需要外部数据库，会先说明软件、版本、只读账号和安装步骤。

## 8. 本课关联知识

- **LangGraph**：原有 Query 节点和条件边不变；节点读取 Intent 的 `curr`，并把工具结果写入原有 `State.answer`。不需要学习新的图 API。
- **第九章，上下文工程**：Query 只获得当前指标定义与日期；合成数据结果带来源标签，避免被当成真实淘宝数据。
- **第十章，通信协议**：这里的“工具”是本地 Python 函数，由 Query 直接调用；没有模型 Tool Calling，也没有 MCP/A2A。
- **第十二章，评估**：假模型集成测试与本地计算测试各有范围。在线验证还需检查分类、日期、工具选择和答案是否与已知样例一致。

## 9. Agent 岗位面试知识点

这是**常见技术知识点与模拟题**，不是公开面经：“如何避免一个演示数据库的结果被误当成生产答案？”可结合本课解释：数据模式显式开启、只读连接、受限 SQL、限定问法、结果标明来源，以及默认不取数。还要能解释普通函数调用与 LLM Tool Calling 的区别。

## 10. 下一课前置条件

能独立复述 `Intent.curr → Query → query_demo_metric → SQL 门禁 → SQLite → answer`，并理解为什么比较或分组问题不能直接复用单日结果。下一步先记录一条 Plus 模型的真实输出，再根据问题决定增加小型业务评测或扩展数据来源；接入公开数据或真实数据库前仍需核对许可、表结构和指标口径。
