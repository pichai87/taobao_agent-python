"""Check that model tool proposals cannot bypass the Query execution boundary."""

import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from langchain_core.messages import AIMessage

from taobao_agent.intent_agent import IntentResult
from taobao_agent.metric_catalog import load_metric_catalog
from taobao_agent.query_agent import answer_query
from taobao_agent.uci_online_retail import UCI_CATALOG_PATH


class ProposalModel:
    def __init__(self, proposals: list[dict[str, object]]) -> None:
        self.proposals = proposals
        self.bound_tool_names: list[str] = []

    def bind_tools(self, tools: list[object], *, tool_choice: str = "auto") -> "ProposalModel":
        self.bound_tool_names = [tool.name for tool in tools]
        return self

    def invoke(self, messages: list[object]) -> AIMessage:
        return AIMessage(content="", tool_calls=self.proposals)


def intent() -> IntentResult:
    return IntentResult(
        intent_type="data_query", secondary_intents=[], confidence=0.9,
        metric_name="交易明细金额", metric_field=None, time_label="2010-12-01",
        curr="2010-12-01", prev=None, scope=None, dimensions=[],
    )


def proposal(**arguments: object) -> dict[str, object]:
    return {
        "name": "query_daily_metric",
        "args": {"metric_name": "交易明细金额", "business_date": "2010-12-01", **arguments},
        "id": "call-1",
    }


class QueryToolCallingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = load_metric_catalog(UCI_CATALOG_PATH)
        self.database_path = Path("unused.sqlite")

    def test_matching_proposal_executes_approved_tool_once(self) -> None:
        model = ProposalModel([proposal()])
        with patch("taobao_agent.daily_metric_tool.query_uci_metric", return_value=635) as query:
            answer = answer_query(
                intent(), self.catalog, self.database_path, model,
                "2010-12-01 交易明细金额是多少？",
            )

        self.assertEqual(model.bound_tool_names, ["query_daily_metric"])
        query.assert_called_once_with(self.catalog[0], date(2010, 12, 1), self.database_path)
        self.assertIn("6.35 英镑", answer)

    def test_missing_wrong_or_extra_tool_arguments_do_not_query(self) -> None:
        proposals = [
            [],
            [{**proposal(), "name": "unsafe_sql"}],
            [proposal(business_date="2010-12-02")],
            [proposal(database_path="other.sqlite")],
            [proposal(), proposal()],
        ]
        with patch("taobao_agent.daily_metric_tool.query_uci_metric") as query:
            for calls in proposals:
                with self.subTest(calls=calls):
                    answer = answer_query(
                        intent(), self.catalog, self.database_path,
                        ProposalModel(calls), "2010-12-01 交易明细金额是多少？",
                    )
                    self.assertIn("查询未通过校验", answer)
        query.assert_not_called()


if __name__ == "__main__":
    unittest.main()
