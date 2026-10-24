# 第 11 课：固定模板 SQL 与后置校验

先看结果：第 10 课的 GMV 规则现在能生成一条固定形状的只读 SQL。程序会再检查这条 SQL、日期参数和表字段，测试才把它交给合成数据表执行。2026-10-22 算出 20000 分，即 200 元。主 Agent 暂时仍不查询数据库。

## 1. 本课解决的问题

指标文件已经写明“按支付日期、只算已支付订单、金额求和”，但 Query 还没有一个可控制的查询语句。直接拼接用户问题或信任任意 SQL 都缺少边界。本课只实现**当前 GMV 样例所需的一种查询形状**，并让执行前的门禁可以拒绝被改动的语句。

## 2. 在整体架构中的位置

```text
指标 JSON → MetricDefinition ─┐
指定日期 ───────────────────────┼→ build_metric_query() → 候选 SQL + 参数
可信表字段清单 ────────────────┘                         ↓
                                      validate_metric_query() → 测试中的 SQLite

当前主图：用户 → Intent → Supervisor → Query（仍只报告指标定义）
```

原文要求的 SQL Validator Gate 位于查询执行之前。本课实现的是**固定模板的小门禁**，不是原文完整 SQL 安全系统；外部数据库权限、任意 SQL 解析和主图查询仍未实现。

## 3. 修改文件、类与关键函数

| 文件 | 本课变化 |
| --- | --- |
| `src/taobao_agent/metric_sql.py` | 新增 `MetricQuery`、`build_metric_query()`、`validate_metric_query()` |
| `tests/test_metric_sql.py` | 验证合法查询、空日期、写操作、追加语句、参数篡改和越权字段 |
| README、学习路线、学习记录 | 记录功能边界、验证和安装要求 |

没有更改 LangGraph 图、Query 节点或第 9、10 课里你写的学习笔记，也没有新增依赖。

## 4. 调用链、参数、State 和返回值

测试先用现有 `load_metric_catalog()` 读取 `examples/demo_metrics.json`，得到 `MetricDefinition`。然后调用 `build_metric_query(metric, date(2026, 10, 22))`，返回 `MetricQuery(statement, parameters)`；其中 `parameters=("2026-10-22", "paid")`。再把候选、原指标、原日期和可信字段清单交给 `validate_metric_query()`。门禁通过才返回同一个查询对象，测试调用 `sqlite3.execute(statement, parameters)`，得到整数 `20000`。

这些调用目前都在测试里，没有进入 `WorkflowState`。将来 Query 接入时，才会从 Intent 的日期取得 `business_date`，并且每次执行前都经过门禁。

## 5. 关键代码怎样执行

构建器只生成这种结构：

```sql
SELECT COALESCE(SUM("order_amount_fen"), 0) AS value FROM "demo_orders"
WHERE "paid_date" = ? AND "payment_status" = ?
```

`?` 是参数占位符：日期和 `paid` 作为独立参数交给 SQLite，而不是插进 SQL 文本。表名、列名不能用 `?` 绑定，因此构建器只接受以字母或下划线开头、后续由字母数字下划线组成的简单标识符，并给它们加双引号。没有计算规则时直接报错。`COALESCE(..., 0)` 让没有订单的日期返回 0。

**后置门禁**重新按同一指标和日期构造预期语句，要求候选 SQL 与两个参数完全一致；同时检查表和三个字段都在调用方提供的批准清单中。因此把 SQL 改为 `DELETE`、追加另一条语句、把日期换掉，都会被拒绝。这个方法适合当前唯一模板，不应被当作可审核任意模型 SQL 的解析器。

## 6. 设计原因与替代方案

- **原文要求**：业务规则参与 SQL 构建，执行前要经过 SQL Validator Gate。
- **必要工程补充**：用可审核的固定模板和参数绑定验证一条取数路径；在用户尚无真实库时，让测试用合成表提供可核对答案。
- **替代方案**：引入通用 SQL 解析器或由模型输出任意 SQL。当前只有一个 GMV 问法，通用解析会增加无关复杂度；任意 SQL 还需要更完整的权限、方言和安全设计。

批准字段清单在测试里明确给出，**真实系统必须由可信 MDL/表结构与数据库权限提供**。此门禁不证明指标业务口径真实，也不能代替数据库账号的只读权限。未来若允许更多 SQL 形状，需要扩展或替换校验规则。

## 7. 运行验证与已知限制

在项目根目录执行：

```bash
UV_CACHE_DIR=/private/tmp/taobao-agent-uv-cache uv run --locked --offline python -m unittest discover -s tests -p 'test_metric_sql.py' -v
```

目的：只检查本课的 SQL 构建、门禁和合成表计算。实际 **6 个测试通过，退出码 0**。其中固定日期得到 200 元、无订单日得到 0；修改 SQL、参数和批准字段均被拒绝；带有 `OR 1=1` 的条件值仍作为参数，不会改变 SQL 结构。这些测试不证明主 Agent 会取数，也不证明能安全处理任意 SQL。

执行 `UV_CACHE_DIR=/private/tmp/taobao-agent-uv-cache uv run --locked --offline python -m unittest discover -s tests -v`：**42 个测试通过，退出码 0**。执行 `UV_CACHE_DIR=/private/tmp/taobao-agent-uv-cache uv sync --locked --offline`：解析 45 个包、检查 44 个已安装包，退出码 0；无新增依赖。执行 `UV_CACHE_DIR=/private/tmp/taobao-agent-uv-cache uv run --locked --offline taobao-agent --help`：退出码 0，只检查入口。

**软件提醒**：上述 SQLite 数据库只存在于测试内存中，使用 Python 自带的 `sqlite3`。本课无需安装 MySQL、PostgreSQL、Navicat 等工具。将来接入外部数据库前，会在对应课程先说明软件、账号权限和安装步骤。

## 8. 本课相关知识

- **LangGraph**：没有新节点、边或 State 字段。SQL 模块将来由已有 Query 节点调用。
- **第九章，上下文工程**：Query 只需拿到相关指标定义和日期；可信字段清单是另一个输入，不能让模型自行臆造。
- **第十章，通信协议**：当前都是进程内 Python 函数，未使用 MCP 或 A2A。
- **第十二章，评估**：同时验证预期结果和故意改坏的 SQL，属于计算正确性与安全边界测试；在线 Agent 业务评测仍待接入 Query 后进行。

## 9. Agent 岗位面试知识点

这是**常见技术知识点与模拟题**，不是公开面经：“已有结构化指标规则，为什么 SQL 执行前还要校验？”可以用本课回答：构建过程和调用方都可能出错；后置门禁复核最终语句、参数和表字段，数据库只读权限则提供另一层执行边界。固定模板门禁的覆盖范围必须明确。

## 10. 下一课的前置条件

能解释 `?` 与 `parameters` 的关系，以及候选 SQL 为什么必须先过门禁。下一课才考虑让 Query 在**明确启用合成数据模式**时调用本地查询工具；执行工具应只接收通过门禁的查询，并使用受限的只读连接。公开数据与真实库仍需单独核实来源和权限。
