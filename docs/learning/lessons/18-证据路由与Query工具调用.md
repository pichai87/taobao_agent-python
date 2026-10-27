# 第 18 课：按证据选择路径，并接入 Query Tool Calling

## 1. 这课解决什么问题

用户问“2010-12-02 的交易明细金额为什么比 2010-12-01 下降？”时，问题里只有日期，**必须先查两天金额**。若用户本轮已明确给出两天金额，Analysis 可以直接计算变化，答复须注明这些数字未经数据库核实。本课同时让 Query 的模型真正提出一次受控工具调用，再由程序检查并执行。

一句话：**Supervisor 选最终专家，证据决定是否需要先走 Query；模型建议工具，程序掌握执行权。**

## 2. 在整体架构中的位置与依据

- **原文要求**：Intent → Supervisor → 专家 Agent；Query 负责取数，Analysis 负责分析；工具执行受 Harness/SQL Gate 约束。
- **必要工程补充**：单日、双日 Query 共用一次取数逻辑；模型工具建议与已批准的指标、日期逐项核对，不能让模型更改 SQL 或数据库路径。
- **针对本项目问法的窄范围补充**：用户本轮明确给出同一指标、两个日期、两个同单位金额时，直接进入 Analysis。PDF 没有规定这种跳过 Query 的路线，因此只支持这一种可检查的格式，不支持从历史对话取数。

仍只有一张 LangGraph 图。用到的概念仅是 **State**（节点共享的数据）和 **条件边**（根据 State 决定下一个节点）。

## 3. 修改了哪些文件

| 文件 | 本次作用 |
| --- | --- |
| `workflow.py` | Supervisor 写入最终专家和可能已有的证据；条件边选 Query 或 Analysis；CLI 把同一个模型提供给 Query。 |
| `analysis_agent.py` | `ComparisonEvidence` 统一表示“两个数及来源”；`extract_user_comparison()` 只提取本轮明确金额；`answer_analysis()` 标注数据来源。 |
| `query_agent.py` | `_fetch_days()` 复用单日/双日取数；`_query_day()` 检查模型的工具调用建议，再执行受控工具；`QueryEvidence` 表示经 UCI 查询得到的两日金额。 |
| `model.py` | `ToolCallingModel` 描述当前模型可调用 `bind_tools()`。 |
| `tests/test_workflow.py`、`tests/test_query_tool_calling.py` | 检查两条图路径、工具建议、异常拒绝和数值结果。 |

没有增加依赖，也没有安装数据库服务或 Navicat。

## 4. 调用链、参数和 State

**问题只有日期，需查数据库**：

```text
question
  → Intent: IntentResult(metric_name, prev, curr, intent_type=attribution)
  → State.intent: 保存为字典
  → Supervisor: State.next_expert = analysis；尚无 analysis_evidence
  → 条件边: 先到 Query
  → Query: 每个日期向模型请求 query_daily_metric 调用建议
  → Python: 校验建议 → 工具 → SQL Gate → 只读 SQLite
  → State.analysis_evidence: 指标、日期、便士整数、source=uci、unit=pence
  → Analysis: 校验 ComparisonEvidence，计算差额和变化率
  → State.answer → WorkflowResult.answer
```

**问题本轮已经写明两天金额**：

```text
question
  → Intent → State.intent
  → Supervisor: 从本轮 question 提取两笔明确金额
  → State.next_expert = analysis；State.analysis_evidence.source = user
  → 条件边: 直接到 Analysis
  → State.answer → WorkflowResult.answer
```

`next_expert=analysis` 表示**最终负责答复的专家**，并不保证下一个执行节点立刻是 Analysis。`analysis_evidence` 是 Analysis 的输入；此前叫 `query_evidence`，但现在数据也可能来自用户，所以改了名字。`IntentResult` 是意图识别结果；State 保存节点交接数据；`WorkflowResult` 是对外输出，三者用途不同。

## 5. 关键代码怎么执行，在哪里校验

1. `extract_user_comparison()` 只接受当前问题中恰好两个明确日期和金额，且每笔都带 Intent 中同一个指标名，两笔单位相同、日期吻合。缺任何一项就返回 `None`，回到 Query 路径。它不是通用中文金额解析器。
2. Query 从已批准目录找到指标后，`_fetch_days()` 为每个日期调用 `_query_day()`；这消除了单日和双日重复的工具组装代码。
3. `_query_day()` 用 `bind_tools([query_daily_metric], tool_choice="auto")` 向模型提供**一个**工具。模型可能返回工具建议，也可能不返回。程序要求恰好一条有效建议，检查工具名、参数结构、指标和日期必须与当前任务完全一致，然后才执行。
4. 工具只公开指标名、日期；数据库路径由程序绑定。工具内部仍核对指标目录、执行固定 SQL 后置门禁并使用只读 SQLite。工具返回值也再次校验指标和日期。任一步失败，Query 给出受限提示，不交给 Analysis 半份结果。
5. Analysis 只计算两个金额之差和变化率。`source=user` 的回答明确写“未经数据库核实”；`source=uci` 保留公开数据及口径说明。两种来源都不能单凭总额推断下降原因。

