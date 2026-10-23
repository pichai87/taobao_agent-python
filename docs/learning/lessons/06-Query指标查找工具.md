# 第 6 课：Query 先查指标定义

先看一个问题：“昨天 GMV 多少？”以前主图只输出 `next_expert=query`。现在它会进入 Query 节点，调用 `search_metrics` 查找 GMV 的指标定义。由于当前没有配置真实指标目录，最终会明确回答“尚未配置 GMV 的指标定义，无法查询数据”。

## 1. 本阶段解决的问题

原文 PDF 第 6 页展示的取数链路，第一步是 `search_metrics(query="GMV")`，先找指标所在的表和字段，后续才查规则、构建 SQL 和执行查询。本课只把**第一步工具调用**接到现有主图。它解决了“Query 被选中后什么都没有执行”的问题。

## 2. 在整体 Agent 架构中的位置

```text
用户问题 → Intent → Supervisor → Query → search_metrics → 回复查找结果
                     ├─ Other → 寒暄/澄清
                     └─ Analysis → 结束（尚未实现）
```

Query 的完整职责还包括取数、元数据、SQL 开发和血缘。本课只做指标定义查找。没有生成 SQL，也没有访问 ODPS。

## 3. 本次修改的文件、类和关键函数

| 文件 | 作用 |
| --- | --- |
| `src/taobao_agent/metric_search.py` | `MetricDefinition` 记录指标名、表、字段和来源；`search_metrics()` 根据名称在传入的目录中查找 |
| `src/taobao_agent/query_agent.py` | `answer_query()` 调用查找函数，把“找到”或“未配置”转换为明确回复 |
| `src/taobao_agent/workflow.py` | 增加 Query 节点；Supervisor 选中 `query` 时进入它；可注入指标目录 |
| `tests/test_workflow.py` | 用测试中的示例目录验证找到指标、找不到指标和一次模型调用 |
| README、学习路线、学习记录、本课 | 记录运行方法、真实输出和当前限制 |

`MetricDefinition` 的 `source` 是来源说明，方便后续核对指标定义。当前 CLI 没有装载任何指标目录；测试用的 `demo_sales_daily.paid_amount` 是虚构的测试资料，**不会用于真实运行**。

> 第 8 课更新：CLI 现在可通过 `TAOBAO_METRIC_CATALOG_PATH` 加载用户提供的 JSON 指标目录；不设置时仍为空。本课的虚构测试定义没有变成默认业务资料。

## 4. 调用链、参数、State 与返回值

以“昨天 GMV 多少？”为例：

```text
main() 接收 question
  → Intent 节点调用模型，写入 State.intent（例如 metric_name="GMV"）
  → Supervisor 写入 State.next_expert="query"
  → 条件边进入 Query 节点
  → Query 读取 State.intent，调用 answer_query(intent, metric_catalog)
  → answer_query 调用 search_metrics("GMV", metric_catalog)
  → 当前目录为空，工具返回 None
  → Query 写入 State.answer="尚未配置 GMV 的指标定义，无法查询数据。"
  → CLI 输出 intent、next_expert、answer
```

`IntentResult.metric_field` 仍为 `null`：Intent 不能凭模型猜数据库字段。以后有经过核验的目录时，Query 才能从目录中查到表和字段。本课测试可以通过 `run_workflow(..., metric_catalog)` 注入一个示例目录，用来验证数据在函数之间正确传递。

## 5. 关键代码的执行过程

`search_metrics(name, catalog)` 是一个普通 Python 函数：把名称去掉首尾空格、忽略英文大小写，再在传入的定义中逐个匹配。找不到就返回 `None`，不会让模型编造表名或字段。

`answer_query()` 先检查是否提到了指标；随后调用该工具。找不到时说明缺少定义；找到时报告目录给出的表、字段和来源，同时仍说明没有接入数据查询。`workflow.py` 的 Query 节点只负责从 State 取出意图并调用 `answer_query()`。现有 Other 路径仍按第 5 课执行，Analysis 路径仍只返回交接结果。

