#!/usr/bin/env python3
from __future__ import annotations

import argparse
import collections
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

import requests

from meetingmind_runner import (
    MeetingMindError,
    parse_deadline_to_date,
    parse_recipients,
    smtp_send_with_retries,
)


def parse_timestamp(value: str) -> datetime | None:
    if not value:
        return None
    cleaned = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(cleaned)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def load_memory(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    runs = payload.get("runs", [])
    return [x for x in runs if isinstance(x, dict)]


def render_markdown(report: Dict[str, Any]) -> str:
    metrics = report["metrics"]
    recurring = report["recurring_actions"]
    at_risk = report["owners_at_risk"]
    trend = report["daily_trend"]

    lines: List[str] = []
    lines.append(f"# MeetingMind Weekly Intelligence ({report['window']['start_date']} to {report['window']['end_date']})")
    lines.append("")
    lines.append("## KPI Snapshot")
    lines.append(f"- Meetings processed: **{metrics['meetings_processed']}**")
    lines.append(f"- Total actions: **{metrics['total_actions']}**")
    lines.append(f"- Avg actions/meeting: **{metrics['avg_actions_per_meeting']}**")
    lines.append(f"- High-risk meetings: **{metrics['high_risk_meetings']}**")
    lines.append(f"- Negative-sentiment meetings: **{metrics['negative_sentiment_meetings']}**")
    lines.append("")

    lines.append("## Top Owners by Assigned Actions")
    for owner, count in metrics["top_owners"][:8]:
        lines.append(f"- {owner}: {count}")
    if not metrics["top_owners"]:
        lines.append("- No owner assignments found.")
    lines.append("")

    lines.append("## Recurring Action Signals")
    if recurring:
        for item in recurring[:10]:
            lines.append(f"- {item['task']} | owner={item['owner']} | repeats={item['count']}")
    else:
        lines.append("- No recurring actions in selected window.")
    lines.append("")

    lines.append("## At-Risk Owners")
    if at_risk:
        for owner, count in at_risk[:10]:
            lines.append(f"- {owner}: {count} overdue item(s)")
    else:
        lines.append("- No overdue owner signals detected.")
    lines.append("")

    lines.append("## Daily Trend")
    lines.append("| Date | Meetings | Actions | High Risk |")
    lines.append("|---|---:|---:|---:|")
    for row in trend:
        lines.append(
            f"| {row['date']} | {row['meetings']} | {row['actions']} | {row['high_risk']} |"
        )
    return "\n".join(lines)


def maybe_render_pdf(path: Path, markdown_text: str) -> Tuple[bool, str]:
    try:
        from reportlab.lib.pagesizes import letter  # type: ignore
        from reportlab.pdfgen import canvas  # type: ignore
    except Exception:
        return False, "reportlab not installed"

    c = canvas.Canvas(str(path), pagesize=letter)
    width, height = letter
    y = height - 40
    c.setFont("Helvetica", 10)
    for line in markdown_text.splitlines():
        if y < 40:
            c.showPage()
            c.setFont("Helvetica", 10)
            y = height - 40
        c.drawString(35, y, line[:150])
        y -= 14
    c.save()
    return True, "ok"


def post_summary_to_slack(webhook_url: str, report: Dict[str, Any], md_path: Path) -> None:
    metrics = report["metrics"]
    recurring = report["recurring_actions"][:3]
    recurring_text = "\n".join(
        [f"- {x['task']} (owner={x['owner']}, repeats={x['count']})" for x in recurring]
    ) or "- None"
    payload = {
        "text": "MeetingMind weekly intelligence report",
        "blocks": [
            {"type": "header", "text": {"type": "plain_text", "text": "MeetingMind Weekly Intelligence"}},
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*Window:* {report['window']['start_date']} to {report['window']['end_date']}\n"
                        f"*Meetings:* {metrics['meetings_processed']} | *Actions:* {metrics['total_actions']} | "
                        f"*High risk:* {metrics['high_risk_meetings']}"
                    ),
                },
            },
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*Recurring Signals*\n{recurring_text}"}},
            {"type": "context", "elements": [{"type": "mrkdwn", "text": f"Markdown report: `{md_path}`"}]},
        ],
    }
    resp = requests.post(webhook_url, json=payload, timeout=30)
    if resp.status_code >= 400:
        raise MeetingMindError(f"Slack weekly report failed ({resp.status_code}): {resp.text}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate weekly cross-meeting intelligence report")
    parser.add_argument("--memory-file", default=".meetingmind_memory.json")
    parser.add_argument("--lookback-days", type=int, default=7)
    parser.add_argument("--output-json", default="weekly_intelligence.json")
    parser.add_argument("--output-md", default="weekly_intelligence.md")
    parser.add_argument("--output-pdf", default="weekly_intelligence.pdf")
    parser.add_argument("--force", action="store_true", help="Run on any weekday (default: Friday only)")
    parser.add_argument("--send-slack", action="store_true")
    parser.add_argument("--send-email", action="store_true")
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    if not args.force and now.weekday() != 4:
        print("[INFO] Not Friday and --force not set; skipping weekly report generation.")
        return

    memory_runs = load_memory(Path(args.memory_file))
    window_start = now - timedelta(days=max(1, args.lookback_days))
    filtered_runs = []
    for run in memory_runs:
        dt = parse_timestamp(str(run.get("timestamp", "")))
        if dt and dt >= window_start:
            filtered_runs.append((dt, run))

    action_counter: collections.Counter[Tuple[str, str]] = collections.Counter()
    owner_counter: collections.Counter[str] = collections.Counter()
    overdue_owner_counter: collections.Counter[str] = collections.Counter()
    daily: Dict[str, Dict[str, int]] = {}
    high_risk = 0
    negative_sentiment = 0
    total_actions = 0

    for dt, run in filtered_runs:
        day_key = dt.date().isoformat()
        daily.setdefault(day_key, {"meetings": 0, "actions": 0, "high_risk": 0})
        daily[day_key]["meetings"] += 1
        if str(run.get("risk_level", "")).lower() == "high":
            high_risk += 1
            daily[day_key]["high_risk"] += 1
        if str(run.get("sentiment", "")).lower() == "negative":
            negative_sentiment += 1

        for item in run.get("actions", []):
            if not isinstance(item, dict):
                continue
            task = str(item.get("task", "")).strip()
            owner = str(item.get("owner", "Unassigned")).strip() or "Unassigned"
            deadline = str(item.get("deadline", "TBD")).strip()
            if not task:
                continue
            action_counter[(task, owner)] += 1
            owner_counter[owner] += 1
            total_actions += 1
            daily[day_key]["actions"] += 1
            due = parse_deadline_to_date(deadline)
            if due and due < now.date():
                overdue_owner_counter[owner] += 1

    recurring_actions = [
        {"task": task, "owner": owner, "count": count}
        for (task, owner), count in action_counter.items()
        if count > 1
    ]
    recurring_actions.sort(key=lambda x: x["count"], reverse=True)

    daily_rows = []
    for day in sorted(daily.keys()):
        row = {"date": day}
        row.update(daily[day])
        daily_rows.append(row)

    meetings_processed = len(filtered_runs)
    report: Dict[str, Any] = {
        "generated_at_utc": now.isoformat(),
        "window": {
            "days": args.lookback_days,
            "start_date": window_start.date().isoformat(),
            "end_date": now.date().isoformat(),
        },
        "metrics": {
            "meetings_processed": meetings_processed,
            "total_actions": total_actions,
            "avg_actions_per_meeting": round(total_actions / meetings_processed, 2) if meetings_processed else 0.0,
            "high_risk_meetings": high_risk,
            "negative_sentiment_meetings": negative_sentiment,
            "top_owners": owner_counter.most_common(10),
        },
        "recurring_actions": recurring_actions,
        "owners_at_risk": overdue_owner_counter.most_common(10),
        "daily_trend": daily_rows,
    }

    output_json = Path(args.output_json)
    output_md = Path(args.output_md)
    output_pdf = Path(args.output_pdf)
    output_json.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    markdown_text = render_markdown(report)
    output_md.write_text(markdown_text, encoding="utf-8")
    pdf_ok, pdf_detail = maybe_render_pdf(output_pdf, markdown_text)

    print(f"[OK] Weekly JSON report saved -> {output_json}")
    print(f"[OK] Weekly markdown report saved -> {output_md}")
    if pdf_ok:
        print(f"[OK] Weekly PDF report saved -> {output_pdf}")
    else:
        print(f"[WARN] Weekly PDF not generated: {pdf_detail}")

    if args.send_slack:
        webhook = os.getenv("SLACK_WEBHOOK_URL", "").strip()
        if not webhook:
            raise MeetingMindError("Missing SLACK_WEBHOOK_URL for --send-slack")
        post_summary_to_slack(webhook, report, output_md)
        print("[OK] Weekly intelligence summary posted to Slack")

    if args.send_email:
        smtp_host = os.getenv("SMTP_HOST", "").strip()
        smtp_port = int(os.getenv("SMTP_PORT", "587"))
        smtp_user = os.getenv("SMTP_USER", "").strip()
        smtp_password = os.getenv("SMTP_PASSWORD", "").strip()
        email_from = os.getenv("EMAIL_FROM", "").strip()
        email_to = os.getenv("EMAIL_TO", "").strip()
        if not all([smtp_host, smtp_user, smtp_password, email_from, email_to]):
            raise MeetingMindError("Missing SMTP_* / EMAIL_* env vars for --send-email")
        subject = f"[MeetingMind Weekly] {report['window']['start_date']} -> {report['window']['end_date']}"
        body = (
            markdown_text
            + "\n\nArtifacts:\n"
            + f"- {output_json}\n- {output_md}\n"
            + (f"- {output_pdf}\n" if pdf_ok else "")
        )
        smtp_send_with_retries(
            smtp_host=smtp_host,
            smtp_port=smtp_port,
            smtp_user=smtp_user,
            smtp_password=smtp_password,
            from_addr=email_from,
            to_addrs=parse_recipients(email_to),
            subject=subject,
            body=body,
            smtp_security=os.getenv("SMTP_SECURITY", "auto"),
        )
        print("[OK] Weekly intelligence summary sent via email")


if __name__ == "__main__":
    main()
