# 第 21 课：Analysis 根据问题选择分析方式

## 1. 本课解决的问题

以前 Analysis 对任何多日问题都给同一份首末比较。现在它可以根据提问选择“总体变化”“最大相邻日下降”或“最高最低日期”，并用已查询的数值计算答案。

## 2. 架构位置

原文的 Analysis Agent 应对数据做分析。本课是其中一个可验证的小能力，不是原文的 GMV 漏斗归因：当前 UCI 数据没有访客、转化率和补贴字段。图仍是 Intent → Supervisor → Query → Analysis；只有 Query 交出完整 `MetricSeriesEvidence` 才进入 Analysis。

## 3. 修改文件、类和方法

- `analysis_agent.py`：新增 `AnalysisPlan` 和 `answer_analysis_question()`；原有 `answer_analysis()` 保留为确定性计算及失败回退。
- `workflow.py`：向 Analysis 节点传入用户问题和可选模型；主命令把已有百炼模型传给 Analysis。
- `tests/test_analysis_planning.py`：验证不同分析方式和无效模型计划的回退。

## 4. 调用链与数据流

`WorkflowState.question` 与 `WorkflowState.query_evidence` → Analysis 节点校验成 `MetricSeriesEvidence` → 模型只返回 `AnalysisPlan.mode` → Python 从证据计算金额与日期 → `State.answer` → `WorkflowResult.answer`。模型输出不包含金额或原因，因此不能覆盖已核实数值。

## 5. 关键执行过程

例如“哪一天跌得最多”：模型从三种受支持任务中选择 `largest_daily_drop`；程序逐对比较相邻日期，找到下降额最大的那一对，再把日期和英镑金额写入回答。如果模型返回未知任务、非 JSON 或发生限流，回退到旧的首末比较。程序仍明确说明总额不能证明下降原因。

## 6. 设计原因与替代方案

这是模型擅长的“理解用户想看哪种分析”与 Python 擅长的“精确计算”分工。让模型自由撰写归因结论会在没有漏斗证据时增加编造风险；完全写死分析方式又无法响应不同问法。以后有可信维度、访客和订单数据时，才扩展分析计划和诊断树。

## 7. 实测与限制

运行 `UV_CACHE_DIR=/private/tmp/taobao-uv-cache uv run --locked --offline python -m unittest discover -s tests -p 'test_*.py' -q`，**82 个本地测试通过，退出码 0**。它证明固定输出的假模型能选择任务，计算仍使用证据，无效计划会回退；没有验证真实百炼模型选任务的准确率。当前环境没有可用 API Key，本课没有在线调用。无需新依赖或数据库软件。

## 8. 本课知识

LangGraph 只用已有的 State 和 Analysis 节点。第九章上下文工程：Analysis 只接收本次问题与完整数值证据。第十章：这是结构化模型调用，不是 Tool Calling，也无需 MCP。第十二章：需分别测任务选择正确率与数值正确率，单次成功不代表稳定。

## 9. 面试知识点

常见技术问题：如何把模型用于分析又保持数值可信？让模型输出受限的计划，校验后由程序计算；失败时使用有证据的回退回答。这里是模拟问题，不是公开面经。

## 10. 下一课前置条件

确认 Analysis 的 LLM 只选择任务，不负责计算。下一课补 Query 对指标口径和实际表字段的回答，复用现有指标目录与 SQLite，不新增数据源。
