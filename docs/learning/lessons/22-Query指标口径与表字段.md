# 第 22 课：Query 回答指标口径与表字段

## 1. 本课解决的问题

以前“交易明细金额使用什么表、怎么算？”会进入 Query，却因为没有日期而被要求补日期。现在这类问题直接回答指标口径，并核对 SQLite 中真实存在的字段。

## 2. 架构位置

原文的 Query Agent 除取数外还负责元数据。本课实现其中的受限一部分：一个已批准指标的表、字段、计算条件和数据日期范围。不是完整的 MDL、血缘或任意表结构问答。

## 3. 文件与关键方法

- `uci_online_retail.py`：`inspect_uci_metric()` 只读检查导入标记和实际字段，返回 `UCIDataProfile`。
- `query_agent.py`：`answer_table_meta()` 组合指标目录的业务口径与数据库核实结果。
- `workflow.py`：`table_meta` 意图进入 Query 的元数据路径，无需查询日期。
- `tests/test_workflow.py`、`tests/test_uci_online_retail.py`：验证真实字段与错误数据库被拒绝。

## 4. 调用链与数据流

用户问题 → `IntentResult(intent_type=table_meta, metric_name=...)` → `State.intent` → Supervisor 选 Query → `answer_table_meta()` 查 `MetricDefinition` → `inspect_uci_metric()` 读数据库元数据和 `PRAGMA table_info` → `State.answer` → `WorkflowResult.answer`。这里不产生金额，也不进入 Analysis。

## 5. 关键执行

Query 先精确查指标名。若未启用数据源，只说明目录记录，并明确表字段尚未核对；若启用 UCI，先确认指标与项目内置规则一致，再以只读连接检查来源标记与三列实际字段。任何一步失败，都不声称字段存在。

## 6. 设计取舍

复用已有目录和本地 SQLite，不新增 RAG 或另一套知识库。只返回当前批准的字段，避免把数据库里的其他列误说成开放给 Agent 的能力。以后有 MDL 和血缘资料时，可把这条元数据路径扩展为更完整的 Query 专家能力。

## 7. 测试与限制

运行 `UV_CACHE_DIR=/private/tmp/taobao-uv-cache uv run --locked --offline python -m unittest discover -s tests -p 'test_*.py' -q`：**84 个本地测试通过，退出码 0**。测试证明无日期的元数据问题能穿过主图；批准字段缺失或指标规则被改动会失败。这不证明任意表或真实淘宝字段可查询；没有真实百炼在线调用。无需新依赖、数据库服务或 Navicat。

## 8. 本课知识

LangGraph 仍使用原有 Query 节点，仅节点内部按意图选择方法。第九章：指标目录是静态上下文，实际字段是运行时核对的上下文，二者不能混为一谈。第十章：这里是普通 Python 函数调用，不需要另建 MCP。第十二章：元数据评测应同时检查“正确回答”和“未知字段拒答”。

## 9. 面试知识点

常见技术问题：为什么有指标目录还要查数据库 schema？目录描述预期口径，数据库检查实际存在性；两者一致才能给出可信元数据。这里是模拟讲解题。

## 10. 下一课前置条件

现在 Query 有元数据和数值两个可执行能力。下一课让 Supervisor 对原文举例的“查表结构并查询数值”明确规划为组合任务，而不是丢弃次要意图。
