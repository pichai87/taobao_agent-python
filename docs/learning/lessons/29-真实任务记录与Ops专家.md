# 第 29 课：真实任务记录与 Ops 专家

## 1. 本阶段解决什么问题

**一句话**：Ops 不再空泛地回答“任务运行正常”，而是从本机真实执行的数据质量检查记录中读状态、日志和失败原因。

原文 Ops 还涵盖调度实例、告警、基线等生产平台能力。本课没有 DataWorks 凭据，因而只实现当前项目能实际运行和核对的本地质量任务。这是必要的工程替代，不冒充 DataWorks。

## 2. 架构位置与文件

```text
taobao-check-data → 检查业务 SQLite/指标 → data/ops/runs.sqlite
用户问任务状态 → Intent(ops) → Supervisor(ops) → Ops → 只读任务记录/日志 → 答案
```

先读 `ops_store.py` 的 `record_quality_run()`：它执行检查并写入结果。再读 `ops_agent.py` 的 `answer_ops()`：它只读已有记录。最后看 `workflow.py` 中的 `ops_node` 和路由。`intent_agent.py`、`supervisor.py` 仅增加 `ops` 类型；`pyproject.toml` 增加命令入口。

`State.intent` 传分类结果，`State.task_plan` 为 `("ops",)`，`State.answer` 存从记录生成的回答。这一轮不需要 `QueryEvidence`，因为 Ops 查的是任务记录，不是 GMV 数据。

## 3. 如何运行与验证

先确保业务库已通过 `uv run taobao-seed-commerce` 建立。运行 `uv run taobao-check-data` 会执行三项指标的来源、字段和查询检查，并返回 `run_id`。它不会覆盖业务库。随后问 `uv run taobao-agent '最近一次数据质量检查结果和日志是什么？'`，预期进入 Ops，并显示该运行编号、状态、摘要和日志。

离线测试命令：`PYTHONPYCACHEPREFIX=/private/tmp/taobao-pycache .venv/bin/python -m unittest discover -s tests -q`，**35 项通过**。新增测试真实执行一次成功检查、一次缺失业务库导致的失败检查，然后验证 Ops 可读最新失败、指定编号的成功和对应日志。此测试证明记录链路，不证明真实 LLM 对所有运维问法的分类准确率。

## 4. 设计取舍与限制

当前只有一个已知任务 `commerce_quality`，因此 Ops 用确定性只读查询更清晰。若接入 DataWorks 等平台，可以增加经过权限核对的任务工具；不能让 LLM 直接填写任意平台 API、任务 ID 或 SQL。任务记录数据库与业务数据库分离，是执行管理数据，不是另一条业务取数路径。

异常被记录成 `failed` 并保留原因；未运行过时明确答“无记录”。记录中 `running` 状态代表任务尚未结束；进程异常终止后的遗留 `running` 需要后续 Harness 恢复处理。

## 5. 相关知识与面试点

第九章：只给 Ops 当前任务的日志，不把整份运行历史塞给模型。第十章：本地函数调用足够，尚无 MCP 必要。第十二章：检查“状态是否有对应 run_id 和日志”比只看回答是否流畅更可靠。

模拟面试题：Ops 专家怎样避免编造调度状态？答：只读实际执行记录，缺失则承认缺失；执行与读状态分为两个有权限边界的动作。

## 6. 下一阶段前置条件

下一包 Harness 将统一限制工具权限、时间和重试次数，并保存执行轨迹。先能区分 `ops_store` 负责执行记录，`ops_agent` 负责读取记录。
