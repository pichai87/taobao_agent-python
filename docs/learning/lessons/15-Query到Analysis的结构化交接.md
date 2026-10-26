# 第 15 课：Query 到 Analysis 的结构化交接

> 本课记录第 15 课当时的取数入口。第 16 课将 Query 的单日与两日取数统一接到受控日指标工具，当前工具路径请看第 16 课。

## 1. 本课解决的问题

你指出第 14 课的职责不够清楚：回答两日比较时，Supervisor 选中了 Analysis，但 Analysis 自己调用了数据库查询工具。本课让 Query 专家负责取数，让 Analysis 专家只负责计算与解释。

一句话结论：**Query 交付经过校验的数值，Analysis 消费数值；两者之间传数据，不传一段需要重新解析的回答文字。**

## 2. 在整体 Agent 架构中的位置

主图还是同一张 LangGraph 图。`other` 问题仍走 Other，单日取数仍走 Query；两日比较现在走 `Intent → Supervisor → Query → Analysis`。这一步是**必要工程补充**：让专家交接符合各自职责。它没有实现原文的完整归因树，也没有为两个专家分别加入 LLM。当前唯一的模型调用仍在 Intent。

## 3. 修改了哪些文件与方法

| 文件 | 本课修改 |
| --- | --- |
| `src/taobao_agent/query_agent.py` | 新增 `QueryEvidence`，明确交接字段；新增 `fetch_comparison()`，负责检查参数与指标，执行两次受控取数。原有单日 `answer_query()` 保留。 |
| `src/taobao_agent/analysis_agent.py` | `answer_analysis()` 改为只接收 `QueryEvidence`，不再导入或调用查询工具。 |
| `src/taobao_agent/workflow.py` | `WorkflowState` 新增内部字段 `query_evidence`；归因请求先进入 Query，有完整证据才继续到 Analysis。 |
| `tests/test_analysis_agent.py`、`tests/test_workflow.py` | 验证交接内容、节点顺序、金额不变和失败时停止。 |
| `README.md`、学习路线、学习记录 | 更新运行方式、现状和下一步。 |

没有新增依赖，也没有改 UCI 指标口径或 SQL 门禁。

## 4. 调用链与 State 的数据流

```text
用户：2010-12-02 交易明细金额为什么比 2010-12-01 下降？
  → Intent：IntentResult，包含 attribution、指标名、curr、prev
  → State.intent：保存上述参数
  → Supervisor：State.next_expert = "analysis"
  → Query：按 State.intent 查两天，经既有 SQL Validator Gate 和只读 SQLite
       State.query_evidence = {
         metric_name, prev, curr,
         previous_minor: 5896079, current_minor: 4774838,
         source: "uci", unit: "pence"
       }
  → Analysis：验证 query_evidence，计算差额及变化率
       State.answer = 含两日金额和“下降 19.02%”的中文说明
  → WorkflowResult：主命令输出 intent、next_expert、answer
```

`next_expert=analysis` 指 Supervisor 选定最终处理这类问题的专家，并不表示下一步跳过 Query。`query_evidence` 是图内部的交接数据，不出现在主命令 JSON 中。若 Query 返回错误文字，它将 `query_evidence` 置为 `None`；条件边便结束流程，不调用 Analysis。

## 5. 关键代码如何执行

1. `fetch_comparison()` 检查指标名、两个不同的日期以及当前不支持的分组、范围或复合任务。
2. 它查明确配置的指标定义；教学模式调用 `query_demo_metric()`，UCI 模式调用 `query_uci_metric()`。这些工具继续负责 SQL 构建、后置校验和只读执行。
3. 两次查询都成功后，Query 才创建 `QueryEvidence`。字段使用整数“分”或“便士”，并标记来源和单位；模型校验金额非负、来源与单位匹配。
4. Query 节点把它转换为字典，写进 `State.query_evidence`。Analysis 节点再从字典还原并校验 `QueryEvidence`，随后调用 `answer_analysis()`。
5. Analysis 计算“当前 − 基期”和以基期为分母的百分比，保留原先关于 UCI 非支付口径及不能据此断言原因的说明。失败时显式清空交接字段，避免外部调用图时带入旧证据。

