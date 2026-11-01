# 第 30 课：Harness 权限、时限与执行记录

## 1. 本阶段解决什么问题

**一句话**：模型和工具的每一步现在有运行权限、调用预算、总时限、短暂故障的有界重试，以及可回看的执行轨迹。

原文要求 Harness 管执行过程与安全边界。本课在当前单机项目中实现这些必要边界；生产级进程隔离、分布式调度和外部系统权限尚未接入。

## 2. 架构位置与文件顺序

```text
用户问题 → RunHarness(run_id, 权限, 预算, 事件记录)
         → Intent → Supervisor → Query/Analysis/Ops/Other
         → Query 工具调用 → SQL Validator Gate → 只读 SQLite
```

先读 `harness.py` 中的 `_ALLOWED` 和 `RunHarness.call()`，再读 `workflow.py` 如何为一次问题创建同一个运行对象，最后看 `query_agent.py` 每次模型选工具和执行工具时怎样调用它。`commerce_data.py` 给 SQLite 查询设置截止时间；`ops_store.py` 在再次运行时将五分钟前遗留的 `running` 任务标记为失败并写日志。`tests/test_commerce_workflow.py` 包含权限、重试、超时、记录和任务恢复测试。

`State` 继续传业务问题与证据；Harness 记录的是执行过程，二者职责不同。`WorkflowResult.run_id` 可用于从事件文件里找到这一问的全部事件。

## 3. 一次取数如何通过边界

Query 在每轮先请模型选择工具；Harness 记录 `choose_tool`。模型提出 `execute_query` 后，Query 先核对请求日期、指标、顺序和批准 SQL，再经 Harness 权限检查，最后才到只读数据库与 SQL Gate。Analysis 只有 `analyze` 权限，直接请求 `execute_query` 会被 Harness 拒绝。换句话说，模型是否遵守提示不构成权限基础。

连接断开或工具超时只重试一次，其他错误不自动重试。每次调用前检查最多 64 次和默认 120 秒的总预算，结束后再次检查。SQLite 使用 progress handler 中断超过截止时间的查询；模型调用还受 `model.py` 的单次 30 秒超时控制。总时限对非 SQLite 的同步 Python 函数是**协作式**检查，不能强制停止正在阻塞的任意代码，不应宣传为操作系统沙箱。

事件文件默认在 `data/ops/execution_events.jsonl`，每条只写 `run_id`、专家、动作名、状态、耗时和错误类型；不写问题原文、模型消息、工具参数或 Key。运行结果中的 `run_id` 用来定位事件。文件按行追加，当前不做轮转与多进程写锁。

## 4. 运行与验证

命令 `uv run taobao-agent '2026-10-22 GMV 多少？'` 的目的仍是回答业务问题；现在输出多一个 `run_id`，并写本机事件文件。预期事件从 `started` 到 `completed`，中间有 Intent、Supervisor 和 Query 工具记录；这只证明一次调用链，不证明真实模型的整体准确率。

执行 `PYTHONPYCACHEPREFIX=/private/tmp/taobao-pycache .venv/bin/python -m unittest discover -s tests -q`：**40 项离线测试通过**。新测试检查 Analysis 越权被拒、短暂连接失败一次后成功、次数/时限预算触发、记录不含敏感异常文本、单次运行 ID 贯通，以及旧 Ops 任务中断后的状态修复。现有 SQL 篡改拒绝测试也继续通过。另两项固定模型测试分别覆盖“明确范围被列成两个端点时归一”与“变化分析仍保留两个单日观察值”。

**在线首轮纠错记录**：`taobao-eval --diagnostic` 七题为 **6/7**，报告保留在 `/private/tmp/taobao-eval-packages-27-30.json`。失败题 `gmv_decline_industry` 的专家、日期、维度和答案都通过，但模型把分析前置的 `data_query` 也写进 `secondary_intents`，导致“意图集合”断言失败。根因不是 Query 没取数，而是把**程序内部依赖**混成了**用户明确提出的第二任务**。修正 `intent_agent.py` 提示：只问下降分析时不加 `data_query` 次要意图；用户另行索要具体数值时才加。此修改只影响意图表达，不放宽评测断言或 SQL 门禁。

