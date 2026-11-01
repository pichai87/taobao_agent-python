"""Small reviewed semantic model, business rules and SQL snippet store."""

from datetime import date
from pathlib import Path
from typing import Sequence

from pydantic import BaseModel, ConfigDict, Field, model_validator

from taobao_agent.metric_search import MetricDefinition
from taobao_agent.metric_sql import MetricQuery, build_metric_query


KNOWLEDGE_PATH = Path(__file__).resolve().parents[2] / "examples" / "commerce_knowledge.json"


class SemanticModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str = Field(min_length=1)
    table: str = Field(min_length=1)
    source: str = Field(min_length=1)
    columns: dict[str, str]
    dimensions: dict[str, str]
    upstream: dict[str, str] = Field(default_factory=dict)


class BusinessRule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(min_length=1)
    metric_names: tuple[str, ...] = Field(min_length=1)
    condition_field: str
    condition_value: str
    description: str


class SqlSnippet(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(min_length=1)
    group_by: str | None
    description: str
    template: str


class KnowledgeBase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    model: SemanticModel
    rules: tuple[BusinessRule, ...] = Field(min_length=1)
    snippets: tuple[SqlSnippet, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def check_unique_ids(self) -> "KnowledgeBase":
        if len({rule.id for rule in self.rules}) != len(self.rules):
            raise ValueError("知识库规则编号重复")
        if len({item.id for item in self.snippets}) != len(self.snippets):
            raise ValueError("SQL 片段编号重复")
        if len({item.group_by for item in self.snippets}) != len(self.snippets):
            raise ValueError("同一分组出现多个 SQL 片段")
        return self

    def validate_catalog(self, catalog: Sequence[MetricDefinition]) -> None:
        """Reject semantic knowledge that disagrees with the approved metric registry."""

        names = {metric.name for metric in catalog}
        if not names or {name for rule in self.rules for name in rule.metric_names} != names:
            raise ValueError("知识规则与指标注册表不一致")
        if set(self.model.dimensions.values()) - self.model.columns.keys():
            raise ValueError("MDL 维度字段不在表结构中")
        if set(self.model.upstream) != self.model.columns.keys():
            raise ValueError("MDL 缺少日报字段的上游映射")
        for metric in catalog:
            calculation = metric.calculation
            if calculation is None or metric.table != self.model.table or metric.source != self.model.source:
                raise ValueError("MDL 来源与指标注册表不一致")
            if {metric.field, calculation.date_field, calculation.condition.field} - self.model.columns.keys():
                raise ValueError("指标字段不在 MDL 表结构中")
            matching = [rule for rule in self.rules if metric.name in rule.metric_names]
            if len(matching) != 1 or (
                matching[0].condition_field != calculation.condition.field
                or matching[0].condition_value != calculation.condition.value
            ):
                raise ValueError("业务规则与指标计算口径不一致")
        if {item.group_by for item in self.snippets} != {None, *self.model.dimensions.keys()}:
            raise ValueError("SQL 片段未覆盖批准的查询形状")
        sample = date(2026, 10, 22)
        for snippet in self.snippets:
            for metric in catalog:
                self.render_snippet(snippet, metric, sample, sample)

    def rule_for(self, name: str) -> BusinessRule:
        return next(rule for rule in self.rules if name in rule.metric_names)

    def snippet_for(self, group_by: str | None) -> SqlSnippet:
        return next(item for item in self.snippets if item.group_by == group_by)

    def render_snippet(
        self, snippet: SqlSnippet, metric: MetricDefinition, start: date, end: date,
    ) -> MetricQuery:
        """Render reviewed text, then compare it with the non-editable approved SQL shape."""

        canonical = build_metric_query(metric, start, end, snippet.group_by)
        calculation = metric.calculation
        try:
            statement = snippet.template.format(
                metric_field=metric.field, table=metric.table,
                date_field=calculation.date_field,
                condition_field=calculation.condition.field,
                group_field=self.model.dimensions.get(snippet.group_by, ""),
            )
        except (KeyError, ValueError) as exc:
            raise ValueError("SQL 片段占位符无效") from exc
        rendered = MetricQuery(statement, canonical.parameters)
        if rendered != canonical:
            raise ValueError("SQL 片段与批准的 SQL 模板不一致")
        return rendered


def load_knowledge(
    catalog: Sequence[MetricDefinition], path: Path = KNOWLEDGE_PATH,
) -> KnowledgeBase:
    try:
        knowledge = KnowledgeBase.model_validate_json(path.read_text(encoding="utf-8"))
        knowledge.validate_catalog(catalog)
    except (OSError, UnicodeError) as exc:
        raise ValueError("无法读取语义知识文件") from exc
    return knowledge
