"""Run the small business intent dataset against a real configured model."""

import json
from datetime import date
from pathlib import Path

from taobao_agent.intent_agent import create_qwen_model, run_intent_once


CASES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "intent_golden.json"


def main() -> None:
    cases = json.loads(CASES.read_text(encoding="utf-8"))
    model = create_qwen_model()
    passed_cases = 0
    passed_fields = 0
    total_fields = 0
    rule_results = 0

    for case in cases:
        result = run_intent_once(
            case["question"], model, date.fromisoformat(case["reference_date"])
        ).model_dump(mode="json")
        rule_results += result["source"] == "rule"
        mismatches = {
            field: {"expected": expected, "actual": result[field]}
            for field, expected in case["expected"].items()
            if result[field] != expected
        }
        field_count = len(case["expected"])
        total_fields += field_count
        passed_fields += field_count - len(mismatches)
        passed_cases += not mismatches
        print(
            json.dumps(
                {"id": case["id"], "source": result["source"], "mismatches": mismatches},
                ensure_ascii=False,
            )
        )

    print(f"完全匹配样例: {passed_cases}/{len(cases)}；字段匹配: {passed_fields}/{total_fields}")
    print(f"其中规则降级结果: {rule_results}/{len(cases)}")


if __name__ == "__main__":
    main()
