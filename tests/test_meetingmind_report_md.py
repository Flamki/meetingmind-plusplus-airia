import unittest

from meetingmind_report_md import render_markdown, top_runs_by_actions


class MeetingMindReportMarkdownTests(unittest.TestCase):
    def test_top_runs_sorted(self) -> None:
        runs = [
            {"status": "ok", "file": "a.txt", "action_item_count": 1},
            {"status": "error", "file": "bad.txt", "action_item_count": 10},
            {"status": "ok", "file": "b.txt", "action_item_count": 4},
        ]
        top = top_runs_by_actions(runs, limit=2)
        self.assertEqual(len(top), 2)
        self.assertEqual(top[0]["file"], "b.txt")

    def test_render_markdown_contains_kpis(self) -> None:
        report = {
            "meta": {
                "generated_at_utc": "2026-03-16T00:00:00Z",
                "pipeline_id": "pid",
                "input_dir": "C:\\x",
                "pattern": "*.txt",
                "recursive": True,
                "files_processed": 2,
                "total_duration_seconds": 5.5,
                "avg_runtime_seconds": 2.75,
            },
            "kpis": {
                "successful_runs": 2,
                "failed_runs": 0,
                "total_action_items": 7,
                "avg_action_items_per_success": 3.5,
                "top_owners": [{"owner": "Ava", "count": 3}],
            },
            "runs": [{"status": "ok", "file": "a.txt", "action_item_count": 3, "duration_seconds": 1.2}],
        }
        md = render_markdown(report, "Title")
        self.assertIn("# Title", md)
        self.assertIn("Processed **2** transcript(s)", md)
        self.assertIn("Total action items: **7**", md)


if __name__ == "__main__":
    unittest.main()
