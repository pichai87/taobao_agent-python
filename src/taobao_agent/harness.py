"""The first runtime guard around a business tool call."""

from typing import Sequence

from taobao_agent.metric_search import MetricDefinition, search_metrics


class ToolUnavailableError(Exception):
    """A tool timed out or lost its connection."""


def guarded_metric_search(
    name: str, catalog: Sequence[MetricDefinition]
) -> MetricDefinition | None:
    """Keep operational lookup failures separate from a missing metric."""

    try:
        return search_metrics(name, catalog)
    except (TimeoutError, ConnectionError) as exc:
        raise ToolUnavailableError("metric search unavailable") from exc
