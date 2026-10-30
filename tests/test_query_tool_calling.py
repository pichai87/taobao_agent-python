"""Check the bounded Query tool loop against valid and invalid model actions."""

import sqlite3
import unittest
from contextlib import closing
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
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


def rule(**arguments: object) -> dict[str, object]:
    return {"name": "rule_search", "args": {"metric_name": "交易明细金额", **arguments}, "id": "r1"}


def sql(**arguments: object) -> dict[str, object]:
    return {
        "name": "build_sql",
        "args": {"metric_name": "交易明细金额", "dates": ["2010-12-01"], **arguments},
        "id": "b1",
    }


class QueryToolCallingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = load_metric_catalog(UCI_CATALOG_PATH)
        self.database_path = Path("unused.sqlite")

    def test_full_query_chain_executes_approved_sql(self) -> None:
        model = ScriptedModel([[search()], [rule()], [sql()], [daily()]])
        with patch("taobao_agent.daily_metric_tool.query_uci_metric", return_value=635) as query:
            answer = answer_query(
                intent(), self.catalog, self.database_path, model,
                "2010-12-01 交易明细金额是多少？",
            )

        self.assertEqual(model.bound_tool_names, [
            "search_metrics", "rule_search", "build_sql", "query_daily_metric",
        ])
        self.assertEqual(len(model.messages), 4)
        self.assertTrue(any(isinstance(item, ToolMessage) for item in model.messages[1]))
        args, kwargs = query.call_args
        self.assertEqual(args, (self.catalog[0], date(2010, 12, 1), self.database_path))
        self.assertEqual(kwargs["candidate"].parameters, ("2010-12-01", "positive"))
        self.assertIn("6.35 英镑", answer)

    def test_invalid_or_unapproved_actions_never_query(self) -> None:
        scripts = [
            [[]],
            [[daily()]],
            [[{**search(), "name": "unsafe_sql"}]],
            [[search()], [daily()]],
            [[search()], [sql()]],
            [[search()], [rule()], [daily()]],
            [[search()], [rule()], [sql(dates=["2010-12-02"])]],
            [[search()], [rule()], [sql(statement="SELECT * FROM retail_lines")]],
            [[search()], [rule()], [sql()], [daily(business_date="2010-12-02")]],
            [[search()], [rule()], [sql()], [daily(database_path="other.sqlite")]],
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

    def test_model_chain_reaches_read_only_sqlite_with_built_sql(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "retail.sqlite"
            with closing(sqlite3.connect(path)) as connection:
                connection.executescript(
                    "CREATE TABLE retail_lines (invoice_date TEXT, record_type TEXT, line_amount_pence INTEGER);"
                    "CREATE TABLE import_metadata (source_id TEXT, source_sha256 TEXT, first_date TEXT, last_date TEXT);"
                    "INSERT INTO import_metadata VALUES ('uci-online-retail-352', 'fixture', '2010-12-01', '2010-12-01');"
                    "INSERT INTO retail_lines VALUES ('2010-12-01', 'positive', 635);"
                    "INSERT INTO retail_lines VALUES ('2010-12-01', 'cancel', 999);"
                )
            answer = answer_query(
                intent(), self.catalog, path,
                ScriptedModel([[search()], [rule()], [sql()], [daily()]]),
                "2010-12-01 交易明细金额是多少？",
            )
        self.assertIn("6.35 英镑", answer)


if __name__ == "__main__":
    unittest.main()