**定向复测**：`taobao-eval --case gmv_decline_industry --diagnostic` 为 **1/1**，报告在 `/private/tmp/taobao-eval-packages-27-30-retest.json`；观察到的意图集合仅 `attribution`。这证明该题在这次模型调用中恢复，并不保证其他问法。

**完整回归发现新问题**：`/private/tmp/taobao-eval-packages-27-30-final.json` 为 **6/7**；前一题已通过，但 `range_multi_metric_industry` 失败。模型把“2026-10-21 至 2026-10-22”输出为两个单日的 `dates`，`date_range=null`，因而 Query 算出了两天各自的数值，未返回范围合计。改动有两层：提示明确“至/到”须输出范围；程序仅在问题明确出现两个日期的“至/到”、模型所识别的端点也完全一致、且是纯取数意图时，将端点表示归一为 `date_range`。真正的两日比较仍保留两个单日。该题增加离线回归，在线定向复测 **1/1**，报告在 `/private/tmp/taobao-eval-range-retest.json`。

**最终七题回归**：`/private/tmp/taobao-eval-packages-27-30-verified.json` 为 **7/7**；七题的意图、专家、日期、维度和答案断言全部通过，错误输出文件为空。这是当前固定题集的一次在线结果，不能证明未知问法或长会话可靠。新增的血缘、三任务、Ops 和跨轮追问需要另做在线冒烟验证。

**新增路径冒烟**：执行真实本地质量任务，并用 Qwen 分别问血缘、三任务复合问题、会话首轮/追问、Ops 日志。任务执行、血缘、三任务、首轮会话和 Ops 都有正确的 JSON 与空错误文件；结果分别在 `/private/tmp/taobao-smoke-job.json`、`taobao-smoke-lineage.json`、`taobao-smoke-three-tasks.json`、`taobao-smoke-session-first.json`、`taobao-smoke-ops.json`。三任务回答同时含指标口径和下降分析；Ops 读到真实 `success` 运行与逐步日志。唯独追问首轮失败：模型输出的 `curr` 与 `dates` 不一致，`IntentResult` 拒绝，错误保留在 `/private/tmp/taobao-smoke-session-second-error.txt`，没有返回错误数值。纠错：当用户原话恰为“那前一天呢？”一类明确省略问法、上一轮只有一个日期时，程序基于已确认日期计算前一天，保留 `source=rule`；不对多日期或其他复杂追问猜测。该规则与一般 LLM 上下文路径均有离线测试。在线追问复测输出在 `/private/tmp/taobao-smoke-session-retest.json`，错误文件为空，得到 `curr=2026-10-21`、`source=rule` 和 `GMV 24000.00 元`。这证明这一明确省略问法在本次会话能恢复，不代表所有自然语言追问均可靠。

## 5. 设计取舍与已知限制

权限表由程序控制，模型只能在表内提出工具。重试限于读操作和模型/工具提议路径；对有副作用的外部写操作，不能照搬自动重试，否则可能重复执行。当前 Ops 的质量检查是独立 CLI，未交给模型自由触发。

五分钟未结束的 Ops 记录被标为失败是**状态恢复**，不是自动重跑业务任务。多进程协调、写入幂等、持久队列和真正的进程级执行超时，是以后接生产平台时的工程问题。

## 6. 相关学习与面试点

第九章：执行轨迹与对话记忆不同，不应把全部轨迹塞进提示。第十章：Tool Calling 只是提出动作，Harness 决定可否执行；未来若用 MCP，也仍需本地权限核对。第十二章：分别测越权拦截率、暂时故障恢复率、时限触发、任务完成率及成本；本课只覆盖离线可确定的部分。

模拟面试题：为什么有 SQL Gate 还需要 Harness？SQL Gate 验证一个查询是否符合口径和 SQL 规则；Harness 验证谁能发起动作、执行多少次、多久结束以及失败后留下什么记录。

## 7. 下一阶段前置条件

下一包是 FastAPI 与 SSE。它应调用现有 `run_workflow()`，使用同一权限与数据契约，而不是另建一套 Web 专用业务逻辑。先能解释 `run_id`、`State`、`QueryEvidence` 三者的区别。
