#!/usr/bin/env python3
"""
MeetingMind Runner

Calls an Airia PipelineExecution endpoint and optionally forwards results to:
- Slack (incoming webhook)
- Jira (create issues)
- Email (SMTP)

Usage examples:
  python meetingmind_runner.py --user-input "Summarize this meeting transcript..."
  python meetingmind_runner.py --transcript-file sample.txt --create-jira --post-slack --send-email
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import random
import re
import smtplib
import sys
import time
import ssl
import threading
from dataclasses import asdict, dataclass
from datetime import date, datetime
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import requests

RETRYABLE_STATUS_CODES = {408, 409, 425, 429, 500, 502, 503, 504}
DEFAULT_TIMEOUT_SECONDS = 120


@dataclass
class ActionItem:
    task: str
    owner: str = "Unassigned"
    deadline: str = "TBD"
    priority: str = "medium"


class MeetingMindError(Exception):
    pass


@dataclass
class IntegrationOutcome:
    channel: str
    success: bool
    detail: str
    artifact: str = ""


def configure_console_encoding() -> None:
    # Prevent Windows cp1252 console crashes when model output contains emoji/unicode.
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def parse_deadline_to_date(deadline: str) -> Optional[date]:
    value = (deadline or "").strip()
    if not value or value.upper() == "TBD":
        return None
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%m/%d/%Y", "%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    iso_like = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", value)
    if iso_like:
        try:
            return datetime.strptime(iso_like.group(1), "%Y-%m-%d").date()
        except ValueError:
            return None
    return None


def analyze_sentiment_and_risk(
    summary_text: str,
    actions: Sequence[ActionItem],
    *,
    today: Optional[date] = None,
) -> Dict[str, Any]:
    now = today or date.today()
    text = (summary_text or "").lower()
    negative_markers = [
        "blocked",
        "delay",
        "risk",
        "escalation",
        "conflict",
        "concern",
        "urgent",
        "missed",
        "overdue",
        "slipping",
        "unresolved",
    ]
    positive_markers = [
        "on track",
        "aligned",
        "resolved",
        "completed",
        "confirmed",
        "approved",
        "green",
    ]
    negative_hits = sum(1 for marker in negative_markers if marker in text)
    positive_hits = sum(1 for marker in positive_markers if marker in text)
    score = positive_hits - negative_hits
    if score >= 2:
        sentiment = "positive"
    elif score <= -2:
        sentiment = "negative"
    else:
        sentiment = "neutral"

    missing_owner_count = sum(1 for a in actions if a.owner.lower() in {"unassigned", "", "tbd"})
    missing_deadline_count = sum(1 for a in actions if a.deadline.lower() in {"tbd", "", "unknown"})
    overdue_actions: List[str] = []
    for a in actions:
        deadline_date = parse_deadline_to_date(a.deadline)
        if deadline_date and deadline_date < now:
            overdue_actions.append(f"{a.task} (owner={a.owner}, deadline={a.deadline})")

    risk_signals: List[str] = []
    if negative_hits >= 2:
        risk_signals.append("Multiple negative risk markers found in meeting text")
    if missing_owner_count > 0:
        risk_signals.append(f"{missing_owner_count} action item(s) have no clear owner")
    if missing_deadline_count > 0:
        risk_signals.append(f"{missing_deadline_count} action item(s) have no clear deadline")
    if overdue_actions:
        risk_signals.append(f"{len(overdue_actions)} action item(s) are past deadline")

    if overdue_actions or negative_hits >= 3:
        risk_level = "high"
    elif risk_signals:
        risk_level = "medium"
    else:
        risk_level = "low"

    return {
        "sentiment": sentiment,
        "risk_level": risk_level,
        "negative_hits": negative_hits,
        "positive_hits": positive_hits,
        "missing_owner_count": missing_owner_count,
        "missing_deadline_count": missing_deadline_count,
        "overdue_actions": overdue_actions,
        "risk_signals": risk_signals,
    }


def format_risk_brief(risk: Dict[str, Any]) -> str:
    level = str(risk.get("risk_level", "unknown")).upper()
    sentiment = str(risk.get("sentiment", "neutral")).upper()
    signals = risk.get("risk_signals") or []
    if signals:
        return f"Risk={level}, Sentiment={sentiment}. Signals: " + " | ".join(str(s) for s in signals)
    return f"Risk={level}, Sentiment={sentiment}. No major risk signals detected."


def parse_recipients(value: str) -> List[str]:
    if not value:
        return []
    parts = re.split(r"[;,]", value)
    recipients = [p.strip() for p in parts if p.strip()]
    unique: List[str] = []
    seen = set()
    for item in recipients:
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def load_memory_store(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"runs": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict) and isinstance(payload.get("runs"), list):
            return payload
    except (json.JSONDecodeError, OSError):
        pass
    return {"runs": []}


def save_memory_store(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    temp_path.replace(path)


def build_memory_insights(current_actions: Sequence[ActionItem], memory_store: Dict[str, Any]) -> List[str]:
    runs = memory_store.get("runs", [])
    if not isinstance(runs, list) or not runs:
        return ["No prior meeting memory available yet."]

    prior_keys: set[Tuple[str, str]] = set()
    for run in runs[-8:]:
        if not isinstance(run, dict):
            continue
        for item in run.get("actions", []):
            if isinstance(item, dict):
                task = str(item.get("task", "")).strip().lower()
                owner = str(item.get("owner", "")).strip().lower()
                if task:
                    prior_keys.add((task, owner))

    repeated: List[str] = []
    for action in current_actions:
        key = (action.task.strip().lower(), action.owner.strip().lower())
        if key in prior_keys:
            repeated.append(f"{action.task} (owner={action.owner})")

    insights: List[str] = []
    if repeated:
        insights.append(f"{len(repeated)} repeated action(s) are still open from earlier meetings")
        insights.extend(repeated[:5])
    else:
        insights.append("No repeated open actions detected from recent meeting memory.")
    return insights


def append_memory_run(
    memory_store: Dict[str, Any],
    actions: Sequence[ActionItem],
    risk: Dict[str, Any],
) -> Dict[str, Any]:
    runs = memory_store.get("runs")
    if not isinstance(runs, list):
        runs = []
    runs.append(
        {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "risk_level": risk.get("risk_level", "unknown"),
            "sentiment": risk.get("sentiment", "neutral"),
            "actions": [a.__dict__ for a in actions],
        }
    )
    # Keep last 50 runs for lightweight local memory.
    memory_store["runs"] = runs[-50:]
    return memory_store


def load_dotenv_file(path: Path) -> None:
    if not path.exists() or not path.is_file():
        return
    try:
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip().lstrip("\ufeff")
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            name = name.strip().lstrip("\ufeff")
            value = value.strip()
            if not name:
                continue
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            if name not in os.environ:
                os.environ[name] = value
    except OSError as exc:
        raise MeetingMindError(f"Failed reading env file '{path}': {exc}") from exc


def hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class DeliveryLedger:
    """Simple JSON-backed idempotency ledger."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._data: Dict[str, Any] = self._load()

    def _load(self) -> Dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                return payload
        except (json.JSONDecodeError, OSError):
            pass
        return {}

    def _persist(self) -> None:
        temp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        temp_path.write_text(json.dumps(self._data, indent=2, ensure_ascii=False), encoding="utf-8")
        temp_path.replace(self.path)

    def has(self, key: str) -> bool:
        with self._lock:
            return key in self._data

    def mark(self, key: str, metadata: Dict[str, Any]) -> None:
        with self._lock:
            self._data[key] = metadata
            self._persist()


