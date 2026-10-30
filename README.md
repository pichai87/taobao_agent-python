# 电商数据分析 Agent

基于《淘宝百亿补贴数据分析助手 Agent 实战》逐步实现的 Python + LangGraph 学习项目。

当前运行代码只有一张 LangGraph 图：**Intent 识别问题 → Supervisor 选择专家 → Query 查指标与取数 → 按需交给 Analysis**。Query 可处理同一指标的单日或多个指定日期；Analysis 接收整组查询证据，由模型选择受限分析任务、Python 计算数值。当前数据源仅批准 UCI 的“交易明细金额”，总额变化还不能证明下降原因。第 18 课的“从用户问题里匹配两笔金额并跳过 Query”已移除。阶段 2 的单节点练习位于[历史样例](docs/learning/archive/02_minimal_agent.py)，不参与当前运行。

## 环境与验证

```bash
uv sync
uv run python -m unittest discover -s tests -v
```

第 24 课新增七题业务评测入口。准备好本地 UCI SQLite 和 Plus Key 后，在同一终端运行 `TAOBAO_UCI_DB_PATH=data/uci_online_retail/online_retail.sqlite DASHSCOPE_API_KEY="$DASHSCOPE_PLUS_API_KEY" DASHSCOPE_MODEL=qwen-plus uv run taobao-eval`。它逐题报告意图、路由和答案事实检查；失败退出码为 1，会产生多次模型请求。当前环境尚未运行在线七题评测。详见[第 24 课](docs/learning/lessons/24-业务Golden评测入口.md)。

## 设置模型 Key

原文使用通义千问。项目通过阿里云百炼的兼容接口调用模型，默认模型是 `qwen-turbo`。在**运行项目的同一个终端**设置 Key；`read -s` 会隐藏输入，按回车结束：

```bash
read -s DASHSCOPE_API_KEY
export DASHSCOPE_API_KEY
```

密钥只从环境变量读取，不要写入项目文件或提交到仓库。若密钥所属地域不是北京，可另设 `DASHSCOPE_BASE_URL` 为该地域的兼容接口地址；如需指定其他已开通模型，可设 `DASHSCOPE_MODEL`。未提供密钥时，CLI 会提示设置 `DASHSCOPE_API_KEY` 并退出。

如果你有 Turbo 和 Plus 两个 Key，可在同一个终端分别输入。`read -s` 不会显示输入内容；每执行一次命令只会传入**一个** Key：

```bash
read -s DASHSCOPE_TURBO_API_KEY
read -s DASHSCOPE_PLUS_API_KEY

DASHSCOPE_API_KEY="$DASHSCOPE_PLUS_API_KEY" DASHSCOPE_MODEL=qwen-plus uv run taobao-agent '昨天 GMV 比前天为什么下降？'
```

这条命令只使用 Plus Key。若收到 `429 limit_requests`，请稍等几分钟后单次重试，并在百炼控制台查看模型监控和限流。不同 Key 若属于同一账号，换 Key 本身不能保证解除同一模型的限流。不要把 Key、包含 Key 的命令展开结果或终端环境导出内容发给别人。

## 主命令做什么

上面的 GMV 命令在没有可查询数据源时，只调用模型**一次**以得到意图 JSON。Supervisor 在程序内部选专家；主意图应是 `attribution`，`next_expert` 应是 `analysis`，`answer` 会说明缺少指标定义或可查询的数据源。配置 UCI 数据库后，Query 会让同一个模型在受控工具循环中依次查指标、查规则、构建 SQL、查询所需日期；模型请求次数取决于它如何安排工具调用，不能固定按日期计算。

目前的对应关系：取数或查表结构 → `query`；波动归因 → `analysis`；其他问题 → `other`。原文还有 Ops 专家，但当前没有对应的已验证意图类型，因此暂不分配给 Ops。

要检查第一个可执行分支，可在已设置 Plus Key 的同一个终端运行：

```bash
DASHSCOPE_API_KEY="$DASHSCOPE_PLUS_API_KEY" DASHSCOPE_MODEL=qwen-plus uv run taobao-agent '你好'
```

