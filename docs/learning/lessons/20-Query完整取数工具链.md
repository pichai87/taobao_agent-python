# 第 20 课：Query 的四步取数工具链

## 1. 本课解决的问题

上一课的模型只看得到“找指标”和“查每日金额”。SQL 在查询工具内部生成并校验，但 Query 的工具调用过程没有体现原文的规则检索与 SQL 构建。本课接通 `search_metrics → rule_search → build_sql → query_daily_metric`。Analysis 仍只消费 Query 的结果，不查库。

## 2. 在整体架构中的位置

用户问题进入原有 LangGraph 图：Intent 提取指标和日期 → Supervisor 决定最终专家 → Query 收集有来源的数值 → 需要比较时进入 Analysis。图的节点和边没有变化。`next_expert=analysis` 表示最终分析由 Analysis 完成，取数这个前置任务仍由 Query 执行。

**原文要求**：Query 按指标语义、规则、SQL、数据查询推进，SQL 执行前有 Validator Gate。**必要工程补充**：用已有的 UCI SQLite 与固定 `SELECT SUM` 模板实现一条可核算的局部链路；ODPS、完整 MDL、SQL 片段库和任意业务问法尚未实现。

## 3. 修改的文件与关键方法

| 文件 | 变化与原因 |
| --- | --- |
| `query_agent.py` | `_run_tool_loop()` 增加规则检索和 SQL 构建工具，核对顺序、指标、全部日期，保存待执行的 `MetricQuery`。 |
| `daily_metric_tool.py` | `make_daily_metric_tool()` 接收程序绑定的候选 SQL；模型仍只能传指标名和日期。 |
| `uci_online_retail.py` | `query_uci_metric()` 接收候选 SQL，并在数据库真实字段和固定模板校验后执行。 |
| 三个相关测试文件 | 覆盖完整链路、错误调用、真实 SQLite 和 SQL 篡改。 |

`MetricQuery`、`build_metric_query()` 和 `validate_metric_query()` 是第 11 课已有代码，本课复用。

## 4. 调用链、参数、State 与返回值

```text
IntentResult(metric_name, dates)
  → WorkflowState.intent（图节点间的字典）
  → MetricQueryPlan（本次允许的指标和日期）
  → search_metrics：MetricDefinition（表、金额字段、口径）
  → rule_search：MetricCalculation（sum、日期字段、条件、单位）
  → build_sql：MetricQuery（SQL 模板和绑定参数），按日期保存在程序内
  → query_daily_metric：由程序找出该日期的候选 SQL
  → query_uci_metric：核对数据库、字段、SQL 与参数；只读执行
  → DailyMetricValue → MetricSeriesEvidence → WorkflowState.query_evidence
  → Query 回答，或 Analysis 计算变化 → WorkflowResult.answer
```

模型只提出工具名及受限参数，Python 核对后执行。模型不能传数据库路径，也不能把自己写的 SQL 传给执行器。`build_sql` 返回 SQL 供模型观察；实际执行使用程序保存的同一个 `MetricQuery`，所以这一步确实参与取数。

## 5. 关键代码执行过程

`MetricQueryPlan` 最多容纳 7 个按时间递增的日期。Query 只允许先找指标、再找规则、一次为所有日期构建 SQL、最后逐日查询。跳步、重复、改指标、改日期或额外传 `statement` 字段都会失败。循环最多 12 轮模型响应、12 次工具调用。

真正的安全检查在 `query_uci_metric()`：只读打开数据库，确认导入标记和日期范围，读取 SQLite 实际字段，与批准字段取交集，再由 `validate_metric_query()` 比较**完整 SQL 和绑定参数**。通过后才执行。规则文件和模型建议本身都不能批准数据库访问。

## 6. 设计原因与替代方案

这让原文四步在 Query 调用链中可见，同时保留可核算的固定模板。另一方案是让模型自由生成 SQL，再建立通用解析器和沙箱；目前只有一个核准指标和三列数据，缺少为任意 SQL 定义业务权限的依据。完整 MDL、独立规则库和 ODPS 要等相应数据与口径到位后设计。

## 7. 实际测试结果和限制

在项目根目录运行：

```bash
UV_CACHE_DIR=/private/tmp/taobao-uv-cache uv run --locked --offline python -m unittest discover -s tests -p 'test_*.py' -q
```

**目的**：运行全部本地测试。**实际结果**：79 个通过，退出码 0。固定输出的假模型走四步并读取临时 SQLite，得到 6.35 英镑；测试还证明跳步、错日期和额外 SQL 参数不会触发查询，改 SQL 模板或绑定日期会被数据库入口拒绝。它不能证明百炼模型一定按四步调用，也不能证明所有业务问题都能取数。当前环境没有可用的 `DASHSCOPE_API_KEY`，本课未发出真实模型请求。无需安装数据库服务或 Navicat。

## 8. 本课涉及的知识

**LangGraph**：Query 节点写 `State.query_evidence`，Analysis 节点读它；本课没改节点和边。**第九章上下文工程**：Query 只把核准指标和日期给模型，不塞入整个数据库或图状态。**第十章工具调用**：模型建议工具，程序验证、执行并返回 `ToolMessage`；这里是同进程调用，无需 MCP/A2A。**第十二章评估**：分别测试工具顺序、参数、数值、安全拒绝；离线测试与在线 Agent 成功率是不同证据。

## 9. Agent 岗位面试知识点

常见技术问题：为什么 Tool Calling 还需程序校验？因为模型输出只是提议，不是权限；参数约束、执行预算和最终 SQL 门禁由程序掌握。还应能说明 SQL 模板与绑定参数各负责什么。这里是模拟讲解题，不是引用公开面经。

## 10. 下一阶段前置条件

先确认这四步与 SQL 门禁的调用链。之后优先取得有可信字段和口径支撑的分组/归因证据，或补充现有问法的小型在线工具调用评测。当前 UCI 导出表没有访客、转化率和补贴字段，不能仅凭总额解释下降原因。