## 6. 设计原因与主要替代方案

- **原文要求**：先 `search_metrics` 找到指标，再考虑规则、SQL 和查询。
- **必要工程补充**：当前缺少可核实的业务目录，运行时使用空目录；工具返回“未配置”，让缺失的前置资料清楚可见。测试注入虚构目录验证调用，不把示例表当作生产表。
- **替代方案**：可以立刻把 `search_metrics` 包成 LangChain Tool，让 LLM 自主决定是否调用。当前 Query 的第一步已经由流程确定，直接调用本地函数更简单，也容易验证。以后有多个可选工具和 ReAct 循环时再引入模型 Tool Calling。

## 7. 实际测试结果和已知限制

在项目根目录执行：

```bash
UV_CACHE_DIR=/private/tmp/taobao-agent-uv-cache uv run --locked --offline python -m unittest discover -s tests -v
```

这条命令使用假模型检查代码行为，不发送百炼请求。**24 个测试通过，退出码 0**。新增测试证明：Query 会读取注入的示例指标定义；无关定义不会被误当作 GMV；未配置时给出明确提示；模型仍只调用一次。测试不能证明真实模型一定识别为 `data_query`，也不能证明任何真实表字段正确。

另执行 `UV_CACHE_DIR=/private/tmp/taobao-agent-uv-cache uv sync --locked --offline`：解析 45 个包、检查 44 个已安装包，退出码 0，说明现有锁文件与环境可用；没有新增依赖。执行 `UV_CACHE_DIR=/private/tmp/taobao-agent-uv-cache uv run --locked --offline taobao-agent --help`：退出码 0。查看编译图节点得到 `__start__、intent、supervisor、other、query、__end__`。这些检查不验证业务取数。

你可以在已配置 Plus Key 的终端运行：

```bash
DASHSCOPE_API_KEY="$DASHSCOPE_PLUS_API_KEY" DASHSCOPE_MODEL=qwen-plus uv run taobao-agent '昨天 GMV 多少？'
```

这条命令的目的是验证真实模型的 Intent → Supervisor → Query → 工具调用链，会发送一次模型请求。预期 `next_expert=query`，`answer` 说明 GMV 指标尚未配置。本次工具环境没有你的 Key，**没有执行在线查询样例**。这一步不会返回 GMV 数值、SQL 或数据源结果。

## 8. 本阶段涉及的学习知识

- **LangGraph**：仍使用第 5 课的条件边，只是增加 Query 这个目的节点；无需学习新的图 API。
- **第九章，上下文工程**：Query 只接收已校验的 `IntentResult` 和明确传入的指标目录，不依赖整段会话历史。来源字段帮助后续判断目录是否可信。
- **第十章，通信协议**：这里的“工具”是同一进程里的 Python 函数调用；**Tool Calling** 指模型通过结构化请求选择并传参给工具；**MCP** 则是向外部工具服务通信的协议。本课没有后两者，也不需要 A2A。
- **第十二章，评估**：本地测试验证工具选择后的确定性执行；真实模型对不同问法能否选中 Query，需要业务评测样例。当前没有计算准确率、LLM Judge 或外部标准基准。

## 9. Agent 岗位面试知识点

这是常见技术知识点，**不是公开面经**：什么时候直接调用 Python 函数，什么时候使用模型 Tool Calling？结合本项目回答：如果流程明确要求 Query 第一步查指标，程序直接调用更可控；若专家需要根据问题在多个工具间动态选择，再考虑让模型选工具。还要说明工具返回 `None` 时不能编造结果，以及为何记录指标定义来源。

## 10. 下一阶段的前置条件

先能解释 `question → Intent.metric_name → Query → search_metrics → answer`，并确认真实终端输出。要继续构建 SQL，需有经过核验的最小指标定义、表字段、过滤口径和测试数据；执行 SQL 前还需基本只读限制与校验。没有这些资料时，可以继续验证工具调用边界，但不能声称已完成真实取数。