这条命令的目的是验证真实模型把“你好”识别为 `other`，然后主图进入 Other 节点。预期结果包含 `next_expert=other` 和非空 `answer`；它会发送一次模型请求。若模型分错类，实际结果可能不同，请把**不含 Key 的 JSON 输出**作为反馈。一次成功只证明这句寒暄的链路可用，不能证明所有其他问题都能正确分类或回复。

你已在 2026-10-23 用 Plus Key 验证这句“你好”：真实输出为 `intent_type=other`、`next_expert=other`，并得到 Other 的回复。这仍是一条样例，不是所有问候语的准确率。

要检查 Query 的第一步，可在同一终端运行：

```bash
DASHSCOPE_API_KEY="$DASHSCOPE_PLUS_API_KEY" DASHSCOPE_MODEL=qwen-plus uv run taobao-agent '昨天 GMV 多少？'
```

这条命令的目的是验证 Intent → Supervisor → Query → `search_metrics` 的真实调用链，会发送一次模型请求。**未设置指标目录文件时**，预期 `next_expert=query`，`answer` 提示“尚未配置 GMV 的指标定义，无法查询数据”。它不会产生 SQL，也不会给出 GMV 数值。只有模型把问题识别为取数意图时才会走到 Query；若输出不同，请提供不含 Key 的 JSON 结果。

Query 的指标查找现经由一个小型 Harness 错误边界：目录查找超时或连接失败时，`answer` 会提示“指标目录暂时不可用”，不会展示内部异常详情。默认目录为空；超时和断线行为目前由本地测试模拟。配置 UCI 数据库后，Query 才调用固定模板门禁和本地只读查询工具。

## 指标目录文件

第 23 课支持一个明确的组合问法：“交易明细金额怎么计算？2010-12-01 是多少？”Intent 若识别出 `table_meta + data_query`，Supervisor 会把两项放入 `State.task_plan`，Query 依次回答口径和数值；其他尚未支持的组合会明确提示。详见[第 23 课](docs/learning/lessons/23-Supervisor组合任务计划.md)。

现在也可以问“交易明细金额使用什么表和字段、怎样计算？”。Intent 若识别为 `table_meta`，Query 会在无需日期的情况下核对已批准指标与本地 SQLite 字段，再说明口径；未启用 UCI 数据源时仅说明目录记录尚未由数据库核实。详见[第 22 课](docs/learning/lessons/22-Query指标口径与表字段.md)。

如果以后取得经过人工核对的指标定义，可将它们放在一个 JSON 文件中，并在运行命令的终端设置 `TAOBAO_METRIC_CATALOG_PATH` 为该文件路径。文件内容是列表，每项包含 `name`、`table`、`field`、`source`；可选的 `calculation` 记录当前支持的 `sum`、日期字段、一个等值条件和单位。当前项目**没有提供真实指标文件**；第 8 课有使用虚构表名的格式示例，第 10 课解释计算规则。

主命令会在调用模型前读取并检查文件。文件不存在、字段缺失或指标名重复时会退出并提示配置错误。加载成功仅表示格式符合要求，**不证明字段真实存在或业务口径正确**；普通模式下主命令不会据自定义目录生成 SQL 或访问数据库。

## 合成数据测试样例

`examples/demo_shop.sql` 是手工编造的 6 笔订单，`examples/demo_metrics.json` 把 GMV 映射到样例表的金额字段，并记录 `sum`、`paid_date`、`payment_status=paid` 和单位“分”。它们不是淘宝真实数据，也不是公开数据集的摘录。2026-10-21 与 2026-10-22 的样例 GMV 分别为 300 元和 200 元。第 17 课起，这些文件仅供离线测试；主命令不再提供教学数据查询模式。

在项目根目录运行 `UV_CACHE_DIR=/private/tmp/taobao-agent-uv-cache uv run --locked --offline python -m unittest discover -s tests -p 'test_demo_data.py' -v`，可以验证样例载入 SQLite 和固定日期的计算结果。这不调用模型，也不改变主命令；第 9 课解释了测试所用 SQL 和后续公开数据接入计划。

