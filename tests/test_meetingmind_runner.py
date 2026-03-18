import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from meetingmind_runner import (
    DeliveryLedger,
    MeetingMindError,
    analyze_sentiment_and_risk,
    build_memory_insights,
    make_idempotency_key,
    normalize_action_items,
    parse_recipients,
    parse_deadline_to_date,
    request_with_retries,
    extract_action_items,
    ActionItem,
    call_airia,
    call_airia_via_webhook,
    clean_airia_markup,
    is_pending_human_approval,
    load_dotenv_file,
    post_slack_approval_request,
    post_to_slack_via_api,
    post_to_teams,
    create_asana_task,
)


class MeetingMindRunnerTests(unittest.TestCase):
    def test_extract_action_items_from_json_array(self) -> None:
        text = """
        Here are actions:
        [{"task":"Ship release notes","owner":"Ava","deadline":"Friday"}]
        """
        items = extract_action_items(text)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].task, "Ship release notes")
        self.assertEqual(items[0].owner, "Ava")

    def test_extract_action_items_from_json_object_key(self) -> None:
        text = """
        ```json
        {"action_items":[{"task":"Prepare RCA","owner":"Noah","deadline":"Tomorrow"}]}
        ```
        """
        items = extract_action_items(text)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].task, "Prepare RCA")
        self.assertEqual(items[0].owner, "Noah")

    def test_extract_action_items_fallback_and_dedup(self) -> None:
        text = """
        - Action: follow up with legal
        - Action: follow up with legal
        1. Next step: create Jira task
        """
        items = extract_action_items(text)
        self.assertEqual(len(items), 2)
        self.assertIn("follow up with legal", items[0].task.lower())

    def test_normalize_action_items_removes_duplicates(self) -> None:
        raw = [
            ActionItem(task="  Build API  ", owner="Mia", deadline="Monday"),
            ActionItem(task="Build API", owner="mia", deadline="monday"),
            ActionItem(task="   ", owner="X", deadline="Y"),
        ]
        items = normalize_action_items(raw)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].task, "Build API")

    def test_extract_action_items_owner_markdown(self) -> None:
        text = """
        ## Action Items
        - **Arjun**: Finalize onboarding checklist API and deploy to staging by March 18
        - **Maya**: Deliver progress indicator UI polish by March 17 EOD
        """
        items = extract_action_items(text)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].owner, "Arjun")
        self.assertIn("March 18", items[0].deadline)

    def test_clean_airia_markup_prefers_artifact(self) -> None:
        text = """
        <airiaThinking>hidden</airiaThinking>
        <airiaArtifact identifier="x" type="text/markdown" title="t">
        # Meeting Summary
        Body text
        </airiaArtifact>
        Tail
        """
        cleaned = clean_airia_markup(text)
        self.assertIn("# Meeting Summary", cleaned)
        self.assertNotIn("airiaThinking", cleaned)
        self.assertNotIn("Tail", cleaned)

    def test_delivery_ledger_persists(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            ledger_path = Path(temp_dir) / "ledger.json"
            ledger = DeliveryLedger(ledger_path)
            key = make_idempotency_key("slack", "abc", {"x": 1})
            self.assertFalse(ledger.has(key))
            ledger.mark(key, {"channel": "slack"})
            self.assertTrue(ledger.has(key))

            reloaded = DeliveryLedger(ledger_path)
            self.assertTrue(reloaded.has(key))

    def test_load_dotenv_file_sets_missing_vars_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / ".env"
            env_path.write_text("\ufeffFOO=bar\nQUOTED='baz'\n#COMMENT=1\n", encoding="utf-8")
            with patch.dict("os.environ", {"FOO": "keep"}, clear=False):
                load_dotenv_file(env_path)
                self.assertEqual(os.environ.get("FOO"), "keep")
                self.assertEqual(os.environ.get("QUOTED"), "baz")

    @patch("meetingmind_runner.time.sleep", autospec=True)
    @patch("meetingmind_runner.requests.request", autospec=True)
    def test_request_with_retries_on_transient_error(self, mock_request: Mock, _sleep: Mock) -> None:
        error = requests.exceptions.ConnectionError("temp")
        ok_response = Mock()
        ok_response.status_code = 200
        mock_request.side_effect = [error, ok_response]

        response = request_with_retries("GET", "https://example.com", retries=2, backoff_seconds=0, jitter_seconds=0)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(mock_request.call_count, 2)

    @patch("meetingmind_runner.requests.request", autospec=True)
    def test_request_with_retries_fails_after_limit(self, mock_request: Mock) -> None:
        mock_request.side_effect = requests.exceptions.Timeout("timeout")
        with self.assertRaises(MeetingMindError):
            request_with_retries("GET", "https://example.com", retries=1, backoff_seconds=0, jitter_seconds=0)

    @patch("meetingmind_runner.request_with_retries", autospec=True)
    def test_call_airia_requires_object_response(self, mock_request_with_retries: Mock) -> None:
        fake_resp = Mock()
        fake_resp.status_code = 200
        fake_resp.json.return_value = ["not-an-object"]
        mock_request_with_retries.return_value = fake_resp

        with self.assertRaises(MeetingMindError):
            call_airia("pid", "key", "input")

    @patch("meetingmind_runner.request_with_retries", autospec=True)
    def test_call_airia_via_webhook_success(self, mock_request_with_retries: Mock) -> None:
        fake_resp = Mock()
        fake_resp.status_code = 200
        fake_resp.json.return_value = {"result": "ok"}
        mock_request_with_retries.return_value = fake_resp

        out = call_airia_via_webhook("https://example.com/w", "hello")
        self.assertEqual(out.get("result"), "ok")

    @patch("meetingmind_runner.request_with_retries", autospec=True)
    def test_call_airia_via_webhook_all_variants_fail(self, mock_request_with_retries: Mock) -> None:
        bad_resp = Mock()
        bad_resp.status_code = 400
        bad_resp.text = "bad request"
        mock_request_with_retries.return_value = bad_resp

        with self.assertRaises(MeetingMindError):
            call_airia_via_webhook("https://example.com/w", "hello")

    @patch("meetingmind_runner.request_with_retries", autospec=True)
    def test_call_airia_via_webhook_empty_body_is_accepted(self, mock_request_with_retries: Mock) -> None:
        resp = Mock()
        resp.status_code = 200
        resp.text = ""
        mock_request_with_retries.return_value = resp
        out = call_airia_via_webhook("https://example.com/w", "hello")
        self.assertTrue(out.get("webhookAccepted"))
        self.assertEqual(out.get("result"), "")

    def test_is_pending_human_approval_shape(self) -> None:
        payload = {
            "$type": "string",
            "result": "c7fdf255-3430-48d8-a072-aae4ad063697",
            "report": None,
            "executionId": "6be0181e-93b5-4abe-9608-f917728daac6",
        }
        self.assertTrue(is_pending_human_approval(payload))

    def test_parse_deadline_to_date_iso(self) -> None:
        parsed = parse_deadline_to_date("2026-03-20")
        self.assertIsNotNone(parsed)
        self.assertEqual(str(parsed), "2026-03-20")

    def test_parse_recipients_dedup(self) -> None:
        out = parse_recipients("a@x.com; b@x.com, A@x.com")
        self.assertEqual(out, ["a@x.com", "b@x.com"])

    def test_analyze_sentiment_and_risk_overdue(self) -> None:
        items = [
            ActionItem(task="Fix contract draft", owner="Ava", deadline="2026-03-10", priority="high"),
            ActionItem(task="Share recap", owner="Unassigned", deadline="TBD", priority="medium"),
        ]
        risk = analyze_sentiment_and_risk(
            "Project is blocked and there is escalation risk and unresolved conflict.",
            items,
            today=parse_deadline_to_date("2026-03-16"),
        )
        self.assertEqual(risk.get("risk_level"), "high")
        self.assertTrue(risk.get("overdue_actions"))
        self.assertGreaterEqual(risk.get("missing_owner_count", 0), 1)

    def test_build_memory_insights_detects_repeated(self) -> None:
        memory_store = {
            "runs": [
                {
                    "timestamp": "2026-03-15T10:00:00Z",
                    "actions": [
                        {"task": "Finalize deck", "owner": "Noah", "deadline": "2026-03-17", "priority": "high"}
                    ],
                }
            ]
        }
        insights = build_memory_insights(
            [ActionItem(task="Finalize deck", owner="Noah", deadline="2026-03-19", priority="high")],
            memory_store,
        )
        joined = " ".join(insights).lower()
        self.assertIn("repeated", joined)

    @patch("meetingmind_runner.request_with_retries", autospec=True)
    def test_post_to_slack_via_api_includes_scope_details(self, mock_request_with_retries: Mock) -> None:
        fake_resp = Mock()
        fake_resp.status_code = 200
        fake_resp.json.return_value = {
            "ok": False,
            "error": "missing_scope",
            "needed": "chat:write:bot",
            "provided": "identify",
        }
        mock_request_with_retries.return_value = fake_resp
        with self.assertRaises(MeetingMindError) as ctx:
            post_to_slack_via_api(
                token="xoxb-test",
                channel="#general",
                summary_text="summary",
                actions=[],
            )
        self.assertIn("needed=chat:write:bot", str(ctx.exception))

    @patch("meetingmind_runner.request_with_retries", autospec=True)
    def test_post_to_teams_success(self, mock_request_with_retries: Mock) -> None:
        fake_resp = Mock()
        fake_resp.status_code = 200
        fake_resp.text = "1"
        mock_request_with_retries.return_value = fake_resp
        ok = post_to_teams(
            webhook_url="https://example.com/webhook",
            summary_text="summary",
            actions=[],
            risk={"risk_level": "low", "sentiment": "neutral"},
            memory_insights=["none"],
        )
        self.assertTrue(ok)

    @patch("meetingmind_runner.request_with_retries", autospec=True)
    def test_create_asana_task_parses_gid(self, mock_request_with_retries: Mock) -> None:
        fake_resp = Mock()
        fake_resp.status_code = 201
        fake_resp.json.return_value = {"data": {"gid": "12001234567890"}}
        mock_request_with_retries.return_value = fake_resp
        gid = create_asana_task(
            personal_access_token="pat",
            project_gid="12009999999999",
            name="Task",
            notes="Notes",
            due_on_iso="2026-03-20",
        )
        self.assertEqual(gid, "12001234567890")

    @patch("meetingmind_runner.request_with_retries", autospec=True)
    def test_post_slack_approval_request_webhook(self, mock_request_with_retries: Mock) -> None:
        fake_resp = Mock()
        fake_resp.status_code = 200
        fake_resp.text = "ok"
        mock_request_with_retries.return_value = fake_resp
        detail = post_slack_approval_request(
            execution_id="6be0181e-93b5-4abe-9608-f917728daac6",
            summary_preview="Approval preview text",
            slack_webhook_url="https://hooks.slack.com/services/x/y/z",
            approve_url_template="https://example.com/approve?e={execution_id}",
            deny_url_template="https://example.com/deny?e={execution_id}",
        )
        self.assertEqual(detail, "posted_webhook")


if __name__ == "__main__":
    unittest.main()
