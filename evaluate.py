"""Evaluate the deterministic triage baseline against synthetic labeled cases."""

from __future__ import annotations

import json
from pathlib import Path

from main import RuleBasedAnalyzer


CASES_PATH = Path("data/evaluation_cases.jsonl")
OUTPUT_PATH = Path("reports/evaluation-report.json")


def main() -> None:
    analyzer = RuleBasedAnalyzer()
    cases = [json.loads(line) for line in CASES_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
    checks = {"category": 0, "priority": 0, "human_review": 0}
    failures: list[dict[str, str]] = []
    for case in cases:
        actual = analyzer.analyze(case["subject"], case["content"])
        comparisons = {
            "category": actual.category == case["category"],
            "priority": actual.priority == case["priority"],
            "human_review": actual.requires_human_review == case["requires_human_review"],
        }
        for name, passed in comparisons.items():
            checks[name] += int(passed)
        if not all(comparisons.values()):
            failures.append({"subject": case["subject"], "actual": json.dumps({"category": actual.category, "priority": actual.priority, "requires_human_review": actual.requires_human_review}, ensure_ascii=False), "expected": json.dumps({key: case[key] for key in ("category", "priority", "requires_human_review")}, ensure_ascii=False)})
    report = {
        "dataset": str(CASES_PATH),
        "case_count": len(cases),
        "metrics": {name: round(value / len(cases), 4) for name, value in checks.items()},
        "failures": failures,
        "scope": "synthetic deterministic baseline; not an LLM quality claim",
    }
    OUTPUT_PATH.parent.mkdir(exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
