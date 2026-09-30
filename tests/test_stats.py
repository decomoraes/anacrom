"""The stats: runs saved and read back, numbers worked out, the page safe to open.

    python3 -m unittest tests.test_stats -v
"""
from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from anacrom.stats import build_stats, read_jsonl, record_run, render_text   # noqa: E402
from anacrom.stats_page import render_page                                    # noqa: E402


def decision(action, source="jev", confidence=0.8, reason="", seconds=0.15, dry_run=False):
    return {"at": 1.0, "state": {}, "options": [action], "dry_run": dry_run,
            "decision": {"action": action, "source": source, "confidence": confidence,
                         "reason": reason, "target": 0},
            "seconds": seconds, "input_tokens": 500}


class Stats(unittest.TestCase):
    def setUp(self):
        self._folder = tempfile.TemporaryDirectory()
        self.addCleanup(self._folder.cleanup)
        self.folder = pathlib.Path(self._folder.name)

    def write(self, name, rows):
        (self.folder / name).write_text("".join(json.dumps(r) + "\n" for r in rows))

    def test_an_empty_folder_gives_zeroes_not_errors(self):
        stats = build_stats(self.folder)
        self.assertEqual(stats["totals"]["runs"], 0)
        self.assertEqual(stats["totals"]["gold_per_hour"], 0)
        self.assertIsNone(stats["totals"]["cost_per_1k_gold"])
        self.assertIn("0 runs", render_text(stats))

    def test_runs_round_trip_and_add_up(self):
        for gold, kills in (([100, 250], 4), ([250, 240], 1)):
            record_run(self.folder / "runs.jsonl", {
                "stopped": "time limit", "seconds": 1800.0, "ticks": 900, "actions": {"fight": 5},
                "jev_calls": 100, "input_tokens": 60000, "cost_usd": 0.0025, "gold": gold,
                "kills": kills, "kills_by_name": {"a zombie": kills}, "items_taken": 2,
                "output": "a long transcript that should not be kept"}, 1000.0, "Jevensen")
        self.assertNotIn("output", read_jsonl(self.folder / "runs.jsonl")[0])
        t = build_stats(self.folder)["totals"]
        self.assertEqual((t["runs"], t["calls"], t["tokens"], t["kills"]), (2, 200, 120000, 5))
        self.assertEqual(t["gold"], 140)                   # +150 then -10
        self.assertEqual(t["hours"], 1.0)
        self.assertEqual(t["gold_per_hour"], 140)
        self.assertEqual(t["tokens_per_call"], 600)
        self.assertEqual(build_stats(self.folder)["kills_by_name"], {"a zombie": 5})

    def test_old_runs_without_kills_are_unknown_not_zero(self):
        self.write("runs.jsonl", [{"started_at": 1, "stopped": "we died (right after)", "seconds": 60,
                                   "jev_calls": 3, "input_tokens": 10, "cost_usd": 0.0, "gold": [5, 5]}])
        stats = build_stats(self.folder)
        self.assertIsNone(stats["runs"][0]["kills"])
        self.assertEqual(stats["totals"]["deaths"], 1)

    def test_decisions_dry_runs_and_fallbacks(self):
        self.write("jev.jsonl", [
            decision("fight", confidence=0.95), decision("fight", confidence=0.62),
            decision("roam", confidence=0.05),
            decision("wait", source="fallback", confidence=0.3, reason="unsure (roam 0.12 < 0.30)"),
            decision("flee", source="rule", confidence=None),
            decision("fight", dry_run=True)])
        d = build_stats(self.folder)["decisions"]
        self.assertEqual(d["count"], 5)                    # the dry run is not counted
        self.assertEqual(d["actions"], {"fight": 2, "roam": 1, "wait": 1, "flee": 1})
        self.assertEqual(d["sources"], {"jev": 3, "fallback": 1, "rule": 1})
        self.assertEqual(d["fallbacks"], {"roam": 1})
        self.assertEqual(d["confidence_histogram"][9], 1)  # 0.95
        self.assertEqual(d["confidence_histogram"][6], 1)  # 0.62
        self.assertEqual(d["confidence_histogram"][0], 1)  # 0.05
        self.assertEqual(d["latency_ms"]["median"], 150)

    def test_creature_verdicts_follow_the_autopilots_thresholds(self):
        (self.folder / "creatures.json").write_text(json.dumps({
            "a goat": {"prey": 0.07, "threat": 0.6, "learned": False},
            "a zombie": {"prey": 0.88, "threat": 1.6, "learned": False},
            "a wraith": {"prey": 0.88, "threat": 2.4, "learned": False},
            "a spectre": {"prey": 0.9, "threat": 1.0, "learned": True}}))
        verdicts = {c["name"]: c["verdict"] for c in build_stats(self.folder)["creatures"]}
        self.assertEqual(verdicts, {"a goat": "ignore", "a zombie": "hunt",
                                    "a wraith": "avoid", "a spectre": "avoid"})

    def test_a_half_written_line_is_skipped(self):
        (self.folder / "jev.jsonl").write_text(json.dumps(decision("fight")) + '\n{"at": 1, "dec')
        self.assertEqual(build_stats(self.folder)["decisions"]["count"], 1)


class Page(unittest.TestCase):
    def test_creature_names_cannot_break_out_of_the_page(self):
        stats = build_stats(pathlib.Path(tempfile.gettempdir()) / "no-such-folder")
        stats["creatures"] = [{"name": "</script><script>alert(1)</script>", "prey": 0.9,
                               "threat": 1.0, "learned": False, "verdict": "hunt", "kills": 0}]
        page = render_page(stats)
        self.assertNotIn("</script><script>alert", page)
        self.assertEqual(page.count("<script"), 2)         # the data block and the drawing code

    def test_standalone_is_a_full_document_and_the_fragment_is_not(self):
        stats = build_stats(pathlib.Path(tempfile.gettempdir()) / "no-such-folder")
        self.assertTrue(render_page(stats).startswith("<!doctype html>"))
        self.assertTrue(render_page(stats, standalone=False).startswith("<title>Jev Stats</title>"))


if __name__ == "__main__":
    unittest.main()
