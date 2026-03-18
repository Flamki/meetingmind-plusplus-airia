#!/usr/bin/env python3
"""
Render a demo-ready Markdown report from MeetingMind batch JSON output.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List


def esc(text: str) -> str:
    return (text or "").replace("|", "\\|").strip()


def load_report(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def top_runs_by_actions(runs: List[Dict[str, Any]], limit: int = 5) -> List[Dict[str, Any]]:
    ok_runs = [r for r in runs if r.get("status") == "ok"]
    return sorted(ok_runs, key=lambda r: r.get("action_item_count", 0), reverse=True)[:limit]


def render_markdown(report: Dict[str, Any], title: str) -> str:
    meta = report.get("meta", {})
    kpis = report.get("kpis", {})
    runs = report.get("runs", [])

    success = int(kpis.get("successful_runs", 0))
    failed = int(kpis.get("failed_runs", 0))
    processed = int(meta.get("files_processed", 0))
    success_rate = (success / processed * 100.0) if processed else 0.0

    lines: List[str] = []
    lines.append(f"# {title}")
    lines.append("")
    lines.append("## Executive Summary")
    lines.append(
        f"- Processed **{processed}** transcript(s) with **{success}** success and **{failed}** failure."
    )
    lines.append(
        f"- Success rate: **{success_rate:.1f}%** | Total action items: **{kpis.get('total_action_items', 0)}**."
    )
    lines.append(
        f"- Avg action items per successful run: **{kpis.get('avg_action_items_per_success', 0)}**."
    )
    lines.append(
        f"- Avg runtime per file: **{meta.get('avg_runtime_seconds', 0)}s** | Total runtime: **{meta.get('total_duration_seconds', 0)}s**."
    )
    if meta.get("mode"):
        lines.append(f"- Execution mode: **{meta.get('mode')}**.")
    if "high_risk_runs" in kpis:
        lines.append(
            f"- Risk profile: **{kpis.get('high_risk_runs', 0)} high-risk**, **{kpis.get('medium_risk_runs', 0)} medium-risk** run(s)."
        )
    if "negative_sentiment_runs" in kpis:
        lines.append(f"- Negative sentiment meetings detected: **{kpis.get('negative_sentiment_runs', 0)}**.")
    lines.append("")

    lines.append("## Run Metadata")
    lines.append("| Field | Value |")
    lines.append("|---|---|")
    lines.append(f"| Generated (UTC) | {esc(str(meta.get('generated_at_utc', '')))} |")
    lines.append(f"| Pipeline ID | `{esc(str(meta.get('pipeline_id', '')) )}` |")
    lines.append(f"| Input Directory | `{esc(str(meta.get('input_dir', '')) )}` |")
    lines.append(f"| Pattern | `{esc(str(meta.get('pattern', '')) )}` |")
    lines.append(f"| Recursive | `{esc(str(meta.get('recursive', '')) )}` |")
    lines.append("")

    lines.append("## Top Owners")
    owners = kpis.get("top_owners", [])
    if owners:
        lines.append("| Owner | Task Count |")
        lines.append("|---|---|")
        for o in owners[:10]:
            lines.append(f"| {esc(str(o.get('owner', 'Unassigned')))} | {o.get('count', 0)} |")
    else:
        lines.append("No owner distribution available.")
    lines.append("")

    lines.append("## Highest-Action Meetings")
    top_runs = top_runs_by_actions(runs, limit=5)
    if top_runs:
        lines.append("| File | Action Items | Duration (s) | Preview |")
        lines.append("|---|---:|---:|---|")
        for r in top_runs:
            preview = esc(str(r.get("summary_preview", ""))).replace("\n", " ")
            lines.append(
                f"| `{esc(str(r.get('file', '')) )}` | {r.get('action_item_count', 0)} | {r.get('duration_seconds', 0)} | {preview[:140]} |"
            )
    else:
        lines.append("No successful runs found.")
    lines.append("")

    lines.append("## Risk Signals")
    risk_runs = [r for r in runs if r.get("status") == "ok" and (r.get("risk_level") or r.get("risk_signals"))]
    if risk_runs:
        lines.append("| File | Risk Level | Sentiment | Risk Signals |")
        lines.append("|---|---|---|---|")
        for r in risk_runs[:10]:
            signals = ", ".join(str(x) for x in (r.get("risk_signals") or [])[:3]) or "-"
            lines.append(
                f"| `{esc(str(r.get('file', '')) )}` | {esc(str(r.get('risk_level', '-')))} | {esc(str(r.get('sentiment', '-')))} | {esc(signals)} |"
            )
    else:
        lines.append("No risk metadata available.")
    lines.append("")

    lines.append("## Per-File Results")
    lines.append("| File | Status | Action Items | Duration (s) |")
    lines.append("|---|---|---:|---:|")
    for r in runs:
        lines.append(
            f"| `{esc(str(r.get('file', '')) )}` | {esc(str(r.get('status', 'unknown')))} | {r.get('action_item_count', 0)} | {r.get('duration_seconds', 0)} |"
        )
    lines.append("")

    lines.append("## Notes For Devpost")
    lines.append("- This report was generated automatically from real pipeline executions.")
    lines.append("- Demo recommendation: show both `manual` and `auto` mode runs in one flow.")
    lines.append("- Include your Airia Community agent URL in the submission.")
    lines.append("")

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Render demo_report.md from demo_report.json")
    parser.add_argument("--input-json", default="demo_report.json", help="Path to batch JSON report")
    parser.add_argument("--output-md", default="demo_report.md", help="Path to markdown output")
    parser.add_argument("--title", default="MeetingMind Demo Report", help="Markdown report title")
    args = parser.parse_args()

    report = load_report(Path(args.input_json))
    markdown = render_markdown(report, title=args.title)
    out = Path(args.output_md)
    out.write_text(markdown, encoding="utf-8")
    print(f"[DONE] Markdown report saved -> {out.resolve()}")


if __name__ == "__main__":
    main()
