# 第 23 课：Supervisor 规划“查口径＋查数”

## 1. 本课解决的问题

以前 Intent 即使识别出 `secondary_intents`，后续也不能完成两个任务。现在可以问“交易明细金额怎么计算？2010-12-01 是多少？”，一次得到口径和数值。

## 2. 架构位置

PDF 明确举例 `table_meta + data_query`。本课让 Supervisor 将它规划成先说明口径、再取数的两步；两个任务都属于 Query 专家，所以仍在一个 Query 节点执行。其他组合先明确拒绝，不假装已实现完整多 Agent 规划。

## 3. 文件与方法

- `supervisor.py`：`plan_tasks()` 从已校验的 `IntentResult` 生成受支持任务顺序。
- `workflow.py`：增加 `State.task_plan`；Supervisor 写计划，Query 按计划调用 `answer_table_meta()` 与 `answer_query()`；不支持的组合在 Supervisor 停止。
- `tests/test_workflow.py`：验证两种主次顺序、证据结果和拒绝路径。

## 4. 调用链与信息载体

`IntentResult(intent_type, secondary_intents, metric_name, curr)` → `State.intent` → `plan_tasks()` → `State.task_plan=[table_meta,data_query]` → Query 分别调用元数据与取数函数 → `State.answer` 包含两个标明来源的部分 → `WorkflowResult.answer`。`next_expert=query` 表示最终由 Query 回答；`task_plan` 表示它具体做哪两件事。

## 5. 关键执行

主意图是 `table_meta` 或 `data_query` 都可，次要意图为另一项即可。Query 先核对指标口径和数据库实际字段，再按 Intent 日期取数。执行每个子任务时临时去掉 `secondary_intents`，使原有单任务校验仍可复用；用户原始 Intent 不被改写。缺日期或数据源时会明确写出对应任务失败的原因，不会静默省略。

## 6. 设计取舍

这是一条确定的两任务依赖，Supervisor 用 Python 规划更可控；Intent 已经由 LLM 完成语义识别，再让另一个 LLM 重判同样的两项会增加延迟。未来有跨 Query、Ops、Analysis 的开放式计划时，再考虑模型规划和更复杂的图结构。

## 7. 测试与限制

运行 `UV_CACHE_DIR=/private/tmp/taobao-uv-cache uv run --locked --offline python -m unittest discover -s tests -p 'test_*.py' -q`：**86 个本地测试通过，退出码 0**。固定输出的 Intent 假模型穿过 Supervisor、Query 和临时 SQLite，两种主次顺序都得到口径及 6.35 英镑；不支持的组合被拒绝。没有用真实百炼验证多意图识别准确率。无需新依赖或数据库软件。

## 8. 本课知识

LangGraph 使用 State 新字段传递计划，未新增节点。第九章：`task_plan` 是精简交接信息，不需要向 Query 复制完整模型对话。第十章：同进程节点交接不是 A2A。第十二章：组合任务评测要同时检查意图识别、计划完整性、两个子任务的结果和未支持组合的拒绝行为。

## 9. 面试知识点

常见技术问题：Supervisor 什么时候用规则，什么时候用 LLM？任务类型和依赖已明确时，规则简单可测；开放式、多步且不能枚举的计划才可能需要模型。这里是模拟讲解题。

## 10. 下一课前置条件

有了单任务和一种组合任务后，下一课建立小型 Golden Dataset 与可重复运行的评测命令，区分模型行为评测与现有单元测试。
