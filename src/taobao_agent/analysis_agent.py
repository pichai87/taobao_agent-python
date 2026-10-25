"""The first Analysis capability: compare two verified daily totals."""

from decimal import Decimal, ROUND_HALF_UP
from taobao_agent.query_agent import QueryEvidence


def _money(minor_units: int) -> str:
    """Display an integer number of cents/pence without floating-point rounding."""

    return f"{minor_units // 100}.{minor_units % 100:02d}"


def answer_analysis(evidence: QueryEvidence) -> str:
    """Explain a verified comparison; never claim a cause from two totals."""

    if evidence.source == "demo":
        source = "教学合成数据"
        unit = "元"
        caveat = "这只说明样例 GMV 的金额变化，不能据此判断下降或增长的原因。"
    else:
        source = "UCI Online Retail 公开数据"
        unit = "英镑"
        caveat = "这是排除取消、非正数量和非正单价后的交易明细金额，不是已支付 GMV；仅凭总额不能判断变化原因。"

    previous = evidence.previous_minor
    current = evidence.current_minor
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
        f"{source}：{evidence.prev.isoformat()} 的{evidence.metric_name}为 {_money(previous)} {unit}，"
        f"{evidence.curr.isoformat()} 为 {_money(current)} {unit}；"
        f"{direction} {_money(abs(difference))} {unit}，{change}。{caveat}"
    )
