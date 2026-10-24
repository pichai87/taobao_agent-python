# 电商数据分析 Agent

基于《淘宝百亿补贴数据分析助手 Agent 实战》逐步实现的 Python + LangGraph 学习项目。

当前运行代码只有一张 LangGraph 图：**Intent 识别问题 → Supervisor 选择下一位专家 → 在可用时执行专家**。Other 能回复寒暄；Query 的第一步会调用本地指标查找函数。默认没有配置实际指标目录和数据源，所以 Query 会说明无法取数；Analysis 仍只输出交接目标。阶段 2 的单节点练习已移到[历史样例](docs/learning/archive/02_minimal_agent.py)，不参与当前程序运行。

## 环境与验证

```bash
uv sync
uv run python -m unittest discover -s tests -v
```

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

上面的 `taobao-agent` 命令只调用模型**一次**：先得到意图 JSON，再由 Supervisor 在程序内部按意图选专家。例子中的主意图应是 `attribution`，`next_expert` 应是 `analysis`，`answer` 应为 `null`，因为 Analysis 尚未接入。这不会真的分析 GMV 为什么下降。

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

Query 的指标查找现经由一个小型 Harness 错误边界：目录查找超时或连接失败时，`answer` 会提示“指标目录暂时不可用”，不会展示内部异常详情。默认目录为空；超时和断线行为目前由本地测试模拟。主图没有 SQL 执行路径；第 11 课的固定模板门禁尚未接入 Query。

## 指标目录文件

如果以后取得经过人工核对的指标定义，可将它们放在一个 JSON 文件中，并在运行命令的终端设置 `TAOBAO_METRIC_CATALOG_PATH` 为该文件路径。文件内容是列表，每项包含 `name`、`table`、`field`、`source`；可选的 `calculation` 记录当前支持的 `sum`、日期字段、一个等值条件和单位。当前项目**没有提供真实指标文件**；第 8 课有使用虚构表名的格式示例，第 10 课解释计算规则。

主命令会在调用模型前读取并检查文件。文件不存在、字段缺失或指标名重复时会退出并提示配置错误。加载成功仅表示格式符合要求，**不证明字段真实存在或业务口径正确**；当前主命令仍不会生成 SQL 或访问数据库。

## 第 9 课的教学数据

`examples/demo_shop.sql` 是手工编造的 6 笔订单，`examples/demo_metrics.json` 把 GMV 映射到样例表的金额字段，并记录 `sum`、`paid_date`、`payment_status=paid` 和单位“分”。它们不是淘宝真实数据，也不是公开数据集的摘录。2026-10-21 与 2026-10-22 的样例 GMV 分别为 300 元和 200 元。规则通过格式校验仍不足以证明真实表字段和业务口径正确；当前 Agent 不据此生成或执行 SQL。

在项目根目录运行 `UV_CACHE_DIR=/private/tmp/taobao-agent-uv-cache uv run --locked --offline python -m unittest discover -s tests -p 'test_demo_data.py' -v`，可以验证样例载入 SQLite 和固定日期的计算结果。这不调用模型，也不改变主命令；第 9 课解释了测试所用 SQL 和后续公开数据接入计划。

当前只使用 Python 自带的 `sqlite3` 在测试进程内建立数据库，**无需安装数据库服务或 Navicat**。以后如需外部数据库或图形管理工具，会先在对应课程文档说明用途、版本与安装方式；Navicat 属于可选的查看工具。

第 11 课新增 `metric_sql.py`：它根据样例指标规则与指定日期生成一种带 `?` 参数的只读 `SELECT SUM`，再由 `validate_metric_query()` 核对 SQL、参数及批准的表字段。测试用内存 SQLite 执行通过门禁的查询，得到 2026-10-22 的 200 元；主 Agent 仍未调用此模块。这个门禁只覆盖当前固定模板，尚不是任意 SQL 的通用校验器。第 11 课文档解释它的限制与调用链，无需安装新软件。

## 单独检查 Intent

在已设置 Plus Key 的同一个终端运行：

```bash
DASHSCOPE_API_KEY="$DASHSCOPE_PLUS_API_KEY" DASHSCOPE_MODEL=qwen-plus uv run taobao-intent '昨天 GMV 比前天为什么下降？'
```

`taobao-intent` 只检查意图识别，方便定位错误。它与主命令使用同一个识别函数；它不是另一套正在运行的 Agent 图。这条命令不会查询 GMV 数值，也不能用一次成功证明模型总是准确。

命令开头选择 Plus Key 和 `qwen-plus`，`uv run taobao-intent` 启动意图识别程序，最后的引号里是待识别问题。结果包含主意图、次要意图、指标名称、时间和维度。`source=llm` 表示来自模型；如果模型触发 429，少量已支持的 GMV 问法会得到 `source=rule` 的规则结果，其他问法仍显示限流。规则结果的 `confidence=0.0` 只是“没有模型置信度”的标记；模型自报的置信度也不能当作真实准确率。当前没有语义模型，`metric_field` 必须为 `null`。

规则目前只识别“昨天 GMV 多少”“昨天 GMV 比前天为什么下降”“按行业看昨天的 GMV”这三类明确句式；不支持的句式不会猜答案。模型返回不合格式的 JSON 仍会报错，规则不会掩盖这种错误。

可在项目根目录运行五条固定日期的业务样例评测；这会实际发送 **5 次模型请求**，请确认自己的额度后再运行：

```bash
DASHSCOPE_API_KEY="$DASHSCOPE_PLUS_API_KEY" DASHSCOPE_MODEL=qwen-plus uv run python scripts/evaluate_intent.py
```

脚本按样例比较主意图和指定参数字段，输出每条样例的差异、结果来源与总匹配数。它不检查真实查询结果，也不代表完整 Agent 的成功率。

阶段计划、验证记录和教学文档见 `docs/learning/`。