def env(name: str, required: bool = False, default: Optional[str] = None) -> str:
    value = os.getenv(name, default)
    if required and not value:
        print(f"[ERROR] Missing required environment variable: {name}", file=sys.stderr)
        sys.exit(1)
    return value or ""


def request_with_retries(
    method: str,
    url: str,
    *,
    retries: int = 3,
    backoff_seconds: float = 1.25,
    jitter_seconds: float = 0.25,
    retry_statuses: Optional[Iterable[int]] = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    **kwargs: Any,
) -> requests.Response:
    statuses = set(retry_statuses or RETRYABLE_STATUS_CODES)
    last_error: Optional[Exception] = None

    for attempt in range(retries + 1):
        try:
            resp = requests.request(method, url, timeout=timeout, **kwargs)
            if resp.status_code in statuses and attempt < retries:
                delay = backoff_seconds * (2**attempt) + random.uniform(0, max(jitter_seconds, 0))
                time.sleep(delay)
                continue
            return resp
        except requests.RequestException as exc:
            last_error = exc
            if attempt >= retries:
                break
            delay = backoff_seconds * (2**attempt) + random.uniform(0, max(jitter_seconds, 0))
            time.sleep(delay)

    raise MeetingMindError(f"Request failed after {retries + 1} attempts: {last_error}")


def call_airia(
    pipeline_id: str,
    api_key: str,
    user_input: str,
    async_output: bool = False,
    webhook_url: Optional[str] = None,
    retries: int = 3,
    backoff_seconds: float = 1.25,
    jitter_seconds: float = 0.25,
) -> Dict[str, Any]:
    if webhook_url and webhook_url.strip():
        return call_airia_via_webhook(
            webhook_url=webhook_url,
            user_input=user_input,
            async_output=async_output,
            retries=retries,
            backoff_seconds=backoff_seconds,
            jitter_seconds=jitter_seconds,
        )

    if not pipeline_id or not api_key:
        raise MeetingMindError("Pipeline mode requires both pipeline_id and api_key")

    url = f"https://api.airia.ai/v2/PipelineExecution/{pipeline_id}"
    headers = {
        "X-API-KEY": api_key,
        "Content-Type": "application/json",
    }
    payload = {"userInput": user_input, "asyncOutput": async_output}
    resp = request_with_retries(
        "POST",
        url,
        headers=headers,
        json=payload,
        retries=retries,
        backoff_seconds=backoff_seconds,
        jitter_seconds=jitter_seconds,
    )
    if resp.status_code >= 400:
        raise MeetingMindError(f"Airia call failed ({resp.status_code}): {resp.text}")
    try:
        data = resp.json()
    except json.JSONDecodeError as exc:
        raise MeetingMindError(f"Airia response was not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise MeetingMindError("Airia response JSON must be an object")
    return data


def call_airia_via_webhook(
    webhook_url: str,
    user_input: str,
    async_output: bool = False,
    retries: int = 3,
    backoff_seconds: float = 1.25,
    jitter_seconds: float = 0.25,
) -> Dict[str, Any]:
    payload_variants: List[Dict[str, Any]] = [
        {"userInput": user_input, "asyncOutput": async_output},
        {"input": user_input, "asyncOutput": async_output},
        {"message": user_input, "asyncOutput": async_output},
    ]
    headers = {"Content-Type": "application/json"}
    errors: List[str] = []

    for payload in payload_variants:
        resp = request_with_retries(
            "POST",
            webhook_url,
            headers=headers,
            json=payload,
            retries=retries,
            backoff_seconds=backoff_seconds,
            jitter_seconds=jitter_seconds,
        )
        if resp.status_code >= 400:
            errors.append(f"{resp.status_code}: {resp.text[:300]}")
            continue
        if not resp.text or not resp.text.strip():
            # Some webhook deployments are fire-and-forget and return empty body on success.
            return {"result": "", "webhookAccepted": True}
        try:
            data = resp.json()
        except json.JSONDecodeError as exc:
            text = resp.text.strip()
            if text:
                # Fallback for text/plain webhook responses.
                return {"result": text, "webhookAccepted": True}
            raise MeetingMindError(f"Webhook response was not valid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise MeetingMindError("Webhook response JSON must be an object")
        return data

    joined = " | ".join(errors) if errors else "Unknown webhook error"
    raise MeetingMindError(f"Webhook call failed for all payload variants: {joined}")


def best_text_from_airia_response(data: Dict[str, Any]) -> str:
    # Airia response shapes can vary by pipeline/interface. We try common keys first.
    candidates = [
        data.get("output"),
        data.get("result"),
        data.get("text"),
        data.get("message"),
        data.get("response"),
    ]
    if isinstance(data.get("data"), dict):
        nested = data.get("data", {})
        candidates.extend([nested.get("output"), nested.get("text"), nested.get("result")])
    if isinstance(data.get("choices"), list):
        for item in data.get("choices", []):
            if isinstance(item, dict):
                message = item.get("message")
                if isinstance(message, dict):
                    candidates.append(message.get("content"))
                candidates.append(item.get("text"))
    for c in candidates:
        if isinstance(c, str) and c.strip():
            return clean_airia_markup(c.strip())
    # Fallback to full JSON string for downstream parsing/debug.
    return json.dumps(data, indent=2, ensure_ascii=False)


def is_pending_human_approval(data: Dict[str, Any]) -> bool:
    result = data.get("result")
    execution_id = data.get("executionId")
    if not (isinstance(result, str) and isinstance(execution_id, str)):
        return False
    uuid_like = re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}", result)
    if not uuid_like:
        return False
    # Airia returns this shape when approval-gated flows are accepted but paused.
    return data.get("$type") == "string" or data.get("report") is None


def clean_airia_markup(text: str) -> str:
    cleaned = re.sub(r"<airiaThinking>[\s\S]*?</airiaThinking>", "", text, flags=re.IGNORECASE).strip()
    artifacts = re.findall(
        r"<airiaArtifact\b[^>]*>([\s\S]*?)</airiaArtifact>",
        cleaned,
        flags=re.IGNORECASE,
    )
    if not artifacts:
        artifacts = re.findall(
            r"<artifact\b[^>]*>([\s\S]*?)</artifact>",
            cleaned,
            flags=re.IGNORECASE,
        )
    if artifacts:
        # Prefer artifact body since it is usually the structured output.
        cleaned = "\n\n".join([a.strip() for a in artifacts if a.strip()])
    # Remove any remaining custom XML-like tags if present.
    cleaned = re.sub(r"</?airia[^>]*>", "", cleaned, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"</?artifact[^>]*>", "", cleaned, flags=re.IGNORECASE).strip()
    return cleaned


def _json_candidates_from_text(text: str) -> Sequence[str]:
    candidates: List[str] = []
    stripped = text.strip()
    if stripped:
        candidates.append(stripped)

    for match in re.findall(r"```json\s*(.*?)```", text, flags=re.DOTALL | re.IGNORECASE):
        candidates.append(match.strip())
    for match in re.findall(r"```\s*(.*?)```", text, flags=re.DOTALL):
        candidates.append(match.strip())

    # Captures the first array-looking payload if present.
    bracket_match = re.search(r"\[\s*\{[\s\S]*?\}\s*\]", text)
    if bracket_match:
        candidates.append(bracket_match.group(0).strip())

    return candidates


def _as_action_items(payload: Any) -> List[ActionItem]:
    objects: List[Dict[str, Any]] = []
    if isinstance(payload, list):
        objects = [obj for obj in payload if isinstance(obj, dict)]
    elif isinstance(payload, dict):
        for key in ("action_items", "actions", "tasks", "todo"):
            value = payload.get(key)
            if isinstance(value, list):
                objects = [obj for obj in value if isinstance(obj, dict)]
                break
        if not objects and all(k in payload for k in ("task",)):
            objects = [payload]

    result: List[ActionItem] = []
    for obj in objects:
        task = str(obj.get("task", "")).strip()
        owner = str(obj.get("owner", "Unassigned")).strip() or "Unassigned"
        deadline = str(obj.get("deadline", "TBD")).strip() or "TBD"
        priority = str(obj.get("priority", "medium")).strip().lower() or "medium"
        if priority not in {"high", "medium", "low"}:
            priority = "medium"
        if task:
            result.append(ActionItem(task=task, owner=owner, deadline=deadline, priority=priority))
    return result


def normalize_action_items(items: Sequence[ActionItem], max_items: int = 100) -> List[ActionItem]:
    seen: set[Tuple[str, str, str]] = set()
    normalized: List[ActionItem] = []
    for item in items:
        task = re.sub(r"\s+", " ", item.task).strip()
        owner = re.sub(r"\s+", " ", item.owner).strip() or "Unassigned"
        deadline = re.sub(r"\s+", " ", item.deadline).strip() or "TBD"
        priority = (item.priority or "medium").strip().lower()
        if priority not in {"high", "medium", "low"}:
            priority = "medium"
        if not task:
            continue
        key = (task.lower(), owner.lower(), deadline.lower())
        if key in seen:
            continue
        seen.add(key)
        normalized.append(ActionItem(task=task[:300], owner=owner[:120], deadline=deadline[:120], priority=priority))
        if len(normalized) >= max_items:
            break
    return normalized


def extract_action_items(text: str, max_items: int = 100) -> List[ActionItem]:
    for candidate in _json_candidates_from_text(text):
        try:
            parsed = json.loads(candidate)
            items = _as_action_items(parsed)
            if items:
                return normalize_action_items(items, max_items=max_items)
        except json.JSONDecodeError:
            pass

    # Fallback: line-based extraction from bullet points.
    items: List[ActionItem] = []
    lines = text.splitlines()
    has_action_heading = any(
        re.match(r"^\s*#+\s+.*action", ln, flags=re.IGNORECASE) for ln in lines
    )
    allow_global = not has_action_heading
    in_action_section = False
    for line in lines:
        raw = line.strip()
        if raw.startswith("- "):
            clean = raw[2:].strip()
        elif raw.startswith("* "):
            clean = raw[2:].strip()
        else:
            clean = raw
        if not clean:
            continue
        heading = re.match(r"^#+\s+(?P<title>.+)$", clean)
        if heading:
            title = heading.group("title").strip().lower()
            in_action_section = "action item" in title or title == "actions" or title == "action items"
            continue
        # Common markdown format: **Owner**: Task by Deadline
        md_owner = (
            re.match(r"^\*\*(?P<owner>[^*]+)\*\*:\s*(?P<task>.+)$", clean)
            if (in_action_section or allow_global)
            else None
        )
        if md_owner:
            owner = md_owner.group("owner").strip()
            task_text = md_owner.group("task").strip()
            deadline = "TBD"
            by_match = re.search(r"\bby\s+(.+)$", task_text, flags=re.IGNORECASE)
            if by_match:
                deadline = by_match.group(1).strip().rstrip(".")
            items.append(ActionItem(task=task_text, owner=owner, deadline=deadline))
            continue

        plain_owner = (
            re.match(r"^(?P<owner>[A-Z][a-zA-Z .'-]{1,40}):\s*(?P<task>.+)$", clean)
            if (in_action_section or allow_global)
            else None
        )
        if plain_owner:
            owner = plain_owner.group("owner").strip()
            task_text = plain_owner.group("task").strip()
            deadline = "TBD"
            by_match = re.search(r"\bby\s+(.+)$", task_text, flags=re.IGNORECASE)
            if by_match:
                deadline = by_match.group(1).strip().rstrip(".")
            items.append(ActionItem(task=task_text, owner=owner, deadline=deadline))
            continue

        if (in_action_section or allow_global) and (
            any(k in clean.lower() for k in ["action", "todo", "follow up", "next step"])
            or re.match(r"^\d+\.\s+", clean)
            or clean.startswith("**")
        ):
            items.append(ActionItem(task=clean))
    return normalize_action_items(items, max_items=max_items)


def post_to_slack(
    webhook_url: str,
    summary_text: str,
    actions: List[ActionItem],
    risk: Optional[Dict[str, Any]] = None,
    memory_insights: Optional[Sequence[str]] = None,
    retries: int = 3,
    backoff_seconds: float = 1.25,
    jitter_seconds: float = 0.25,
) -> bool:
    action_lines = []
    if actions:
        for idx, a in enumerate(actions, 1):
            action_lines.append(
                f"{idx}. {a.task} | owner={a.owner} | deadline={a.deadline} | priority={a.priority}"
            )
    else:
        action_lines.append("No action items found.")

    blocks: List[Dict[str, Any]] = [
        {"type": "header", "text": {"type": "plain_text", "text": "MeetingMind Execution Update"}},
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Summary*\n{summary_text[:2800]}"},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "*Action Items*\n" + "\n".join(action_lines[:15])},
        },
    ]
    if risk:
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Risk Analysis*\n{format_risk_brief(risk)}"},
            }
        )
    if memory_insights:
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "*Meeting Memory*\n" + "\n".join(f"- {x}" for x in memory_insights[:5])},
            }
        )

    payload = {"text": "MeetingMind execution update", "blocks": blocks}
    resp = request_with_retries(
        "POST",
        webhook_url,
        json=payload,
        retries=retries,
        backoff_seconds=backoff_seconds,
        jitter_seconds=jitter_seconds,
        timeout=30,
    )
    if resp.status_code >= 400:
        raise MeetingMindError(f"Slack webhook failed ({resp.status_code}): {resp.text}")
    print("[OK] Posted summary to Slack")
    return True


