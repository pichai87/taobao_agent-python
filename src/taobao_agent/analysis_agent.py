"""The first Analysis capability: compare two verified daily totals."""

from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Sequence

from taobao_agent.demo_query_tool import DemoQueryError, query_demo_metric
from taobao_agent.harness import ToolUnavailableError, guarded_metric_search
from taobao_agent.intent_agent import IntentResult
from taobao_agent.metric_search import MetricDefinition
from taobao_agent.uci_online_retail import UCIDateOutOfRange, UCIQueryError, query_uci_metric


def _money(minor_units: int) -> str:
    """Display an integer number of cents/pence without floating-point rounding."""

    return f"{minor_units // 100}.{minor_units % 100:02d}"


def answer_analysis(
    intent: IntentResult,
    catalog: Sequence[MetricDefinition],
    demo_data: bool = False,
    uci_db_path: Path | None = None,
) -> str:
    """Compare two dates; never turn an observed change into a causal claim."""

    if not intent.metric_name:
        return "请提供要比较的指标名称；当前没有执行分析。"
    if intent.curr is None or intent.prev is None or intent.curr == intent.prev:
        return "请提供两个不同的明确日期；当前没有执行分析。"
    if intent.scope or intent.dimensions or intent.secondary_intents:
        return "当前只支持同一指标的两个日期总额比较，不支持指定范围、分组或复合任务；当前没有执行分析。"

    try:
        metric = guarded_metric_search(intent.metric_name, catalog)
    except ToolUnavailableError:
        return "指标目录暂时不可用；当前没有执行分析。"
    if metric is None:
        return f"尚未配置 {intent.metric_name} 的指标定义；当前没有执行分析。"

    if demo_data:
        if metric.name != "GMV" or metric.calculation is None or metric.calculation.unit != "fen":
            return "教学数据模式目前只支持已配置完整规则、单位为分的 GMV 比较。"
        try:
            previous = query_demo_metric(metric, intent.prev)
            current = query_demo_metric(metric, intent.curr)
        except DemoQueryError:
            return "教学数据比较查询未通过校验或执行失败；当前没有返回比较结果。"
        source = "教学合成数据"
        unit = "元"
        caveat = "这只说明样例 GMV 的金额变化，不能据此判断下降或增长的原因。"
    elif uci_db_path is not None:
        if metric.name != "交易明细金额" or metric.calculation is None or metric.calculation.unit != "pence":
            return "UCI 公开数据模式目前只支持交易明细金额比较。"
        try:
            previous = query_uci_metric(metric, intent.prev, uci_db_path)
            current = query_uci_metric(metric, intent.curr, uci_db_path)
        except UCIDateOutOfRange as exc:
            return f"UCI Online Retail 数据日期仅覆盖 {exc.first_date} 至 {exc.last_date}；当前没有返回比较结果。"
        except UCIQueryError:
            return "UCI 公开数据比较查询未通过校验或执行失败；当前没有返回比较结果。"
        source = "UCI Online Retail 公开数据"
        unit = "英镑"
        caveat = "这是排除取消、非正数量和非正单价后的交易明细金额，不是已支付 GMV；仅凭总额不能判断变化原因。"
    else:
        return "已找到指标定义，但当前没有启用可查询的数据源；无法比较两个日期的数值。"

    if previous < 0 or current < 0:
        return "查询结果不是可比较的非负金额；当前没有返回比较结果。"
    difference = current - previous
    direction = "下降" if difference < 0 else "上升" if difference > 0 else "持平"
    if previous == 0:
        change = "基期金额为 0，变化率无法计算"
    else:
        rate = (Decimal(abs(difference)) * 100 / Decimal(previous)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        change = f"变化率 {rate:.2f}%"
    return (
        f"{source}：{intent.prev.isoformat()} 的{metric.name}为 {_money(previous)} {unit}，"
        f"{intent.curr.isoformat()} 为 {_money(current)} {unit}；"
        f"{direction} {_money(abs(difference))} {unit}，{change}。{caveat}"
    )