测试使用 Python 自带的 `sqlite3` 在内存中建立合成数据库，**无需安装数据库服务或 Navicat**。以后如需外部数据库或图形管理工具，会先在对应课程文档说明用途、版本与安装方式；Navicat 属于可选的查看工具。

第 11 课新增 `metric_sql.py`：它根据指标规则与指定日期生成一种带 `?` 参数的只读 `SELECT SUM`，再由 `validate_metric_query()` 核对 SQL、参数及批准的表字段。当前 UCI 查询继续使用这个门禁；合成 GMV 样例也用于门禁测试。它只覆盖当前固定模板，尚不是任意 SQL 的通用校验器。

## 第 12 课的历史运行模式

第 12 课曾让主命令查询合成 GMV；你已经用 Plus Key 验证过那条历史链路。第 17 课按你的决定移除了这项运行模式。旧命令若设置 `TAOBAO_DEMO_DATA=1`，现在会在调用模型前提示模式已移除，不会访问合成订单。旧课文档保留当时的学习过程；当前可运行的数据查询命令见下方 UCI 示例。

## 第 13 课：用 UCI 公开数据完成查询

数据来自 [UCI Online Retail](https://archive.ics.uci.edu/dataset/352/online+retail)，许可为 CC BY 4.0。其日期为发票生成日期，只有数量、单价和取消标记，没有支付状态或淘宝补贴字段。本项目的“交易明细金额”口径是：排除取消发票、数量不大于 0、单价不大于 0 的行，再按日期汇总 `数量 × 单价`；它**不是已支付 GMV**。金额单位为英镑，日期范围为 2010-12-01 至 2011-12-09。

若本机 `data/uci_online_retail/` 已有第 13 课导入的文件，直接执行下方查询命令。新克隆仓库时，在项目根目录下载官方数据并导入一次：

```bash
mkdir -p data/uci_online_retail
curl -fL 'https://archive.ics.uci.edu/static/public/352/online+retail.zip' -o 'data/uci_online_retail/online+retail.zip'
unzip -n 'data/uci_online_retail/online+retail.zip' -d data/uci_online_retail
shasum -a 256 'data/uci_online_retail/Online Retail.xlsx'
uv run taobao-import-uci 'data/uci_online_retail/Online Retail.xlsx' 'data/uci_online_retail/online_retail.sqlite'
```

目的：取得官方 Excel、核对文件、导入只保留查询所需字段的 SQLite 副本。我们实际下载的 Excel SHA-256 为 `43465a06f2ccf7c8b5bd2892bc7defb52f97487934fe93b16ae4c3936424676d`；若你下载后的值不同，先核对 UCI 文件版本，不要直接套用本课的数值。导入预期输出：正向明细 530104 行、取消 9288 行、其余 2517 行。导入命令**不会覆盖已有数据库**；再次导入请使用新的目标路径。Excel 和 SQLite 都在被 Git 忽略的 `data/` 下，不会被提交。`openpyxl` 是读取 Excel 的唯一新增依赖；SQLite 属于 Python 标准库，**无需安装数据库服务或 Navicat**。

准备好 Plus Key 后，在同一个终端运行：

```bash
env -u TAOBAO_METRIC_CATALOG_PATH -u TAOBAO_DEMO_DATA TAOBAO_UCI_DB_PATH=data/uci_online_retail/online_retail.sqlite DASHSCOPE_API_KEY="$DASHSCOPE_PLUS_API_KEY" DASHSCOPE_MODEL=qwen-plus uv run taobao-agent '2010-12-01 交易明细金额是多少？'
```

当前命令先请求一次 Intent，再由 Query 的受控工具循环查指标、查规则、构建 SQL、查日期。预期 `next_expert=query`，`answer` 含 `58960.79 英镑` 和“不是已支付 GMV”。若模型未给出合法工具调用，程序会安全结束，不输出猜测金额。日期超出数据范围时也不会把“无数据”说成“0 销售额”。第 13 课文档记录当时的实现；当前四步链路见[第 20 课](docs/learning/lessons/20-Query完整取数工具链.md)。

你随后用 Plus Key 实际运行了这条命令，得到 `next_expert=query` 和 `58960.79 英镑`。这是该问法的一次真实调用验证，不能代表其他问法都能正确识别。

## 第 14 课：两个日期的金额变化

已有第 13 课的本地数据库且已在同一个终端准备 Plus Key 时，运行：

```bash
env -u TAOBAO_METRIC_CATALOG_PATH -u TAOBAO_DEMO_DATA TAOBAO_UCI_DB_PATH=data/uci_online_retail/online_retail.sqlite DASHSCOPE_API_KEY="$DASHSCOPE_PLUS_API_KEY" DASHSCOPE_MODEL=qwen-plus uv run taobao-agent '2010-12-02 交易明细金额为什么比 2010-12-01 下降？'
```

目的：检查模型是否把问题识别为 `attribution` 并提取两个日期，再由 Query 先查指标定义、查两天数据，把结构化证据交给 Analysis。若意图和工具调用正确，预期 `next_expert=analysis`，回答包含 `58960.79`、`47748.38`、`下降 11212.41 英镑` 和 `变化率 19.02%`，并说明总额不能解释原因。当前工具循环的真实百炼调用尚待验证；本地假模型与真实 SQLite 已验证数值路径。

当前仅支持同一指标的两个明确日期、无分组和无指定范围；基期金额为零时不会给出无意义的百分比。仍使用 Python 自带 SQLite，无需安装数据库服务或 Navicat。详见[第 14 课](docs/learning/lessons/14-两日金额变化与Analysis节点.md)。

你随后用 Plus Key 实测了上面的 UCI 两日问题：意图为 `attribution`，两个日期正确，回答包含 `下降 11212.41 英镑，变化率 19.02%`。这是第 14 课代码的一条真实在线样例。

## 第 15 课：Query 把取数结果交给 Analysis

第 14 课由 Analysis 直接调用查询工具。第 15 课改为 **Intent → Supervisor → Query → Analysis**：Query 把数值交给 Analysis。当前交接字段仍为 `State.query_evidence`，但内容已从固定两笔金额变为按日期排列的一组观测值。任一次取数失败，Query 不交出半份证据。`next_expert=analysis` 表示最终专家，Query 是它的取数前置步骤。第 15 课的[教学文档](docs/learning/lessons/15-Query到Analysis的结构化交接.md)记录当时的实现。

要验证当前图的真实模型调用，可再次运行本节上方的 UCI 两日命令。**目的**：确认 Query → Analysis 路径仍返回正确数值，并验证 Query 的指标查找与查询工具循环。**预期**：`next_expert=analysis`，`answer` 含 58960.79、47748.38、11212.41 英镑和 19.02%。主命令 JSON 不显示内部 `query_evidence`；本地测试检查这份交接。一次成功也不能证明所有问法可靠。

## 第 16 课：明确 Query 的受控取数工具

第 16 课时，Query 单日和两日路径共用 `query_daily_metric`：单日调用一次，两日调用两次。工具只接受指标名和日期；目录与数据库路径由程序绑定。第 19 课已将固定两次取数改为一组日期的任务，当前方法名为 `fetch_metric_series()`；历史课程仍记录当时的 `fetch_comparison()`。

第 16 课时工具由 Python 程序调用；现在 Query 可在四步受控工具循环中提出调用，由 Python 校验并执行。你可以再次运行上面的 UCI 两日命令检查当前路径；当前实现见[第 20 课教学文档](docs/learning/lessons/20-Query完整取数工具链.md)。无新依赖，也无需安装数据库服务或 Navicat。

## 第 17 课：移除教学运行模式

主命令只在配置 `TAOBAO_UCI_DB_PATH` 时查询当前批准的公开数据；没有数据库路径时仍可检查指标定义，但不返回数值。合成订单和指标文件保留在 `examples/`，只用于离线测试。`TAOBAO_DEMO_DATA=1` 已停用。Query、Analysis 和受控工具的 demo 分支已移除，UCI 的单日与两日调用链、SQL 门禁和只读限制仍在。细节见[第 17 课教学文档](docs/learning/lessons/17-移除教学运行模式.md)。

## 第 18 课：按证据选择路径，并接入 Query Tool Calling

**历史实现，已由第 19 课替换，可以跳过。**第 18 课曾让模型按固定日期重复提出日指标工具调用，并为两笔用户金额添加直达 Analysis 的特殊分支；这些代码现已调整。[旧教学文档](docs/learning/lessons/18-证据路由与Query工具调用.md)只用于回看当时的设计。

## 第 19 课：通用日期取数证据与 Query 工具循环

本课可直接接在第 17 课之后学习。`IntentResult` 保留 `curr/prev`，新增可列出多个日期的 `dates`；Query 先用 `search_metrics` 查指标定义，再用 `query_daily_metric` 取得每个批准日期的金额，按顺序形成 `State.query_evidence.values`。Analysis 对 2～7 个日期计算首末与相邻变化；单日或多日取数问题由 Query 直接回答。第 18 课的用户金额捷径已移除，不必先学它。当前仍只接 UCI 的一个批准指标，没有维度拆解或完整波动归因。详见[第 19 课教学文档](docs/learning/lessons/19-通用取数证据与工具循环.md)。

要用自己的 Plus Key 在线检查三日问法，可在已有 UCI SQLite 的同一个终端运行：

```bash
env -u TAOBAO_METRIC_CATALOG_PATH -u TAOBAO_DEMO_DATA TAOBAO_UCI_DB_PATH=data/uci_online_retail/online_retail.sqlite DASHSCOPE_API_KEY="$DASHSCOPE_PLUS_API_KEY" DASHSCOPE_MODEL=qwen-plus uv run taobao-agent '比较 2010-12-01、2010-12-02、2010-12-03 的交易明细金额变化'
```

目的：验证真实模型能识别三个日期，并完成“查指标 → 逐日取数 → Analysis”的工具循环。预期数值为 58960.79、47748.38、46943.71 英镑，首末下降 12017.08 英镑、20.38%。实际模型调用轮次取决于工具安排；本地假模型与真实 SQLite 已得到这些数值，但**本课尚未使用真实百炼 Key 验证**。若模型未识别出三个日期或没有调用批准工具，程序不会编造缺失金额。

## 单独检查 Intent

在已设置 Plus Key 的同一个终端运行：

```bash
DASHSCOPE_API_KEY="$DASHSCOPE_PLUS_API_KEY" DASHSCOPE_MODEL=qwen-plus uv run taobao-intent '昨天 GMV 比前天为什么下降？'
```

`taobao-intent` 只检查意图识别，方便定位错误。它与主命令使用同一个识别函数；它不是另一套正在运行的 Agent 图。这条命令不会查询 GMV 数值，也不能用一次成功证明模型总是准确。

命令开头选择 Plus Key 和 `qwen-plus`，`uv run taobao-intent` 启动意图识别程序，最后的引号里是待识别问题。结果包含主意图、次要意图、指标名称、时间和维度。`source=llm` 表示来自模型；如果模型触发 429，少量已支持的 GMV 问法会得到 `source=rule` 的规则结果，其他问法仍显示限流。规则结果的 `confidence=0.0` 只是“没有模型置信度”的标记；模型自报的置信度也不能当作真实准确率。当前没有语义模型，`metric_field` 必须为 `null`。

规则目前只识别“昨天 GMV 多少”“昨天 GMV 比前天为什么下降”“按行业看昨天的 GMV”这三类明确句式；不支持的句式不会猜答案。模型返回不合格式的 JSON 仍会报错，规则不会掩盖这种错误。

可在项目根目录运行六条固定日期的业务样例评测；这会实际发送 **6 次模型请求**，请确认自己的额度后再运行：

```bash
DASHSCOPE_API_KEY="$DASHSCOPE_PLUS_API_KEY" DASHSCOPE_MODEL=qwen-plus uv run python scripts/evaluate_intent.py
```

脚本按样例比较主意图和指定参数字段，输出每条样例的差异、结果来源与总匹配数。它不检查真实查询结果，也不代表完整 Agent 的成功率。

阶段计划、验证记录和教学文档见 `docs/learning/`。