本课的 LangGraph 新知识点只有**同一节点后的条件交接**：Query 完成后，有完整证据才进入 Analysis；单日回答或取数错误直接结束。这是在已有 State、节点和条件边知识上的一次应用。

## 6. 设计原因与替代方案

- **原文要求**：Query 与 Analysis 分别承担取数和分析。原文没有规定本课这份 `QueryEvidence` 的具体字段；字段设计属于必要工程补充。
- 不能把 Query 现有的中文 `answer` 直接交给 Analysis 再解析：其中的数字、单位和日期是展示文字，后续一旦改措辞便可能解析错误。结构化交接保留明确的类型与口径。
- 可以让 Analysis 直接调用底层工具，第 14 课就是这样做的；它适合先验证计算，但专家职责不清晰。现在把取数入口收回 Query。
- 也可以让 Analysis 再次调用 LLM 规划步骤；当前固定的两日比较没有这个需要。未来接入 LLM 时，它应帮助选择分析步骤，受控查询与确定性计算仍保留。

## 7. 实际测试及其边界

在项目根目录执行：

```bash
UV_CACHE_DIR=/private/tmp/taobao-agent-uv-cache uv run --locked --offline python -m unittest discover -s tests -v
```

**目的**：验证业务数值、Query → Analysis 的节点顺序与字段交接，回归单日 Query、Other 和错误处理。**实际结果**：67 个测试通过，退出码 0。测试中间曾因假模型把测试指标写成 GMV 而失败，改为实际配置的“交易明细金额”后全部通过。**不能证明**：所有真实模型问法都能正确提取两个日期，也不能证明已经具备完整归因能力。

还用假模型和本机已有的 UCI SQLite 跑了完整图。实际节点顺序为 `intent、supervisor、query、analysis`；模型调用一次；最终仍是 58960.79 英镑、47748.38 英镑、下降 11212.41 英镑（19.02%）。第 14 课时你已经用 Plus Key 在线验证过同一句问题；**改图后尚未再次在线运行**，因此本次只证明本地新路径。

若要复核在线主命令，使用 README 第 15 课所指的 UCI 两日命令。它会发送一次模型请求；预期 `next_expert=analysis`、数值不变。没有数据库服务或 Navicat 的安装要求，仍使用 Python 自带 SQLite。

## 8. 与 Hello-Agents 的关系

- **第九章，上下文工程**：Analysis 只接收它需要的 `query_evidence`，不需要完整数据库路径、SQL 或全部对话历史。这体现了专家之间传递最小必要上下文。
- **第十章，通信协议**：Query 与 Analysis 在同一进程内通过 LangGraph State 交接；不需要 MCP 或 A2A。两者调用的数据库工具仍是普通 Python 函数。
- **第十二章，评估**：测试不仅看最后答案，还检查实际经过哪些节点、证据字段是否正确、第二次取数失败时是否跳过 Analysis。这属于本地单元与集成验证，不等于在线 Agent 成功率。

## 9. Agent 岗位面试知识点

常见技术知识点：多 Agent 如何交接任务，为什么最好传结构化结果？本课的回答是：Query 提供带日期、单位和来源的数值契约；Analysis 只负责消费、计算和说明。这样能定位错误属于意图、取数还是分析，也能防止从自然语言回答里反向提取数字。这是项目中的工程实践，不是某份公开面经原题。

## 10. 下一课的前置条件

请先用 README 中的命令确认改图后的在线输出。如果结果正常，再选择下一项有数据支持的完整能力。一个可行方向是：在 UCI 已有字段中加入经过核对的分组统计，给 Analysis 提供“哪些分组贡献了金额变化”的证据。访客数、转化率等原文归因指标仍需新的可信数据，不能从 UCI 交易明细中推断。
