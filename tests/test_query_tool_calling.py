"""Check the bounded Query tool loop against valid and invalid model actions."""

import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from langchain_core.messages import AIMessage, ToolMessage

from taobao_agent.intent_agent import IntentResult
from taobao_agent.metric_catalog import load_metric_catalog
from taobao_agent.query_agent import answer_query
from taobao_agent.uci_online_retail import UCI_CATALOG_PATH


class ScriptedModel:
    def __init__(self, responses: list[list[dict[str, object]]]) -> None:
        self.responses = list(responses)
        self.bound_tool_names: list[str] = []
        self.messages: list[list[object]] = []

    def bind_tools(self, tools: list[object], *, tool_choice: str = "auto") -> "ScriptedModel":
        self.bound_tool_names = [item.name for item in tools]
        return self

    def invoke(self, messages: list[object]) -> AIMessage:
        self.messages.append(list(messages))
        return AIMessage(content="", tool_calls=self.responses.pop(0))


def intent() -> IntentResult:
    return IntentResult(
        intent_type="data_query", secondary_intents=[], confidence=0.9,
        metric_name="交易明细金额", metric_field=None, time_label="2010-12-01",
        curr="2010-12-01", prev=None, scope=None, dimensions=[],
    )


def search(**arguments: object) -> dict[str, object]:
    return {"name": "search_metrics", "args": {"metric_name": "交易明细金额", **arguments}, "id": "s1"}


def daily(**arguments: object) -> dict[str, object]:
    return {
        "name": "query_daily_metric",
        "args": {"metric_name": "交易明细金额", "business_date": "2010-12-01", **arguments},
        "id": "d1",
    }


class QueryToolCallingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = load_metric_catalog(UCI_CATALOG_PATH)
        self.database_path = Path("unused.sqlite")

    def test_search_then_daily_query_executes_approved_tool(self) -> None:
        model = ScriptedModel([[search()], [daily()]])
        with patch("taobao_agent.daily_metric_tool.query_uci_metric", return_value=635) as query:
            answer = answer_query(
                intent(), self.catalog, self.database_path, model,
                "2010-12-01 交易明细金额是多少？",
            )

        self.assertEqual(model.bound_tool_names, ["search_metrics", "query_daily_metric"])
        self.assertEqual(len(model.messages), 2)
        self.assertTrue(any(isinstance(item, ToolMessage) for item in model.messages[1]))
        query.assert_called_once_with(self.catalog[0], date(2010, 12, 1), self.database_path)
        self.assertIn("6.35 英镑", answer)

    def test_invalid_or_unapproved_actions_never_query(self) -> None:
        scripts = [
            [[]],
            [[daily()]],
            [[{**search(), "name": "unsafe_sql"}]],
            [[search()], [daily(business_date="2010-12-02")]],
            [[search()], [daily(database_path="other.sqlite")]],
            [[search(metric_name="GMV")]],
        ]
        with patch("taobao_agent.daily_metric_tool.query_uci_metric") as query:
            for responses in scripts:
                with self.subTest(responses=responses):
                    answer = answer_query(
                        intent(), self.catalog, self.database_path,
                        ScriptedModel(responses), "2010-12-01 交易明细金额是多少？",
                    )
                    self.assertIn("查询未通过校验", answer)
        query.assert_not_called()


if __name__ == "__main__":
    unittest.main()
