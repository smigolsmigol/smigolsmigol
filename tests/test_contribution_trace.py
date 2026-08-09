from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path
from xml.etree import ElementTree


MODULE_PATH = Path(__file__).parents[1] / "tools" / "contribution_trace.py"
SPEC = importlib.util.spec_from_file_location("contribution_trace", MODULE_PATH)
assert SPEC and SPEC.loader
trace = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = trace
SPEC.loader.exec_module(trace)


def fixture_payload(*, corrupt_total: bool = False) -> dict:
    start = date(2025, 8, 10)
    weeks: list[dict] = []
    total = 0
    for week_index in range(53):
        days: list[dict] = []
        for weekday in range(7):
            offset = week_index * 7 + weekday
            if offset >= 366:
                break
            current = start + timedelta(days=offset)
            count = 0 if offset % 5 else (offset % 23) + 1
            total += count
            if count == 0:
                level = "NONE"
            elif count < 6:
                level = "FIRST_QUARTILE"
            elif count < 12:
                level = "SECOND_QUARTILE"
            elif count < 18:
                level = "THIRD_QUARTILE"
            else:
                level = "FOURTH_QUARTILE"
            days.append(
                {
                    "date": current.isoformat(),
                    "contributionCount": count,
                    "contributionLevel": level,
                    "weekday": weekday,
                }
            )
        weeks.append({"firstDay": days[0]["date"], "contributionDays": days})
    return {
        "data": {
            "user": {
                "contributionsCollection": {
                    "contributionCalendar": {
                        "totalContributions": total + int(corrupt_total),
                        "weeks": weeks,
                    }
                }
            }
        }
    }


class ContributionTraceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.calendar = trace.parse_calendar(fixture_payload(), "smigolsmigol")

    def test_complete_calendar_is_parsed(self) -> None:
        self.assertEqual(366, len(self.calendar.days))
        self.assertEqual(53, self.calendar.week_count)
        self.assertEqual(
            sum(day.count for day in self.calendar.days), self.calendar.total
        )
        self.assertGreater(self.calendar.active_days, 0)

    def test_declared_total_mismatch_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "Contribution total mismatch"):
            trace.parse_calendar(fixture_payload(corrupt_total=True), "smigolsmigol")

    def test_duplicate_date_fails_closed(self) -> None:
        payload = fixture_payload()
        weeks = payload["data"]["user"]["contributionsCollection"][
            "contributionCalendar"
        ]["weeks"]
        weeks[1]["contributionDays"][0]["date"] = weeks[0]["contributionDays"][0][
            "date"
        ]
        with self.assertRaisesRegex(ValueError, "duplicate dates"):
            trace.parse_calendar(payload, "smigolsmigol")

    def test_one_checkpoint_is_selected_per_active_week(self) -> None:
        checkpoints = trace.trace_checkpoints(self.calendar)
        self.assertEqual(len({item.week for item in checkpoints}), len(checkpoints))
        self.assertTrue(all(item.count > 0 for item in checkpoints))

    def test_render_is_deterministic_and_valid_xml(self) -> None:
        first = trace.render_svg(
            self.calendar, theme_name="dark", generated_on=date(2026, 8, 10)
        )
        second = trace.render_svg(
            self.calendar, theme_name="dark", generated_on=date(2026, 8, 10)
        )
        self.assertEqual(first, second)
        ElementTree.fromstring(first)

    def test_render_carries_source_and_motion_boundaries(self) -> None:
        svg = trace.render_svg(
            self.calendar, theme_name="light", generated_on=date(2026, 8, 10)
        )
        self.assertIn("SOURCE GITHUB GRAPHQL", svg)
        self.assertIn("prefers-reduced-motion: reduce", svg)
        self.assertIn("NO THIRD-PARTY RENDERER", svg)
        self.assertIn(self.calendar.digest[:10].upper(), svg)
        self.assertNotIn("GITHUB_TOKEN", svg)
        self.assertNotIn("<script", svg)
        self.assertEqual(1, svg.count("http://www.w3.org/2000/svg"))
        self.assertNotIn('href="http', svg)
        self.assertNotIn("https://", svg)

    def test_graphql_errors_fail_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "GraphQL returned errors"):
            trace.parse_calendar({"errors": [{"message": "denied"}]}, "smigolsmigol")

    def test_cli_summary_is_json_serializable(self) -> None:
        summary = {
            "active_days": self.calendar.active_days,
            "digest": self.calendar.digest,
            "peak": self.calendar.peak,
            "total": self.calendar.total,
        }
        self.assertEqual(summary, json.loads(json.dumps(summary, sort_keys=True)))


if __name__ == "__main__":
    unittest.main()
