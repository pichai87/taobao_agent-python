# 第 24 课：小型业务 Golden Dataset 与评测命令

## 1. 本课解决的问题

单元测试使用固定的假模型，无法告诉我们真实百炼模型是否识别对意图、选对专家、按顺序调用工具。本课加入七道固定业务题和 `taobao-eval` 命令，让真实模型结果可以逐题检查。

## 2. 架构位置

PDF 的评测体系包含 Golden Dataset、按意图评分和节点归因。本课先落地与当前 UCI 能力相符的可核验部分：意图、日期、指标、维度、专家和答案关键事实。没有照搬完整 6D 评分、LLM Judge 或外部基准。

## 3. 修改文件与关键方法

- `scripts/eval/datasets/default.json`：七道业务题及期望行为，覆盖取数、比较、分析选择、元数据、组合任务、不支持的维度和未知指标。
- `evaluation.py`：`GoldenCase`/`GoldenDataset` 校验题集；`score_case()` 分项打分；`evaluate_dataset()` 运行全题并记录耗时和错误类型；`main()` 提供命令入口。
- `pyproject.toml`：注册 `taobao-eval`。
- `tests/test_evaluation.py`：验证题集可读、评分项可区分、单题异常不终止整套评测。

## 4. 调用链与数据流

JSON 题集 → `GoldenDataset` → 每题调用现有 `run_workflow()` → `WorkflowResult(intent,next_expert,answer)` → `score_case()` 比较期望 → `CaseOutcome(checks,elapsed_ms)` → JSON 报告。评测复用当前主图和同一份 UCI 数据，不建另一套 Agent。

## 5. 关键执行

每题独立运行。检查项分别是：主次意图集合、最终专家、指标名、日期、维度、答案必须包含的事实，以及不得包含的金额。错误只记录异常**类型**，避免把私有错误详情或密钥写进报告。若任一题失败，命令退出码为 1；全部通过为 0。报告记录每题耗时毫秒数，暂不统计 Token 和费用。

## 6. 设计取舍

可验证的日期和金额使用精确断言，不用另一个模型当裁判；七题规模小，便于改提示词或代码后重复运行。完整答案质量、推理合理性、因果归因和行业因素命中目前没有充分数据，不能用这个分数代替 PDF 的完整 6D 评测。

## 7. 测试结果与在线运行

已运行 `UV_CACHE_DIR=/private/tmp/taobao-uv-cache uv run --locked --offline python -m unittest discover -s tests -p 'test_*.py' -q`：**89 个本地测试通过，退出码 0**，证明题集格式、评分逻辑和原有回归测试通过。已运行 `UV_CACHE_DIR=/private/tmp/taobao-uv-cache uv run --locked --offline taobao-eval --help`：命令入口正常，退出码 0。已用 `python -m json.tool` 验证题集 JSON 格式。它们**不能证明真实模型通过七题**；本次环境没有 `DASHSCOPE_API_KEY`，未发送在线请求。

你可以在已准备好 Plus Key 的同一终端、项目根目录运行：

```bash
TAOBAO_UCI_DB_PATH=data/uci_online_retail/online_retail.sqlite DASHSCOPE_API_KEY="$DASHSCOPE_PLUS_API_KEY" DASHSCOPE_MODEL=qwen-plus uv run taobao-eval
```

**目的**：让真实模型逐题走当前 Agent，输出每题哪些检查通过。**预期**：JSON 中 `total=7`；`passed` 由真实表现决定，不能预先保证是 7。命令会产生多次模型请求；失败时退出码 1。报告不包含 Key。无需 Navicat 或新数据库安装。

## 8. 本课知识

第十二章：单元/集成测试检验程序约束，Golden Dataset 检验真实 Agent 的业务行为；两者不能互相代替。本课的字符串与字段检查是确定性评测，不是 LLM Judge。第九章：固定参考日期避免相对时间随运行日改变。第十章：工具调用是否成功会体现在取数题结果，但当前报告还没有逐工具调用轨迹和 Token 计数。

## 9. 面试知识点

常见技术问题：Agent 评测为何要分意图、路由、答案，而不是只看最终文字？分项结果可以定位错误发生在哪层；最终答案碰巧正确不能证明中间过程可靠。这里是模拟讲解题。

## 10. 下一阶段前置条件

先运行一次在线七题评测并保存不含密钥的报告；根据失败项优先修 Intent、Tool Calling 或回答路径。再获取可信维度及漏斗数据，推进 PDF 的真实波动归因。七题通过仍不代表完整项目可靠。
