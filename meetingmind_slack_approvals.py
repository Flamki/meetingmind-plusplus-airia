#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict

import requests


def verify_slack_signature(signing_secret: str, timestamp: str, body: bytes, signature: str) -> bool:
    if not signing_secret:
        return True
    try:
        ts = int(timestamp)
    except ValueError:
        return False
    if abs(int(time.time()) - ts) > 60 * 5:
        return False
    base = f"v0:{timestamp}:{body.decode('utf-8')}".encode("utf-8")
    expected = "v0=" + hmac.new(signing_secret.encode("utf-8"), base, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def call_approval_callback(callback_url: str, execution_id: str, decision: str) -> Dict[str, Any]:
    payload = {"executionId": execution_id, "decision": decision}
    resp = requests.post(callback_url, json=payload, timeout=30)
    if resp.status_code >= 400:
        raise RuntimeError(f"callback failed ({resp.status_code}): {resp.text}")
    if not resp.text.strip():
        return {"ok": True}
    try:
        return resp.json()
    except json.JSONDecodeError:
        return {"ok": True, "raw": resp.text[:1000]}


class SlackApprovalHandler(BaseHTTPRequestHandler):
    def _json(self, status: int, payload: Dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        content_length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(content_length)

        signing_secret = os.getenv("SLACK_SIGNING_SECRET", "").strip()
        ts = self.headers.get("X-Slack-Request-Timestamp", "")
        sig = self.headers.get("X-Slack-Signature", "")
        if not verify_slack_signature(signing_secret, ts, raw_body, sig):
            self._json(401, {"ok": False, "error": "invalid_slack_signature"})
            return

        form = urllib.parse.parse_qs(raw_body.decode("utf-8"), keep_blank_values=True)
        payload_str = (form.get("payload") or [""])[0]
        if not payload_str:
            self._json(400, {"ok": False, "error": "missing_payload"})
            return
        try:
            payload = json.loads(payload_str)
        except json.JSONDecodeError:
            self._json(400, {"ok": False, "error": "invalid_payload_json"})
            return

        actions = payload.get("actions") or []
        if not actions:
            self._json(400, {"ok": False, "error": "missing_actions"})
            return
        action = actions[0]
        value_str = str(action.get("value", "")).strip()
        if not value_str:
            self._json(400, {"ok": False, "error": "missing_action_value"})
            return
        try:
            action_payload = json.loads(value_str)
        except json.JSONDecodeError:
            self._json(400, {"ok": False, "error": "invalid_action_value"})
            return

        execution_id = str(action_payload.get("executionId", "")).strip()
        decision = str(action_payload.get("decision", "")).strip().lower()
        if decision not in {"approve", "deny"} or not execution_id:
            self._json(400, {"ok": False, "error": "invalid_decision_or_execution_id"})
            return

        callback_url = os.getenv("AIRIA_APPROVAL_CALLBACK_URL", "").strip()
        if not callback_url:
            self._json(500, {"ok": False, "error": "missing_AIRIA_APPROVAL_CALLBACK_URL"})
            return

        try:
            callback_result = call_approval_callback(callback_url, execution_id, decision)
        except Exception as exc:
            self._json(500, {"ok": False, "error": f"callback_exception: {exc}"})
            return

        decision_text = "APPROVED" if decision == "approve" else "DENIED"
        self._json(
            200,
            {
                "response_type": "in_channel",
                "replace_original": True,
                "text": f"MeetingMind execution `{execution_id}` was {decision_text}.",
                "callback_result": callback_result,
            },
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Slack interactive approval webhook for MeetingMind")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()

    server = HTTPServer((args.host, args.port), SlackApprovalHandler)
    print(f"[INFO] Slack approval handler listening on http://{args.host}:{args.port}")
    print("[INFO] Configure this URL in Slack App Interactivity settings.")
    print("[INFO] Required env vars: AIRIA_APPROVAL_CALLBACK_URL, optional SLACK_SIGNING_SECRET")
    server.serve_forever()


if __name__ == "__main__":
    main()
