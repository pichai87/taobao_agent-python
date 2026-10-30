"""Check that Analysis model choices cannot change the verified numbers."""

import unittest

from langchain_core.messages import AIMessage

from taobao_agent.analysis_agent import (
    MetricSeriesEvidence, answer_analysis, answer_analysis_question,
)
from taobao_agent.daily_metric_tool import DailyMetricValue


class FixedPlanModel:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls: list[list[object]] = []

    def invoke(self, messages: list[object]) -> AIMessage:
        self.calls.append(messages)
        return AIMessage(content=self.response)


def evidence() -> MetricSeriesEvidence:
    return MetricSeriesEvidence(metric_name="交易明细金额", values=(
        DailyMetricValue(metric_name="交易明细金额", business_date="2010-12-01", amount_minor=10000, source="uci", unit="pence"),
        DailyMetricValue(metric_name="交易明细金额", business_date="2010-12-02", amount_minor=8000, source="uci", unit="pence"),
        DailyMetricValue(metric_name="交易明细金额", business_date="2010-12-03", amount_minor=9000, source="uci", unit="pence"),
    ))


class AnalysisPlanningTest(unittest.TestCase):
    def test_model_selects_largest_drop_but_program_calculates_it(self) -> None:
        model = FixedPlanModel('{"mode":"largest_daily_drop"}')
        answer = answer_analysis_question("哪一天跌得最多？", evidence(), model)

        self.assertIn("2010-12-01→2010-12-02，下降 20.00 英镑", answer)
        self.assertIn("不能判断变化原因", answer)
        self.assertEqual(len(model.calls), 1)

    def test_high_low_uses_verified_values(self) -> None:
        answer = answer_analysis_question(
            "哪天最高、哪天最低？", evidence(), FixedPlanModel('{"mode":"high_low"}'),
        )
        self.assertIn("最高为 2010-12-01 的 100.00 英镑", answer)
        self.assertIn("最低为 2010-12-02 的 80.00 英镑", answer)

    def test_invalid_plan_falls_back_to_verified_comparison(self) -> None:
        for raw in ('{"mode":"invent_cause"}', '{"mode":"high_low","cause":"广告停投"}', 'not json'):
            with self.subTest(raw=raw):
                answer = answer_analysis_question("为什么下降？", evidence(), FixedPlanModel(raw))
                self.assertEqual(answer, answer_analysis(evidence()))


if __name__ == "__main__":
    unittest.main()
