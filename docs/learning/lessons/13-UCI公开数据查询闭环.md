# 第 13 课：把一条公开数据查询走到底

先看结果：现在已有的 Agent 图可以查询 **UCI Online Retail** 的本地副本。问“2010-12-01 交易明细金额是多少？”时，固定输出的假模型经过完整主图，查询到 **58960.79 英镑**。同一天直接从原始 Excel 独立汇总，也得到相同结果。真实 Plus 模型对这句新问题的识别仍需在线验证。

## 1. 本课解决的问题

前几课的 Query 只查项目编造的 6 笔订单。本课把一个可核对来源的公开数据集导入、定义清楚指标、接进原有 Query，再核对最终金额。这是一课完成的业务功能，不是再为每个内部步骤各开一课。

[UCI 官方数据页](https://archive.ics.uci.edu/dataset/352/online+retail)注明这是一家英国在线零售商 2010-12-01 至 2011-12-09 的交易明细，许可为 CC BY 4.0。文件有发票号、日期、数量和英镑单价；发票号以 `C` 开头代表取消。它没有支付状态，也没有淘宝补贴字段。所以本课新指标叫**交易明细金额**：只合计未标记取消、数量大于 0 且单价大于 0 的行，不能称为“已支付 GMV”。

## 2. 在整体 Agent 架构中的位置

```text
用户问题
  → Intent：提取“交易明细金额”和 2010-12-01
  → Supervisor：选择 query
  → 原有 Query 节点：查本课独立指标规则
  → 本地 UCI 查询工具：检查数据来源与日期范围
  → 原有 SQL 构建器 → 原有 Validator Gate → 只读 SQLite
  → State.answer：明确写出英镑金额、数据来源和非支付口径
```

LangGraph 没增加节点或边。第 13 课是在**同一个 Query 专家**后面增加一个可选数据源。`TAOBAO_UCI_DB_PATH` 不设置时，默认行为和第 12 课一样；合成数据模式仍独立存在。

## 3. 修改的文件、类和关键方法

| 文件 | 本次作用 |
| --- | --- |
| `src/taobao_agent/uci_online_retail.py` | `import_uci_online_retail()` 从 Excel 导入本地 SQLite；`query_uci_metric()` 做来源、指标、日期和 SQL 检查后取数；`main()` 提供导入命令 |
| `examples/uci_online_retail_metrics.json` | 独立定义“交易明细金额”：求和 `line_amount_pence`，按 `invoice_date` 和 `record_type=positive` 筛选 |
| `src/taobao_agent/query_agent.py` | `answer_query()` 在显式 UCI 模式下调用工具并将便士格式化为英镑 |
| `src/taobao_agent/workflow.py` | 从环境变量取得数据库路径，载入独立指标规则，继续走原有 Query 节点 |
| `pyproject.toml`、`uv.lock` | 加入 `openpyxl`，用于读取官方 `.xlsx`；增加 `taobao-import-uci` 命令 |
| `.gitignore` | 忽略本地 `data/`，防止原始 Excel 和导出的数据库进入仓库 |
| `tests/test_uci_online_retail.py`、`tests/test_workflow.py` | 验证导入、计算、门禁、日期范围和主流程 |
| README、学习路线、学习记录 | 给出操作步骤、实际结果和下一步 |

## 4. 调用链、参数、State 和返回值

导入是**准备数据**，只需做一次：官方 Excel 路径 → `import_uci_online_retail(source_path, database_path)` → SQLite 文件。导入结果列出正向、取消、其他行数、日期范围和原始文件 SHA-256。SQLite 只存每行的日期、行类型和便士金额；没有存客户号、商品描述或发票号。

查询时，主命令用 `TAOBAO_UCI_DB_PATH` 指向 SQLite，再把“交易明细金额”的内置目录和数据库路径交给 `build_workflow()`。Intent 节点把模型输出写进 `State.intent`；Supervisor 写入 `State.next_expert=query`；Query 从 Intent 的 `curr` 取日期，并调用 `query_uci_metric(metric, curr, database_path)`。工具返回整数 `5896079`，单位是便士。Query 格式化为 `58960.79 英镑`，写入 `State.answer`，最后由 `WorkflowResult.answer` 输出。

这里**没有新增 LangGraph State 字段**：数据库路径是运行配置，指标是 Query 的可信输入，查询结果仍用原来的 `answer` 返回。

## 5. 关键代码怎样执行

导入器逐行读取 Excel。它把以 `C` 开头的发票列为取消，把数量或单价不大于 0 的非取消行列为“其他”，剩下的是正向明细。每行金额用 `Decimal(数量 × 单价 × 100)` 四舍五入到整数便士，再写入 SQLite。原始文件中有 4 条正向记录产生不足整数便士的行金额；本课的**逐行便士四舍五入**是明确的工程口径，因此最终金额不应被理解为未经处理的原始浮点求和。数据库记录导入日期范围，已存在的目标文件不会被导入命令覆盖。

查询器先检查固定指标规则是否仍指向批准的表、列和 `record_type=positive`，然后以 SQLite `mode=ro` 打开数据库，并设置 `PRAGMA query_only=ON`。它核对导入标记与日期范围，再把表的实际列与代码批准列取交集，交给第 11 课的 `build_metric_query()` 和 `validate_metric_query()`。通过之后才执行带绑定参数的 `SELECT SUM`。这只是**当前固定模板的 SQL 门禁**，不是任意 SQL 的通用安全审查。

Query 只回答单日、无比较、无范围、无分组、无第二任务的这一个指标。数据集范围外的日期会明确提示覆盖范围，不会把 2026 年的“没有数据”回答为 0 英镑。回答始终标记 UCI、英镑和非支付口径。

## 6. 设计原因与主要替代方案

- **原文要求**：Query 沿指标语义、SQL 构建、Validator Gate、数据执行返回结果。
- **必要工程补充**：没有淘宝真实表时，先用 UCI 官方文件完成一条可复核的公开数据查询；因为 UCI 没有支付状态，另建指标，避免污染合成 GMV 定义。原始数据和导出的数据库均留在本机，不推送仓库。
- **本次没有采用的替代方案**：把 UCI 交易金额直接叫 GMV，或让模型自由编写 SQL。前者会把没有支付状态的数据伪装成支付口径；后者在当前只有一种固定查询形状时增加难以验证的行为。

这不是新 Agent、RAG 或 MCP。导入器和查询器是普通 Python 函数；只有 Intent 使用一次 LLM 调用。仓库内的指标 JSON 是当前可信的静态定义，不是外部动态检索结果。

## 7. 实际测试结果和已知限制

在项目根目录实际运行：

```bash
UV_CACHE_DIR=/private/tmp/taobao-agent-uv-cache uv run --locked --offline python -m unittest discover -s tests -v
```

**目的**：不用 API Key 验证本课和既有行为。**结果**：59 个测试通过，退出码 0。测试覆盖导入筛选、同日金额、已有数据库不覆盖、未批准指标拒绝、缺失数据库不新建、范围外日期不冒充 0、Intent → Supervisor → Query 交接和 CLI 配置。它不能证明真实模型一定识别新指标。

```bash
UV_CACHE_DIR=/private/tmp/taobao-agent-uv-cache uv sync --locked --offline
```

**目的**：核对锁文件与本地依赖。**结果**：解析 47 个包、检查 46 个已安装包，退出码 0。它不证明 UCI 下载文件正确，也不调用模型。

实际取得 [UCI 官方下载包](https://archive.ics.uci.edu/static/public/352/online+retail.zip)，解出 `Online Retail.xlsx`，其 SHA-256 为 `43465a06f2ccf7c8b5bd2892bc7defb52f97487934fe93b16ae4c3936424676d`。运行 `uv run --locked --offline taobao-import-uci 'data/uci_online_retail/Online Retail.xlsx' 'data/uci_online_retail/online_retail.sqlite'` 的**目的**是把原始文件导入本地 SQLite；实际得到正向 530104 行、取消 9288 行、其他 2517 行，日期覆盖 2010-12-01 至 2011-12-09。它只验证这份文件按本课规则完成导入，不证明这些交易属于淘宝。

我另从 Excel 直接扫描 2010-12-01 的 3072 条正向明细，得到 `5896079` 便士；独立的 SQLite 查询也得到 `5896079` 便士。固定输出的假模型经**真实导入的数据库和完整主图**返回 `58960.79 英镑`。这些检查证实本课计算与接线一致；尚未用 Plus Key 测这句新问法，也没有测模型对多种说法的识别率。

如果在同一终端已设置 Plus Key，可运行：

```bash
env -u TAOBAO_METRIC_CATALOG_PATH -u TAOBAO_DEMO_DATA TAOBAO_UCI_DB_PATH=data/uci_online_retail/online_retail.sqlite DASHSCOPE_API_KEY="$DASHSCOPE_PLUS_API_KEY" DASHSCOPE_MODEL=qwen-plus uv run taobao-agent '2010-12-01 交易明细金额是多少？'
```

**目的**：实际检查模型提取指标和日期，再沿同一 Query 链路取数。**预期**：`next_expert=query`，`answer` 含 `58960.79 英镑`。这会发一次在线模型请求；本次未在当前环境使用你的 Key，因此不能报告为已通过。若结果不同，请反馈**不含 Key**的 JSON。无需安装 MySQL、PostgreSQL 或 Navicat；这里使用 Python 自带 SQLite。`openpyxl` 只负责 Excel 导入。

## 8. 本课涉及的相关知识

- **LangGraph**：复用已有的 `State.intent`、条件路由和 Query 节点；本课无需新图 API。
- **Hello-Agents 第九章，上下文工程**：只把指标定义、日期和数据源配置交给 Query；不把 54 万行明细塞进模型上下文。结果写明来源与口径，避免模型或使用者把它误当淘宝 GMV。
- **第十章，通信协议**：Query 同进程调用普通 Python 查询函数，不需要 MCP 或 A2A；模型也没有自主 Tool Calling。
- **第十二章，评估**：小型工作簿单元测试检查计算边界，主图测试检查交接，官方 Excel 的独立核算检查真实导入结果。它们与尚待进行的在线 Agent 业务评测是不同层次。

## 9. Agent 岗位面试知识点

这是**常见技术知识点和模拟题，不是公开面经**：“拿到没有支付状态的公开零售明细，怎样避免 Agent 把它回答成已支付 GMV？”要能解释指标定义先于 SQL、字段语义和币种要核对、取消与非正行要显式处理、答案附来源和限制、测试需要独立口径核算。工程上还要说明为什么把 54 万行放数据库而不是模型上下文。

## 10. 下一阶段的前置条件

能说清“官方 Excel → 本地 SQLite → 内置指标目录 → Intent.curr → Query → SQL 门禁 → 英镑回答”，以及它和第 12 课合成 GMV 的口径差别。先用 Plus Key 核对一条真实模型输出；再根据识别情况决定下一课扩展比较、分组或 Analysis。真实淘宝数据库、RAG、任意 SQL 校验与网页仍是后续工作。