def post_to_slack_via_api(
    token: str,
    channel: str,
    summary_text: str,
    actions: List[ActionItem],
    risk: Optional[Dict[str, Any]] = None,
    memory_insights: Optional[Sequence[str]] = None,
    retries: int = 3,
    backoff_seconds: float = 1.25,
    jitter_seconds: float = 0.25,
) -> bool:
    if not token.strip():
        raise MeetingMindError("Missing Slack token for API posting")
    if not channel.strip():
        raise MeetingMindError("Missing SLACK_CHANNEL for Slack API posting")

    action_lines = []
    if actions:
        for idx, a in enumerate(actions, 1):
            action_lines.append(
                f"{idx}. {a.task} | owner={a.owner} | deadline={a.deadline} | priority={a.priority}"
            )
    else:
        action_lines.append("No action items found.")

    blocks: List[Dict[str, Any]] = [
        {"type": "header", "text": {"type": "plain_text", "text": "MeetingMind Execution Update"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*Summary*\n{summary_text[:2800]}"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": "*Action Items*\n" + "\n".join(action_lines[:15])}},
    ]
    if risk:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"*Risk Analysis*\n{format_risk_brief(risk)}"}})
    if memory_insights:
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "*Meeting Memory*\n" + "\n".join(f"- {x}" for x in memory_insights[:5])},
            }
        )

    payload = {"channel": channel, "text": "MeetingMind execution update", "blocks": blocks}
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"}
    resp = request_with_retries(
        "POST",
        "https://slack.com/api/chat.postMessage",
        headers=headers,
        json=payload,
        retries=retries,
        backoff_seconds=backoff_seconds,
        jitter_seconds=jitter_seconds,
        timeout=30,
    )
    if resp.status_code >= 400:
        raise MeetingMindError(f"Slack API post failed ({resp.status_code}): {resp.text}")
    try:
        data = resp.json()
    except json.JSONDecodeError as exc:
        raise MeetingMindError(f"Slack API response was not JSON: {exc}") from exc
    if not data.get("ok"):
        error = data.get("error", "unknown_error")
        needed = data.get("needed")
        provided = data.get("provided")
        if needed or provided:
            raise MeetingMindError(
                f"Slack API post failed: {error} (needed={needed or 'n/a'}, provided={provided or 'n/a'})"
            )
        raise MeetingMindError(f"Slack API post failed: {error}")
    print("[OK] Posted summary to Slack (API)")
    return True


