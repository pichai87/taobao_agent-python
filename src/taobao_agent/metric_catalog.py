"""Load the small metric registry used by the current Query step."""

from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from taobao_agent.metric_search import MetricDefinition


def load_metric_catalog(path: Path) -> tuple[MetricDefinition, ...]:
    """Read a JSON list and reject invalid or ambiguous metric definitions."""

    try:
        contents = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"无法读取指标目录文件：{path}") from exc

    try:
        metrics = TypeAdapter(list[MetricDefinition]).validate_json(contents)
    except ValidationError as exc:
        raise ValueError(
            "指标目录格式无效：需要指标对象列表，且每项包含 name、table、field、source"
        ) from exc

    names: set[str] = set()
    for metric in metrics:
        normalized = metric.name.casefold()
        if normalized in names:
            raise ValueError(f"指标目录包含重复名称：{metric.name}")
        names.add(normalized)
    return tuple(metrics)
