"""The first local business tool: look up an explicitly supplied metric definition."""

from typing import Annotated, Sequence

from pydantic import BaseModel, ConfigDict, StringConstraints


NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class MetricDefinition(BaseModel):
    """One explicitly supplied metric; this is not a complete MDL model."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: NonEmptyText
    table: NonEmptyText
    field: NonEmptyText
    source: NonEmptyText


def search_metrics(name: str, catalog: Sequence[MetricDefinition]) -> MetricDefinition | None:
    """Find a metric by its exact name; do not guess a table or field."""

    normalized = name.strip().casefold()
    for metric in catalog:
        if metric.name.casefold() == normalized:
            return metric
    return None