def post_slack_approval_request(
    *,
    execution_id: str,
    summary_preview: str,
    slack_webhook_url: str = "",
    slack_bot_token: str = "",
    slack_channel: str = "",
    approve_url_template: str = "",
    deny_url_template: str = "",
    retries: int = 3,
    backoff_seconds: float = 1.25,
    jitter_seconds: float = 0.25,
) -> str:
    if not execution_id.strip():
        raise MeetingMindError("Missing execution_id for Slack approval request")
    if not slack_webhook_url and not slack_bot_token:
        raise MeetingMindError("Slack approval notification needs SLACK_WEBHOOK_URL or SLACK_BOT_TOKEN")
    if slack_bot_token and not slack_webhook_url and not slack_channel:
        raise MeetingMindError("Slack approval with bot token needs SLACK_CHANNEL")

    approve_url = approve_url_template.replace("{execution_id}", execution_id) if approve_url_template else ""
    deny_url = deny_url_template.replace("{execution_id}", execution_id) if deny_url_template else ""
    use_url_buttons = bool(approve_url and deny_url)

    button_approve: Dict[str, Any] = {
        "type": "button",
        "action_id": "meetingmind_approve",
        "text": {"type": "plain_text", "text": "Approve"},
        "style": "primary",
        "value": json.dumps({"executionId": execution_id, "decision": "approve"}),
    }
    button_deny: Dict[str, Any] = {
        "type": "button",
        "action_id": "meetingmind_deny",
        "text": {"type": "plain_text", "text": "Deny"},
        "style": "danger",
        "value": json.dumps({"executionId": execution_id, "decision": "deny"}),
    }
    if use_url_buttons:
        button_approve["url"] = approve_url
        button_deny["url"] = deny_url

    blocks: List[Dict[str, Any]] = [
        {"type": "header", "text": {"type": "plain_text", "text": "MeetingMind Approval Required"}},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*Execution ID:* `{execution_id}`\n"
                    f"*Status:* Waiting for human approval in Airia.\n"
                    f"*Preview:* {summary_preview[:3500]}"
                ),
            },
        },
        {"type": "actions", "elements": [button_approve, button_deny]},
    ]

    if slack_webhook_url:
        payload = {"text": "MeetingMind approval required", "blocks": blocks}
        resp = request_with_retries(
            "POST",
            slack_webhook_url,
            json=payload,
            retries=retries,
            backoff_seconds=backoff_seconds,
            jitter_seconds=jitter_seconds,
            timeout=30,
        )
        if resp.status_code >= 400:
            raise MeetingMindError(f"Slack approval webhook failed ({resp.status_code}): {resp.text}")
        print("[OK] Slack approval request posted (webhook)")
        return "posted_webhook"

    headers = {"Authorization": f"Bearer {slack_bot_token}", "Content-Type": "application/json; charset=utf-8"}
    payload = {"channel": slack_channel, "text": "MeetingMind approval required", "blocks": blocks}
    resp = request_with_retries(
        "POST",
        "https://slack.com/api/chat.postMessage",
        headers=headers,
        json=payload,
        retries=retries,
        backoff_seconds=backoff_seconds,
        jitter_seconds=jitter_seconds,
        timeout=30,
    )
    if resp.status_code >= 400:
        raise MeetingMindError(f"Slack approval API post failed ({resp.status_code}): {resp.text}")
    try:
        data = resp.json()
    except json.JSONDecodeError as exc:
        raise MeetingMindError(f"Slack approval API response was not JSON: {exc}") from exc
    if not data.get("ok"):
        error = data.get("error", "unknown_error")
        needed = data.get("needed")
        provided = data.get("provided")
        if needed or provided:
            raise MeetingMindError(
                f"Slack approval API failed: {error} (needed={needed or 'n/a'}, provided={provided or 'n/a'})"
            )
        raise MeetingMindError(f"Slack approval API failed: {error}")
    print("[OK] Slack approval request posted (API)")
    return f"posted_api:{slack_channel}"


def post_to_teams(
    webhook_url: str,
    summary_text: str,
    actions: List[ActionItem],
    risk: Optional[Dict[str, Any]] = None,
    memory_insights: Optional[Sequence[str]] = None,
    retries: int = 3,
    backoff_seconds: float = 1.25,
    jitter_seconds: float = 0.25,
) -> bool:
    action_lines: List[str] = []
    if actions:
        for idx, a in enumerate(actions[:12], 1):
            action_lines.append(
                f"{idx}. {a.task} | owner={a.owner} | deadline={a.deadline} | priority={a.priority}"
            )
    else:
        action_lines.append("No action items found.")

    risk_level = str((risk or {}).get("risk_level", "low")).lower()
    theme_color = {"high": "C50F1F", "medium": "FFAA44", "low": "107C10"}.get(risk_level, "0078D4")
    sections: List[Dict[str, Any]] = [
        {"activityTitle": "Summary", "text": summary_text[:1800] or "No summary returned."},
        {"activityTitle": "Action Items", "text": "\n".join(f"- {x}" for x in action_lines)},
    ]
    if risk:
        sections.append({"activityTitle": "Risk Analysis", "text": format_risk_brief(risk)[:1500]})
    if memory_insights:
        sections.append(
            {
                "activityTitle": "Meeting Memory",
                "text": "\n".join(f"- {x}" for x in list(memory_insights)[:5])[:1500],
            }
        )

    payload = {
        "@type": "MessageCard",
        "@context": "https://schema.org/extensions",
        "summary": "MeetingMind execution update",
        "themeColor": theme_color,
        "title": "MeetingMind Execution Update",
        "sections": sections,
    }
    resp = request_with_retries(
        "POST",
        webhook_url,
        json=payload,
        retries=retries,
        backoff_seconds=backoff_seconds,
        jitter_seconds=jitter_seconds,
        timeout=30,
    )
    if resp.status_code >= 400:
        raise MeetingMindError(f"Teams webhook failed ({resp.status_code}): {resp.text}")
    print("[OK] Posted summary to Microsoft Teams")
    return True


