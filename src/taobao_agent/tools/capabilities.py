"""Verified public capability list for the Other expert."""

from taobao_agent.commerce_data import CATALOG_PATH
from taobao_agent.metric_catalog import load_metric_catalog


def list_capabilities() -> str:
    names = "、".join(metric.name for metric in load_metric_catalog(CATALOG_PATH))
    return (
        f"当前可查询指标：{names}；支持明确日期、日期范围、按行业分组、"
        "数值比较、漏斗与行业贡献拆解、指标口径和字段血缘，"
        "以及本地数据质量任务的状态和日志。数据为教学合成数据。"
    )
