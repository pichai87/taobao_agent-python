# 电商数据分析 Agent

这是依据用户提供的《淘宝百亿补贴数据分析助手 Agent 实战》PDF，使用 Python、uv 和 LangGraph 渐进实现的学习项目。**当前只有一个运行数据源和一条取数路径**：

```text
用户问题 → Intent（一次模型调用提取参数）→ Supervisor（选择任务）
         → Query（查指标、查规则、构建 SQL、校验、只读取数）
         → 按需 Analysis（只读取 Query 证据，计算变化与漏斗拆解）
```

当前业务数据是明确标注的**合成样例**，不代表淘宝实际经营数据。PDF 的 ODPS、WrenAI、TisPlus3、LightRAG、Ops、Web 和完整 Harness 仍是后续功能；SQLite 与本地指标目录是当前可验证的替代实现。阶段教学见 [学习路线](docs/learning/学习路线.md) 和 [第 26 课：单一数据源与统一取数链](docs/learning/lessons/26-单一数据源与统一取数链.md)。

## 准备环境和数据

项目使用配置的 Python 3.13；安装 uv 后，在项目根目录运行：

```bash
uv sync --locked
uv run taobao-seed-commerce
```

第二条命令把 [10 行可核算样例](examples/commerce_daily.csv) 导入 `data/commerce/commerce.sqlite`。目标文件已存在时会拒绝覆盖；不需要安装数据库服务或 Navicat。数据库位于被 Git 忽略的 `data/`，仓库保存的是样例和建库程序。若本机已经建好数据库，只需运行一次 `uv sync --locked`。

样例包含 2026-10-20 至 2026-10-22 的百亿补贴 GMV、访客数、订单数及行业。另有一行“普通活动”用于验证口径过滤；**百亿补贴 GMV 不包含它**。指标定义在 [commerce_metrics.json](examples/commerce_metrics.json)。

## 设置模型 Key 并提问

在自己的终端设置 `DASHSCOPE_API_KEY`，不要把 Key 写入仓库或对话。若此前已经设置 `DASHSCOPE_PLUS_API_KEY`，可以逐条用命令前缀传给本次进程：

```bash
DASHSCOPE_API_KEY="$DASHSCOPE_PLUS_API_KEY" DASHSCOPE_MODEL=qwen-plus uv run taobao-agent '2026-10-22 百亿补贴 GMV 多少？'
DASHSCOPE_API_KEY="$DASHSCOPE_PLUS_API_KEY" DASHSCOPE_MODEL=qwen-plus uv run taobao-agent '2026-10-22 百亿补贴 GMV 比 2026-10-21 为什么下降？按行业拆解。'
```

第一问应返回合成 GMV `20600.00 元`。第二问应给出从 `24000.00 元` 到 `20600.00 元` 的变化，并用访客数、转化率、客单价及行业证据拆解。**数值贡献不是已证实的经营因果原因**。模型是否正确识别和调用工具，要通过在线评测观察，不能仅凭本地计算推断。

若旧终端仍设置 `TAOBAO_UCI_DB_PATH` 或 `TAOBAO_DEMO_DATA`，先运行 `unset TAOBAO_UCI_DB_PATH TAOBAO_DEMO_DATA`；旧数据源开关已退出主流程。可以用 `TAOBAO_DB_PATH` 指向另一份由同一建库程序生成的数据库，但项目运行时只连接其中**一份**。历史 UCI 数据文件保留在本机，主代码不再依赖它们。

## 验证

```bash
uv run python -m unittest discover -s tests -p 'test_*.py' -q
DASHSCOPE_API_KEY="$DASHSCOPE_PLUS_API_KEY" DASHSCOPE_MODEL=qwen-plus uv run taobao-eval --diagnostic
```

第一条是离线测试，核对合成数据、统一调用链、SQL 校验、错误行为和 Golden Dataset 的预期；它不调用真实模型。第二条逐题调用真实模型，报告意图、路由和答案检查，会产生模型请求和费用。两者的通过数应以实际输出为准。需要只跑一题时用 `--case <题目 ID>`；题目见 [default.json](scripts/eval/datasets/default.json)。

第 26 课记录了真实模型首轮 5/7、定位与修正工具参数格式错误、定向复测 2/2 和完整回归 7/7 的过程。固定七题的一次通过不代表任意问法可靠。

## 已知边界

- 现有指标为 GMV、访客数、订单数；查询支持最多 31 天的日期范围、最多 7 个明确日期和按行业分组。
- Analysis 可计算 GMV 的访客数 × 转化率 × 客单价恒等式与行业数值贡献；没有实验、营销或库存证据时不声称查明原因。
- 当前工具链使用受控 SQL 模板和后置校验；没有让模型执行任意 SQL。
- 旧 UCI 路径及其评测是前 13～25 课的历史实现，可从 Git 历史和对应教学文档查看，不参与当前运行。