def create_asana_task(
    personal_access_token: str,
    project_gid: str,
    name: str,
    notes: str,
    due_on_iso: Optional[str] = None,
    retries: int = 3,
    backoff_seconds: float = 1.25,
    jitter_seconds: float = 0.25,
) -> str:
    if not personal_access_token.strip():
        raise MeetingMindError("Missing ASANA_PAT")
    if not project_gid.strip():
        raise MeetingMindError("Missing ASANA_PROJECT_GID")

    url = "https://app.asana.com/api/1.0/tasks"
    headers = {
        "Authorization": f"Bearer {personal_access_token}",
        "Content-Type": "application/json",
    }
    task_data: Dict[str, Any] = {
        "name": name[:200],
        "notes": notes[:5000],
        "projects": [project_gid],
    }
    if due_on_iso:
        task_data["due_on"] = due_on_iso
    payload = {"data": task_data}
    resp = request_with_retries(
        "POST",
        url,
        headers=headers,
        json=payload,
        retries=retries,
        backoff_seconds=backoff_seconds,
        jitter_seconds=jitter_seconds,
        timeout=30,
    )
    if resp.status_code >= 400:
        raise MeetingMindError(f"Asana task create failed ({resp.status_code}): {resp.text}")
    try:
        gid = resp.json().get("data", {}).get("gid", "<unknown>")
    except json.JSONDecodeError:
        gid = "<unknown>"
    print(f"[OK] Asana task created: {gid}")
    return gid


def create_jira_issue(
    base_url: str,
    email: str,
    api_token: str,
    project_key: str,
    summary: str,
    description: str,
    issue_type: str = "Task",
    due_date_iso: Optional[str] = None,
    labels: Optional[Sequence[str]] = None,
    retries: int = 3,
    backoff_seconds: float = 1.25,
    jitter_seconds: float = 0.25,
) -> str:
    def _adf_from_text(text: str) -> Dict[str, Any]:
        lines = [ln.rstrip() for ln in (text or "").splitlines()]
        content: List[Dict[str, Any]] = []
        for ln in lines:
            if not ln.strip():
                continue
            content.append(
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": ln[:2000]}],
                }
            )
        if not content:
            content = [{"type": "paragraph", "content": [{"type": "text", "text": "Generated by MeetingMind"}]}]
        return {"type": "doc", "version": 1, "content": content}

    url = f"{base_url.rstrip('/')}/rest/api/3/issue"
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    payload = {
        "fields": {
            "project": {"key": project_key},
            "summary": summary[:254],
            # Jira Cloud v3 reliably accepts Atlassian Document Format (ADF).
            "description": _adf_from_text(description),
            "issuetype": {"name": issue_type},
        }
    }
    if due_date_iso:
        payload["fields"]["duedate"] = due_date_iso
    if labels:
        payload["fields"]["labels"] = [str(x) for x in labels if str(x).strip()][:10]
    resp = request_with_retries(
        "POST",
        url,
        headers=headers,
        auth=(email, api_token),
        json=payload,
        retries=retries,
        backoff_seconds=backoff_seconds,
        jitter_seconds=jitter_seconds,
        timeout=30,
    )
    if resp.status_code >= 400:
        raise MeetingMindError(f"Jira issue create failed ({resp.status_code}): {resp.text}")
    try:
        key = resp.json().get("key", "<unknown>")
    except json.JSONDecodeError:
        key = "<unknown>"
    print(f"[OK] Jira issue created: {key}")
    return key


def send_email_smtp(
    smtp_host: str,
    smtp_port: int,
    smtp_user: str,
    smtp_password: str,
    from_addr: str,
    to_addrs: Sequence[str],
    subject: str,
    body: str,
    smtp_security: str = "auto",
) -> bool:
    recipients = [x.strip() for x in to_addrs if x and x.strip()]
    if not recipients:
        raise MeetingMindError("No recipient email addresses provided")

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = ", ".join(recipients)

    security = (smtp_security or "auto").strip().lower()
    if security not in {"auto", "starttls", "ssl", "none"}:
        raise MeetingMindError(f"Unsupported smtp_security value: {smtp_security}")

    if security == "auto":
        security = "ssl" if smtp_port == 465 else "starttls"

    if security == "ssl":
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=30, context=context) as server:
            server.login(smtp_user, smtp_password)
            server.sendmail(from_addr, recipients, msg.as_string())
    else:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
            server.ehlo()
            if security == "starttls":
                server.starttls(context=ssl.create_default_context())
                server.ehlo()
            server.login(smtp_user, smtp_password)
            server.sendmail(from_addr, recipients, msg.as_string())
    print(f"[OK] Email sent to {', '.join(recipients)}")
    return True


def smtp_send_with_retries(
    *,
    smtp_host: str,
    smtp_port: int,
    smtp_user: str,
    smtp_password: str,
    from_addr: str,
    to_addrs: Sequence[str],
    subject: str,
    body: str,
    smtp_security: str = "auto",
    retries: int = 3,
    backoff_seconds: float = 1.25,
    jitter_seconds: float = 0.25,
) -> bool:
    last_error: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            return send_email_smtp(
                smtp_host=smtp_host,
                smtp_port=smtp_port,
                smtp_user=smtp_user,
                smtp_password=smtp_password,
                from_addr=from_addr,
                to_addrs=to_addrs,
                subject=subject,
                body=body,
                smtp_security=smtp_security,
            )
        except (smtplib.SMTPException, OSError) as exc:
            last_error = exc
            if attempt >= retries:
                break
            delay = backoff_seconds * (2**attempt) + random.uniform(0, max(jitter_seconds, 0))
            time.sleep(delay)
    raise MeetingMindError(f"SMTP send failed after {retries + 1} attempts: {last_error}")


def load_user_input(args: argparse.Namespace) -> str:
    if args.user_input:
        return args.user_input
    if args.transcript_file:
        with open(args.transcript_file, "r", encoding="utf-8") as f:
            return f.read()
    print("[ERROR] Provide --user-input or --transcript-file", file=sys.stderr)
    sys.exit(1)


