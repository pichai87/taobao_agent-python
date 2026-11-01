# 第 32 课：语义层与统一 Query

## 1. 解决什么问题

用户说“GMV”，数据库列名却是 `pay_amount_cents`。语义层把业务名称、字段、单位、筛选和上游关系集中登记，Query 不猜字段。

## 2. 架构位置

在 `IntentResult` 与 SQL 之间：Intent 提取用户要什么，Query 查指标目录、规则、能力和片段，构建 SQL 后再进 SQL Validator Gate。最终只在 `commerce_daily` 上查询；该表已经由多表 ETL 生成。

## 3. 本次文件与方法

- `examples/commerce_knowledge.json`：增加日报六列的上游映射。
- `knowledge.py`：`SemanticModel` 和 `validate_catalog()` 检查映射完整，并与指标目录相符。
- `agents/query_agent.py`：保留一条模型工具循环，没有复制“旧库/新库”两套 Query。

## 4. 调用链与数据载体

用户问句 → `IntentResult` → `QueryRequest` → `MetricDefinition` 与 `KnowledgeBase` → 参数化 `MetricQuery` → SQL Gate → SQLite → `QueryEvidence`。`QueryEvidence` 是 Query 交给 Analysis 的唯一数值证据。

## 5. 关键执行过程

模型可在批准的知识工具中选择顺序：`search_metrics`、`rule_search`、按需 `cap_search`、`snippet_search`。依赖齐全后才能 `build_sql` 和 `execute_query`；参数、指标、日期、维度由程序逐项核对。

## 6. 为什么这样设计

**原文目标**是 NL→MDL→SQL；这里用本地可审查 JSON 实现最小语义层，属于**必要工程补充**。WrenAI 是可选替代，不在当前功能链；引入它之前应先有多指标口径和独立评测证据。SQL 片段仍需与批准模板逐字一致，避免知识文件绕过门禁。

## 7. 测试与限制

最终 47 项离线测试通过，覆盖目录不一致、参数篡改、工具跳步、分组合计和 SQL 形状。尚未对本轮改动运行真实 Qwen 或陌生问法评测，留到第 37 课。

## 8. 相关知识

第九章的按需上下文：只把当前指标的规则和片段送入 Query。第十章的 Tool Calling：模型提出工具调用，Python 函数执行；本地同进程无需 MCP。

## 9. 面试知识点

解释 NL2MDL2SQL 与直接 Text-to-SQL 的取舍，以及为何语义层是业务口径的授权边界。这是常见技术知识点。

## 10. 下一课前置条件

理解 `MetricDefinition`、`MetricQuery`、`QueryEvidence` 的数据流；第 33 课检查指标从上游表到结果的血缘。