这说明了校验应放在哪里：**模型建议跨入工具执行边界时校验参数；工具结果跨入 Analysis 边界时校验数值与来源。** 已在同一受控函数内部传递的普通变量无需机械地每行重复校验。

## 6. 为什么这样设计，其他方案是什么

用户提出“有数字就不必查库”的问题后，可以让模型自行判断是否可信，但那会把执行边界交给概率输出。本课采用很窄的确定性提取条件，可测试、失败时回退到 Query。未来若需处理“上文那两个数”，先引入会话记忆和来源管理。

Tool Calling 不等于模型直接执行 Python 函数。模型提出 `tool_calls`；程序核对后调用本地工具。当前没有完整 ReAct 循环，也没有让模型自由规划任意 SQL。对两日比较，程序仍规定要查两个日期，模型分别提交受控工具参数。这保留了参考项目的 Query → Analysis 职责边界。

## 7. 实际验证与命令含义

在项目根目录执行：

```bash
UV_CACHE_DIR=/private/tmp/taobao-agent-uv-cache uv run --locked --offline python -m unittest discover -s tests -p 'test_*.py' -q
```

目的：运行本地单元与图集成测试，不发送模型请求。实际结果：**72 个测试通过，退出码 0**。新增用例覆盖：本轮明确金额直达 Analysis；含混金额不能绕过 Query；两日模型各提出一次正确工具建议；错误工具名、错日期、多余参数、多次或零次调用均不能访问数据库。它不能证明真实百炼模型总会提出正确建议。

另用固定输出假模型和本机第 13 课导入的真实 UCI SQLite 贯穿当前图：意图调用 1 次、工具建议 2 次；2010-12-01 为 **58960.79 英镑**，2010-12-02 为 **47748.38 英镑**，下降 **11212.41 英镑**、**19.02%**，退出码 0。这验证了程序和本机数据，模型输出仍由假模型代替。

可在你配置好 Plus Key 的终端重新运行 README 中的 UCI 双日命令。目的：验证**真实模型**的 Intent 和两次 Tool Calling 是否与本地测试相符。预期答复包含上述数字；若模型不提出合法工具建议，预期得到“查询未通过校验”的安全提示。该命令最多发送 3 次模型请求，可能消耗额度；**本课没有执行真实百炼请求**，因为本次工作环境未设置 Key。一次成功也不能证明长期准确率。

## 8. 本课相关知识

- **第九章，上下文工程**：Query 的模型只接收用户问题和当前要查的一个指标/日期，不把数据库路径、完整 State 或无关历史塞进提示。这是最小必要上下文。
- **第十章，通信协议**：这里是同进程 LangGraph 节点交接加模型 Tool Calling、本地 Python 工具执行；没有跨进程 MCP 或 A2A 的需求。
- **第十二章，评估**：测试把“工具选择正确”“参数正确”“执行结果正确”和“分析表述有证据”拆开。72 个测试是代码回归，不是业务问法成功率；真实模型还需在固定问法集上单独统计。

## 9. Agent 岗位面试知识点

这是**常见技术知识点与模拟面试题**，不是某家公司真实面经：

- 为什么 Supervisor 选了 Analysis，流程仍可能先经过 Query？因为 Analysis 是最终目标，Query 提供缺少的取数证据。
- 模型输出工具参数后为什么还要校验？因为模型输出是候选请求，权限和口径必须由程序控制。
- 工具结果为什么还要校验？因为它跨越工具到专家的边界；日期、指标错位会导致看似合理的错误分析。

## 10. 已知限制与下一课前置条件

当前只支持 UCI 的“交易明细金额”单日查询与双日总额比较，没有维度拆解、访客/转化率等归因证据。用户金额捷径只识别本轮明确格式；没有跨轮记忆。Qwen 在线 Tool Calling 还未验证。下一课可先用真实模型验证这条受控调用链并记录成功/失败样例，再沿参考项目要求增加能解释波动的分组数据与分析能力；引入更复杂知识召回前，应先明确所需指标和可信口径。