def make_idempotency_key(channel: str, transcript_hash: str, payload: Dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hash_text(f"{channel}|{transcript_hash}|{canonical}")


def save_run_report(path: Path, report: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")


def main() -> None:
    configure_console_encoding()
    load_dotenv_file(Path(".env"))

    parser = argparse.ArgumentParser(description="Run MeetingMind via Airia endpoint")
    parser.add_argument("--pipeline-id", default=os.getenv("AIRIA_PIPELINE_ID"), help="Airia pipeline GUID")
    parser.add_argument("--api-key", default=os.getenv("AIRIA_API_KEY"), help="Airia API key")
    parser.add_argument("--webhook-url", default=os.getenv("AIRIA_WEBHOOK_URL"), help="Airia webhook URL")
    parser.add_argument("--no-webhook", action="store_true", help="Ignore webhook and force PipelineExecution mode")
    parser.add_argument("--user-input", help="Input text/transcript")
    parser.add_argument("--transcript-file", help="Path to transcript text file")
    parser.add_argument("--async-output", action="store_true", help="Set asyncOutput=true")
    parser.add_argument("--save-raw", default="airia_response.json", help="Where to save raw API output")
    parser.add_argument("--max-actions", type=int, default=100, help="Maximum action items to keep")
    parser.add_argument("--require-actions", action="store_true", help="Fail when no action items are extracted")
    parser.add_argument("--retries", type=int, default=3, help="Retries for network operations")
    parser.add_argument("--backoff-seconds", type=float, default=1.25, help="Base retry backoff delay")
    parser.add_argument("--jitter-seconds", type=float, default=0.25, help="Additional retry jitter")
    parser.add_argument("--dry-run", action="store_true", help="Do not write to external systems")
    parser.add_argument("--idempotency-file", default=".meetingmind_idempotency.json", help="Idempotency ledger file")
    parser.add_argument("--no-idempotency", action="store_true", help="Disable duplicate-write protection")
    parser.add_argument("--memory-file", default=".meetingmind_memory.json", help="Meeting memory store file")
    parser.add_argument("--disable-memory", action="store_true", help="Disable cross-meeting memory insights")
    parser.add_argument("--strict-integrations", action="store_true", help="Fail run if any selected integration fails")
    parser.add_argument("--mode", choices=["manual", "auto"], help="Force router mode by prefixing user input")
    parser.add_argument("--run-report", default="run_report.json", help="Structured per-run audit report output path")
    parser.add_argument(
        "--smtp-security",
        choices=["auto", "starttls", "ssl", "none"],
        default=os.getenv("SMTP_SECURITY", "auto"),
        help="SMTP security mode (auto/starttls/ssl/none)",
    )
    parser.add_argument(
        "--fanout-mode",
        choices=["parallel", "sequential"],
        default="parallel",
        help="Integration dispatch mode (parallel recommended for demo impact)",
    )

    parser.add_argument("--post-slack", action="store_true")
    parser.add_argument("--notify-slack-approval", action="store_true", help="Send pending approval card to Slack")
    parser.add_argument(
        "--approval-approve-url-template",
        default=os.getenv("APPROVAL_APPROVE_URL_TEMPLATE", ""),
        help="Optional URL template for Approve button; supports {execution_id}",
    )
    parser.add_argument(
        "--approval-deny-url-template",
        default=os.getenv("APPROVAL_DENY_URL_TEMPLATE", ""),
        help="Optional URL template for Deny button; supports {execution_id}",
    )
    parser.add_argument("--post-teams", action="store_true")
    parser.add_argument("--create-jira", action="store_true")
    parser.add_argument("--create-asana", action="store_true")
    parser.add_argument("--send-email", action="store_true")
    args = parser.parse_args()

    webhook_url = None if args.no_webhook else (args.webhook_url.strip() if args.webhook_url else None)

    if not webhook_url and (not args.pipeline_id or not args.api_key):
        print(
            "[ERROR] Provide --webhook-url or set AIRIA_WEBHOOK_URL, "
            "or provide --pipeline-id/--api-key (or env vars).",
            file=sys.stderr,
        )
        sys.exit(1)

    user_input = load_user_input(args)
    if args.mode == "manual" and "manual mode" not in user_input.lower():
        user_input = f"manual mode: review first. {user_input}"
    if args.mode == "auto" and "auto mode" not in user_input.lower():
        user_input = f"auto mode: bypass approval. {user_input}"

    run_started = datetime.utcnow().isoformat() + "Z"
    summary_text = ""
    actions: List[ActionItem] = []
    risk: Dict[str, Any] = {}
    memory_insights: List[str] = []
    response: Dict[str, Any] = {}
    integration_outcomes: List[IntegrationOutcome] = []

    def write_report(status: str, error: str = "", exit_code: int = 0) -> None:
        run_report = {
            "run_started_utc": run_started,
            "run_finished_utc": datetime.utcnow().isoformat() + "Z",
            "status": status,
            "exit_code": exit_code,
            "error": error,
            "mode": args.mode or "default",
            "input_hash": hash_text(user_input),
            "input_chars": len(user_input),
            "pipeline_id": args.pipeline_id,
            "used_webhook": bool(webhook_url),
            "response_execution_id": response.get("executionId"),
            "summary_preview": summary_text[:400],
            "action_item_count": len(actions),
            "risk_level": risk.get("risk_level"),
            "sentiment": risk.get("sentiment"),
            "memory_insights": memory_insights[:8],
            "integrations": [asdict(x) for x in integration_outcomes],
        }
        save_run_report(Path(args.run_report), run_report)
        print(f"[OK] Run report saved -> {args.run_report}")

    transcript_hash = hash_text(user_input)
    ledger = None if args.no_idempotency else DeliveryLedger(Path(args.idempotency_file))

    try:
        response = call_airia(
            args.pipeline_id,
            args.api_key,
            user_input,
            async_output=args.async_output,
            webhook_url=webhook_url,
            retries=args.retries,
            backoff_seconds=args.backoff_seconds,
            jitter_seconds=args.jitter_seconds,
        )
    except MeetingMindError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        write_report(status="error", error=str(exc), exit_code=1)
        sys.exit(1)

    with open(args.save_raw, "w", encoding="utf-8") as f:
        json.dump(response, f, indent=2, ensure_ascii=False)
    print(f"[OK] Saved raw Airia response -> {args.save_raw}")

    summary_text = best_text_from_airia_response(response)
    if is_pending_human_approval(response):
        execution_ref = response.get("executionId") or response.get("result")
        msg = f"Execution is awaiting human approval in Airia. Execution reference: {execution_ref}"
        if args.notify_slack_approval:
            try:
                approval_detail = post_slack_approval_request(
                    execution_id=str(execution_ref or ""),
                    summary_preview=summary_text or "No textual preview returned from Airia.",
                    slack_webhook_url=env("SLACK_WEBHOOK_URL"),
                    slack_bot_token=env("SLACK_BOT_TOKEN"),
                    slack_channel=env("SLACK_CHANNEL"),
                    approve_url_template=args.approval_approve_url_template,
                    deny_url_template=args.approval_deny_url_template,
                    retries=args.retries,
                    backoff_seconds=args.backoff_seconds,
                    jitter_seconds=args.jitter_seconds,
                )
                integration_outcomes.append(
                    IntegrationOutcome(channel="slack_approval", success=True, detail=approval_detail)
                )
            except MeetingMindError as exc:
                integration_outcomes.append(
                    IntegrationOutcome(channel="slack_approval", success=False, detail=str(exc))
                )
                if args.strict_integrations:
                    print(f"[ERROR] Slack approval notification failed in strict mode: {exc}", file=sys.stderr)
                    write_report(status="error", error=f"slack_approval: {exc}", exit_code=4)
                    sys.exit(4)
                print(f"[WARN] Slack approval notification failed: {exc}", file=sys.stderr)
        if args.require_actions:
            print(f"[ERROR] {msg}", file=sys.stderr)
            write_report(status="pending_approval", error=msg, exit_code=3)
            sys.exit(3)
        print(f"[INFO] {msg}")
        write_report(status="pending_approval", error=msg, exit_code=0)
        return

    if response.get("webhookAccepted") and not summary_text.strip():
        print(
            "[WARN] Webhook request was accepted but returned an empty body. "
            "Use PipelineExecution mode for synchronous output if you need parsed actions.",
            file=sys.stderr,
        )
    actions = extract_action_items(summary_text, max_items=max(1, args.max_actions))
    if args.require_actions and not actions:
        print("[ERROR] No action items extracted while --require-actions is enabled", file=sys.stderr)
        write_report(status="error", error="No action items extracted while --require-actions is enabled", exit_code=2)
        sys.exit(2)

    risk = analyze_sentiment_and_risk(summary_text, actions)
    memory_file = Path(args.memory_file)
    memory_store = {"runs": []}
    memory_insights: List[str] = ["Memory disabled for this run."]
    if not args.disable_memory:
        memory_store = load_memory_store(memory_file)
        memory_insights = build_memory_insights(actions, memory_store)

    print("\n=== AIRIA OUTPUT (best-effort text) ===")
    print(summary_text[:5000])
    print("\n=== ACTION ITEMS (parsed) ===")
    if actions:
        for idx, a in enumerate(actions, 1):
            print(f"{idx}. {a.task} | owner={a.owner} | deadline={a.deadline} | priority={a.priority}")
    else:
        print("No structured action items parsed.")
    print("\n=== SENTIMENT + RISK ===")
    print(format_risk_brief(risk))
    if risk.get("overdue_actions"):
        for item in risk["overdue_actions"][:10]:
            print(f"- OVERDUE: {item}")
    print("\n=== MEETING MEMORY INSIGHTS ===")
    for insight in memory_insights[:8]:
        print(f"- {insight}")

    if args.dry_run:
        print("[INFO] Dry-run mode enabled; skipping external writes.")
        if not args.disable_memory:
            updated = append_memory_run(memory_store, actions, risk)
            save_memory_store(memory_file, updated)
            print(f"[OK] Memory store updated -> {memory_file}")
        write_report(status="success_dry_run", exit_code=0)
        return

    integration_errors: List[str] = []

    def run_slack_integration() -> Tuple[List[IntegrationOutcome], List[str]]:
        outcomes: List[IntegrationOutcome] = []
        errors: List[str] = []
        slack_webhook = env("SLACK_WEBHOOK_URL")
        slack_bot_token = env("SLACK_BOT_TOKEN")
        slack_channel = env("SLACK_CHANNEL")
        if not slack_webhook and not slack_bot_token:
            msg = "Slack configuration missing: need SLACK_WEBHOOK_URL or SLACK_BOT_TOKEN+SLACK_CHANNEL"
            outcomes.append(IntegrationOutcome(channel="slack", success=False, detail=msg))
            errors.append(f"slack: {msg}")
            return outcomes, errors
        if slack_bot_token and not slack_webhook and not slack_channel:
            msg = "Slack configuration missing: SLACK_CHANNEL is required when using SLACK_BOT_TOKEN"
            outcomes.append(IntegrationOutcome(channel="slack", success=False, detail=msg))
            errors.append(f"slack: {msg}")
            return outcomes, errors

        payload = {
            "summary_text": summary_text[:4000],
            "actions": [a.__dict__ for a in actions],
            "risk": risk,
            "memory_insights": memory_insights,
        }
        key = make_idempotency_key("slack", transcript_hash, payload)
        if ledger and ledger.has(key):
            outcomes.append(IntegrationOutcome(channel="slack", success=True, detail="skipped_duplicate"))
            return outcomes, errors
        try:
            if slack_webhook:
                post_to_slack(
                    slack_webhook,
                    summary_text,
                    actions,
                    risk=risk,
                    memory_insights=memory_insights,
                    retries=args.retries,
                    backoff_seconds=args.backoff_seconds,
                    jitter_seconds=args.jitter_seconds,
                )
                detail = "posted_webhook"
            else:
                post_to_slack_via_api(
                    token=slack_bot_token,
                    channel=slack_channel,
                    summary_text=summary_text,
                    actions=actions,
                    risk=risk,
                    memory_insights=memory_insights,
                    retries=args.retries,
                    backoff_seconds=args.backoff_seconds,
                    jitter_seconds=args.jitter_seconds,
                )
                detail = f"posted_api:{slack_channel}"
            if ledger:
                ledger.mark(key, {"channel": "slack", "timestamp": int(time.time())})
            outcomes.append(IntegrationOutcome(channel="slack", success=True, detail=detail))
        except MeetingMindError as exc:
            outcomes.append(IntegrationOutcome(channel="slack", success=False, detail=str(exc)))
            errors.append(f"slack: {exc}")
        return outcomes, errors

    def run_teams_integration() -> Tuple[List[IntegrationOutcome], List[str]]:
        outcomes: List[IntegrationOutcome] = []
        errors: List[str] = []
        teams_webhook = env("TEAMS_WEBHOOK_URL")
        if not teams_webhook:
            msg = "Teams configuration missing: need TEAMS_WEBHOOK_URL"
            outcomes.append(IntegrationOutcome(channel="teams", success=False, detail=msg))
            errors.append(f"teams: {msg}")
            return outcomes, errors

        payload = {
            "summary_text": summary_text[:4000],
            "actions": [a.__dict__ for a in actions],
            "risk": risk,
            "memory_insights": memory_insights,
        }
        key = make_idempotency_key("teams", transcript_hash, payload)
        if ledger and ledger.has(key):
            outcomes.append(IntegrationOutcome(channel="teams", success=True, detail="skipped_duplicate"))
            return outcomes, errors
        try:
            post_to_teams(
                webhook_url=teams_webhook,
                summary_text=summary_text,
                actions=actions,
                risk=risk,
                memory_insights=memory_insights,
                retries=args.retries,
                backoff_seconds=args.backoff_seconds,
                jitter_seconds=args.jitter_seconds,
            )
            if ledger:
                ledger.mark(key, {"channel": "teams", "timestamp": int(time.time())})
            outcomes.append(IntegrationOutcome(channel="teams", success=True, detail="posted_webhook"))
        except MeetingMindError as exc:
            outcomes.append(IntegrationOutcome(channel="teams", success=False, detail=str(exc)))
            errors.append(f"teams: {exc}")
        return outcomes, errors

    def run_jira_integration() -> Tuple[List[IntegrationOutcome], List[str]]:
        outcomes: List[IntegrationOutcome] = []
        errors: List[str] = []
        jira_url = env("JIRA_BASE_URL")
        jira_email = env("JIRA_EMAIL")
        jira_token = env("JIRA_API_TOKEN")
        jira_project = env("JIRA_PROJECT_KEY")
        missing = [
            name
            for name, value in (
                ("JIRA_BASE_URL", jira_url),
                ("JIRA_EMAIL", jira_email),
                ("JIRA_API_TOKEN", jira_token),
                ("JIRA_PROJECT_KEY", jira_project),
            )
            if not value
        ]
        if missing:
            msg = f"Jira configuration missing: {', '.join(missing)}"
            outcomes.append(IntegrationOutcome(channel="jira", success=False, detail=msg))
            errors.append(f"jira: {msg}")
            return outcomes, errors

        for a in actions or [ActionItem(task="Meeting follow-up task (unparsed)")]:
            jira_payload = {
                "project_key": jira_project,
                "summary": a.task,
                "owner": a.owner,
                "deadline": a.deadline,
            }
            deadline_date = parse_deadline_to_date(a.deadline)
            key = make_idempotency_key("jira", transcript_hash, jira_payload)
            if ledger and ledger.has(key):
                outcomes.append(IntegrationOutcome(channel="jira", success=True, detail=f"skipped_duplicate:{a.task}"))
                continue
            try:
                issue_key = create_jira_issue(
                    base_url=jira_url,
                    email=jira_email,
                    api_token=jira_token,
                    project_key=jira_project,
                    summary=a.task,
                    description=(
                        f"Owner: {a.owner}\n"
                        f"Deadline: {a.deadline}\n"
                        f"Priority: {a.priority}\n"
                        f"Risk Brief: {format_risk_brief(risk)}\n\n"
                        "Generated by MeetingMind."
                    ),
                    due_date_iso=(deadline_date.isoformat() if deadline_date else None),
                    labels=["meetingmind", f"risk-{risk.get('risk_level', 'unknown')}", f"priority-{a.priority}"],
                    retries=args.retries,
                    backoff_seconds=args.backoff_seconds,
                    jitter_seconds=args.jitter_seconds,
                )
                if ledger:
                    ledger.mark(key, {"channel": "jira", "issue_key": issue_key, "timestamp": int(time.time())})
                outcomes.append(IntegrationOutcome(channel="jira", success=True, detail=f"created:{a.task}", artifact=issue_key))
            except MeetingMindError as exc:
                outcomes.append(IntegrationOutcome(channel="jira", success=False, detail=f"{a.task}: {exc}"))
                errors.append(f"jira: {exc}")
        return outcomes, errors

    def run_asana_integration() -> Tuple[List[IntegrationOutcome], List[str]]:
        outcomes: List[IntegrationOutcome] = []
        errors: List[str] = []
        asana_pat = env("ASANA_PAT")
        asana_project_gid = env("ASANA_PROJECT_GID")
        missing = [
            name
            for name, value in (
                ("ASANA_PAT", asana_pat),
                ("ASANA_PROJECT_GID", asana_project_gid),
            )
            if not value
        ]
        if missing:
            msg = f"Asana configuration missing: {', '.join(missing)}"
            outcomes.append(IntegrationOutcome(channel="asana", success=False, detail=msg))
            errors.append(f"asana: {msg}")
            return outcomes, errors

        for a in actions or [ActionItem(task="Meeting follow-up task (unparsed)")]:
            deadline_date = parse_deadline_to_date(a.deadline)
            asana_payload = {
                "project_gid": asana_project_gid,
                "task": a.task,
                "owner": a.owner,
                "deadline": deadline_date.isoformat() if deadline_date else "",
            }
            key = make_idempotency_key("asana", transcript_hash, asana_payload)
            if ledger and ledger.has(key):
                outcomes.append(IntegrationOutcome(channel="asana", success=True, detail=f"skipped_duplicate:{a.task}"))
                continue
            try:
                gid = create_asana_task(
                    personal_access_token=asana_pat,
                    project_gid=asana_project_gid,
                    name=a.task,
                    notes=(
                        f"Owner: {a.owner}\n"
                        f"Deadline: {a.deadline}\n"
                        f"Priority: {a.priority}\n"
                        f"Risk Brief: {format_risk_brief(risk)}\n\n"
                        "Generated by MeetingMind."
                    ),
                    due_on_iso=(deadline_date.isoformat() if deadline_date else None),
                    retries=args.retries,
                    backoff_seconds=args.backoff_seconds,
                    jitter_seconds=args.jitter_seconds,
                )
                if ledger:
                    ledger.mark(key, {"channel": "asana", "task_gid": gid, "timestamp": int(time.time())})
                outcomes.append(IntegrationOutcome(channel="asana", success=True, detail=f"created:{a.task}", artifact=gid))
            except MeetingMindError as exc:
                outcomes.append(IntegrationOutcome(channel="asana", success=False, detail=f"{a.task}: {exc}"))
                errors.append(f"asana: {exc}")
        return outcomes, errors

    def run_email_integration() -> Tuple[List[IntegrationOutcome], List[str]]:
        outcomes: List[IntegrationOutcome] = []
        errors: List[str] = []
        smtp_host = env("SMTP_HOST")
        smtp_port = int(env("SMTP_PORT", default="587"))
        smtp_user = env("SMTP_USER")
        smtp_password = env("SMTP_PASSWORD")
        from_addr = env("EMAIL_FROM")
        to_addr = env("EMAIL_TO")
        missing = [
            name
            for name, value in (
                ("SMTP_HOST", smtp_host),
                ("SMTP_USER", smtp_user),
                ("SMTP_PASSWORD", smtp_password),
                ("EMAIL_FROM", from_addr),
                ("EMAIL_TO", to_addr),
            )
            if not value
        ]
        if missing:
            msg = f"Email configuration missing: {', '.join(missing)}"
            outcomes.append(IntegrationOutcome(channel="email", success=False, detail=msg))
            errors.append(f"email: {msg}")
            return outcomes, errors

        to_addrs = parse_recipients(to_addr)
        subject = f"[MeetingMind][{str(risk.get('risk_level', 'unknown')).upper()}] Meeting Summary & Action Items"
        body = (
            f"{summary_text}\n\n"
            f"Risk Brief: {format_risk_brief(risk)}\n\n"
            "Action items:\n"
            + "\n".join([f"- {a.task} (Owner: {a.owner}, Deadline: {a.deadline}, Priority: {a.priority})" for a in actions])
            + "\n\nMeeting memory insights:\n"
            + "\n".join([f"- {x}" for x in memory_insights])
        )
        email_payload = {"to_addr": to_addr, "subject": subject, "body_hash": hash_text(body)}
        key = make_idempotency_key("email", transcript_hash, email_payload)
        if ledger and ledger.has(key):
            outcomes.append(IntegrationOutcome(channel="email", success=True, detail="skipped_duplicate"))
            return outcomes, errors

        try:
            smtp_send_with_retries(
                smtp_host=smtp_host,
                smtp_port=smtp_port,
                smtp_user=smtp_user,
                smtp_password=smtp_password,
                from_addr=from_addr,
                to_addrs=to_addrs,
                subject=subject,
                body=body,
                smtp_security=args.smtp_security,
                retries=args.retries,
                backoff_seconds=args.backoff_seconds,
                jitter_seconds=args.jitter_seconds,
            )
            if ledger:
                ledger.mark(key, {"channel": "email", "to": to_addrs, "timestamp": int(time.time())})
            outcomes.append(IntegrationOutcome(channel="email", success=True, detail="sent", artifact=",".join(to_addrs)))
        except MeetingMindError as exc:
            outcomes.append(IntegrationOutcome(channel="email", success=False, detail=str(exc)))
            errors.append(f"email: {exc}")
        return outcomes, errors

    integration_jobs: List[Tuple[str, Any]] = []
    if args.post_slack:
        integration_jobs.append(("slack", run_slack_integration))
    if args.post_teams:
        integration_jobs.append(("teams", run_teams_integration))
    if args.create_jira:
        integration_jobs.append(("jira", run_jira_integration))
    if args.create_asana:
        integration_jobs.append(("asana", run_asana_integration))
    if args.send_email:
        integration_jobs.append(("email", run_email_integration))

    integration_results: Dict[str, Tuple[List[IntegrationOutcome], List[str]]] = {}
    if integration_jobs:
        if args.fanout_mode == "parallel" and len(integration_jobs) > 1:
            print(f"[INFO] Dispatching {len(integration_jobs)} integrations in parallel fanout mode")
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(5, len(integration_jobs))) as executor:
                futures = {
                    executor.submit(job_fn): channel
                    for channel, job_fn in integration_jobs
                }
                for future in concurrent.futures.as_completed(futures):
                    channel = futures[future]
                    try:
                        integration_results[channel] = future.result()
                    except Exception as exc:  # pragma: no cover - safety net
                        msg = f"{channel}: unexpected_error: {exc}"
                        integration_results[channel] = (
                            [IntegrationOutcome(channel=channel, success=False, detail=f"unexpected_error: {exc}")],
                            [msg],
                        )
        else:
            for channel, job_fn in integration_jobs:
                try:
                    integration_results[channel] = job_fn()
                except Exception as exc:  # pragma: no cover - safety net
                    msg = f"{channel}: unexpected_error: {exc}"
                    integration_results[channel] = (
                        [IntegrationOutcome(channel=channel, success=False, detail=f"unexpected_error: {exc}")],
                        [msg],
                    )

        for channel, _ in integration_jobs:
            outcomes, errors = integration_results.get(channel, ([], []))
            integration_outcomes.extend(outcomes)
            integration_errors.extend(errors)

    for err in integration_errors:
        print(f"[WARN] {err}", file=sys.stderr)

    if not args.disable_memory:
        updated = append_memory_run(memory_store, actions, risk)
        save_memory_store(memory_file, updated)
        print(f"[OK] Memory store updated -> {memory_file}")

    if args.strict_integrations and integration_errors:
        print("[ERROR] One or more integrations failed in strict mode:", file=sys.stderr)
        for item in integration_errors:
            print(f"  - {item}", file=sys.stderr)
        write_report(status="error", error="; ".join(integration_errors), exit_code=4)
        sys.exit(4)

    write_report(status="success", exit_code=0)


if __name__ == "__main__":
    main()
