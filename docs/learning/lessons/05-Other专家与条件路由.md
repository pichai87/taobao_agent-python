# 第 5 课：让 Other 专家真正回复

先看结果：输入“你好”时，Intent 先把它识别为 `other`，Supervisor 选择 Other，主图再执行 Other 节点并返回一句回复。输入“昨天 GMV 比前天为什么下降？”时，主图仍只选择 `analysis`，`answer` 为 `null`。

## 1. 本阶段解决的问题

第 4 课的 `next_expert` 只是一个名字；图运行到 Supervisor 就结束。本课只接入一个可执行分支，让“选择 Other”之后确实发生一次回复。原文 PDF 第 22 页将 `other_agent.py` 标为“兜底寒暄”，这是当前不依赖业务数据就能验证的最小任务。

## 2. 在整体架构中的位置

```text
用户问题 → Intent → Supervisor
                     ├─ other → Other 回复 → END
                     ├─ query → END（尚无查询专家）
                     └─ analysis → END（尚无归因专家）
```

这里的 **条件边** 就是“根据 `next_expert` 选择下一站”的图连接。它只决定执行顺序；Other 的回复逻辑在普通 Python 函数里。Ops 尚无对应意图和节点。

## 3. 本次修改的文件、函数与数据结构

| 文件 | 作用 |
| --- | --- |
| `src/taobao_agent/other_agent.py` | `answer_other(question)` 对简单寒暄给出回复；其余 `other` 问题提示用户说清指标和时间 |
| `src/taobao_agent/workflow.py` | `WorkflowState` 增加可选 `answer`；`WorkflowResult` 对外输出 `answer`；主图增加 Other 节点和条件边 |
| `tests/test_workflow.py` | 检查 Other 回复、非 Other 分支不回复、只调用一次模型以及 CLI 输出 |
| README、本课、学习路线、学习记录 | 说明运行目的、预期结果和当前边界 |

没有新增依赖，也没有新增第二张主图。

## 4. 调用链、参数、State 和返回值

以“你好”为例：

```text
main() 接收 question="你好"
  → run_workflow(question, model, reference_date)
  → Intent 节点调用 run_intent_once()，把识别结果写入 State.intent
  → Supervisor 节点读取 State.intent，写入 State.next_expert="other"
  → 条件边读取 next_expert，进入 Other 节点
  → Other 节点把 answer_other(State.question) 写入 State.answer
  → WorkflowResult 返回 intent、next_expert、answer
```

State 可以继续看成传给下一步的“小纸条”。`question` 是最初输入；`intent` 是已校验的识别结果；`next_expert` 是选择；`answer` 只在 Other 节点执行后出现。对于 `query` 和 `analysis`，图现在走到 END，因此对外的 `answer` 是 `null`。

## 5. 关键代码如何执行

`graph.add_conditional_edges("supervisor", route_after_supervisor, {...})` 在 Supervisor 完成后调用 `route_after_supervisor`。函数读取 `State.next_expert`，映射表把 `other` 送到 Other 节点，把 `query`、`analysis` 送到 END。随后 `graph.add_edge("other", END)` 让 Other 回复后结束。

`answer_other()` 目前是一个本地函数。它只把少量明确寒暄语句识别为问候，其他 `other` 问题返回澄清提示，不编造电商数值。Intent 仍调用模型一次；Other 不再调用模型。这个实现是原文 Other 职责的**最小落地**，不是完整的通用聊天专家。

## 6. 设计原因与主要替代方案

- **原文要求**：Other 承担兜底寒暄；Supervisor 之后由专家处理。
- **必要工程补充**：用条件边把“选择结果”变成实际执行路径；对还没有实现的专家返回空答案，避免把选择误认为处理结果。
- **替代方案**：Other 可以再调用一次 LLM 来自由回答，但当前只需安全、可验证的寒暄与澄清。本地函数足够完成这一小步，也便于先理解条件路由。以后若需求要求更丰富的回答，再依据样例和评估结果扩展。

## 7. 实际测试结果与已知限制

在项目根目录执行：

```bash
UV_CACHE_DIR=/private/tmp/taobao-agent-uv-cache uv run --locked --offline python -m unittest discover -s tests -v
```

这条命令的目的是用假模型检查本地调用链，不会消耗 API Key。**22 个测试通过，退出码 0**。新增测试验证“你好”进入 Other 后有回复且模型只调用一次；查询和归因仍得到 `answer=None`；CLI 能输出 Other 的答案。测试无法证明真实模型一定把寒暄识别为 `other`。

另执行 `UV_CACHE_DIR=/private/tmp/taobao-agent-uv-cache uv run --locked --offline taobao-agent --help`，确认 CLI 可启动，退出码 0；查看编译图节点得到 `__start__、intent、supervisor、other、__end__`。这两项也是本地检查，不验证模型输出质量。

要在你已设置 Plus Key 的终端验证真实链路，可运行：

```bash
DASHSCOPE_API_KEY="$DASHSCOPE_PLUS_API_KEY" DASHSCOPE_MODEL=qwen-plus uv run taobao-agent '你好'
```

这条命令会发送一次真实模型请求。预期 JSON 中 `intent.intent_type=other`、`next_expert=other`、`answer` 为非空字符串。第 5 课交付时工具进程没有你的 Key，**当时尚未执行在线验证**；后续用户验证结果见下一段。一次成功只说明这一句寒暄的链路可用，更多说法仍需要小型样例集验证。第 5 课交付时 Query、Analysis、Ops、业务数据和 SQL 均未接入。

**后续验证记录（2026-10-23）**：用户在自己的终端用 Plus Key 和 `qwen-plus` 运行上述命令，实际得到 `intent_type=other`、`next_expert=other`，以及“你好！你可以告诉我想分析的电商指标和时间范围。当前尚未连接业务数据。”。这确认了这一句的在线链路；第 6 课开始后，Query 的首个工具才接入。

## 8. 本课涉及的相关知识

- **LangGraph**：本课唯一主要新概念是条件边。它根据 State 中已有的值决定下一节点；节点仍只返回自己负责的 State 更新。
- **第九章，上下文工程**：Other 只读取原问题；没有把完整意图 JSON 或无关历史消息交给它。未来更复杂的专家再明确各自需要哪些上下文。
- **第十章，通信协议**：Supervisor 和 Other 在同一个 Python 进程内通过 State 交接，直接调用本地函数即可；当前没有外部工具协议需求。
- **第十二章，评估**：这里的 22 个测试是代码行为测试，其中 Other 分支测试验证确定的路由和回复。真实模型把问题分到哪类，还需要在线业务样例评测；没有使用 LLM Judge 或外部标准基准。

## 9. Agent 岗位面试知识点

这是常见技术知识点，**不是公开面经**：如何区分“选中了专家”和“专家执行完成”？结合本项目回答：`next_expert` 是路由决策；只有分支节点写入 `answer` 才产生回复。针对尚未实现的分支，保留 `answer=null`，使调用方知道当前只完成交接。面试时还可说明同进程节点为何直接传 State，以及如何测试没有误入另一分支。

## 10. 下一阶段的前置条件

先确认你能沿 `question → intent → next_expert → answer` 解释本次调用，并用自己的 Plus Key 验证一句寒暄。要让 Query 真正取数，需先准备可核实的指标口径、表字段和测试数据，并在实际执行 SQL 前加入最基本的只读限制与校验；Analysis 要以可信查询结果为输入。下一小步应根据这些前置资料确定，不能从空数据直接生成 GMV 下降原因。
