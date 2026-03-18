#!/usr/bin/env python3
"""
MeetingMind Batch Runner

Batch-process transcript files through an Airia pipeline and generate
a demo-ready JSON report with metrics and parsed action items.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from meetingmind_runner import (
    ActionItem,
    MeetingMindError,
    analyze_sentiment_and_risk,
    best_text_from_airia_response,
    call_airia,
    extract_action_items,
    load_dotenv_file,
)


def to_action_dict(items: List[ActionItem]) -> List[Dict[str, str]]:
    return [{"task": i.task, "owner": i.owner, "deadline": i.deadline, "priority": i.priority} for i in items]


def load_transcript(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def gather_files(input_dir: Path, pattern: str, recursive: bool) -> List[Path]:
    if recursive:
        files = [p for p in input_dir.rglob(pattern) if p.is_file()]
    else:
        files = [p for p in input_dir.glob(pattern) if p.is_file()]
    return sorted(files)


def main() -> None:
    load_dotenv_file(Path(".env"))

    parser = argparse.ArgumentParser(description="Batch-run MeetingMind via Airia endpoint")
    parser.add_argument("--pipeline-id", default=os.getenv("AIRIA_PIPELINE_ID"), help="Airia pipeline GUID")
    parser.add_argument("--api-key", default=os.getenv("AIRIA_API_KEY"), help="Airia API key")
    parser.add_argument("--webhook-url", default=os.getenv("AIRIA_WEBHOOK_URL"), help="Airia webhook URL")
    parser.add_argument("--no-webhook", action="store_true", help="Ignore webhook and force PipelineExecution mode")
    parser.add_argument("--input-dir", required=True, help="Folder containing transcript files")
    parser.add_argument("--pattern", default="*.txt", help="File glob pattern (default: *.txt)")
    parser.add_argument("--recursive", action="store_true", help="Recursively scan subfolders")
    parser.add_argument("--limit", type=int, default=0, help="Process only first N files (0 = all)")
    parser.add_argument("--async-output", action="store_true", help="Set asyncOutput=true")
    parser.add_argument("--mode", choices=["manual", "auto"], help="Force mode via input prefix")
    parser.add_argument("--output-json", default="demo_report.json", help="Output report path")
    parser.add_argument("--save-raw-dir", default="", help="Optional folder to save raw API responses per file")
    parser.add_argument("--strict", action="store_true", help="Exit non-zero if any file fails")
    parser.add_argument("--retries", type=int, default=3, help="Retries for each Airia API call")
    parser.add_argument("--backoff-seconds", type=float, default=1.25, help="Base retry backoff delay")
    parser.add_argument("--jitter-seconds", type=float, default=0.25, help="Additional retry jitter")
    parser.add_argument("--max-actions", type=int, default=100, help="Maximum parsed action items per run")
    parser.add_argument("--require-actions", action="store_true", help="Fail each run that has 0 action items")
    parser.add_argument(
        "--max-input-chars",
        type=int,
        default=300000,
        help="Reject transcripts larger than this character limit",
    )
    args = parser.parse_args()

    webhook_url = None if args.no_webhook else (args.webhook_url.strip() if args.webhook_url else None)

    if not webhook_url and (not args.pipeline_id or not args.api_key):
        print(
            "[ERROR] Provide --webhook-url (or AIRIA_WEBHOOK_URL), "
            "or provide --pipeline-id/--api-key (or env vars).",
            file=sys.stderr,
        )
        sys.exit(1)

    input_dir = Path(args.input_dir)
    if not input_dir.exists() or not input_dir.is_dir():
        print(f"[ERROR] Invalid --input-dir: {input_dir}", file=sys.stderr)
        sys.exit(1)

    files = gather_files(input_dir, args.pattern, args.recursive)
    if args.limit > 0:
        files = files[: args.limit]
    if not files:
        print("[ERROR] No files found for the given input-dir/pattern", file=sys.stderr)
        sys.exit(1)

    raw_dir = Path(args.save_raw_dir) if args.save_raw_dir else None
    if raw_dir:
        raw_dir.mkdir(parents=True, exist_ok=True)

    started = time.time()
    report_items: List[Dict[str, Any]] = []
    owner_counter: Counter = Counter()
    total_actions = 0
    ok_count = 0
    fail_count = 0
    runtimes: List[float] = []

    print(f"[INFO] Processing {len(files)} transcript file(s)...")
    for idx, path in enumerate(files, 1):
        item_start = time.time()
        rel = str(path.relative_to(input_dir))
        try:
            transcript = load_transcript(path)
            if args.mode == "manual" and "manual mode" not in transcript.lower():
                transcript = f"manual mode: review first. {transcript}"
            if args.mode == "auto" and "auto mode" not in transcript.lower():
                transcript = f"auto mode: bypass approval. {transcript}"
            if len(transcript) > args.max_input_chars:
                raise MeetingMindError(
                    f"Input exceeds --max-input-chars ({len(transcript)} > {args.max_input_chars})"
                )
            response = call_airia(
                pipeline_id=args.pipeline_id,
                api_key=args.api_key,
                user_input=transcript,
                async_output=args.async_output,
                webhook_url=webhook_url,
                retries=args.retries,
                backoff_seconds=args.backoff_seconds,
                jitter_seconds=args.jitter_seconds,
            )

            if raw_dir:
                raw_path = raw_dir / f"{path.stem}.airia.json"
                raw_path.write_text(json.dumps(response, indent=2, ensure_ascii=False), encoding="utf-8")

            summary_text = best_text_from_airia_response(response)
            actions = extract_action_items(summary_text, max_items=max(1, args.max_actions))
            risk = analyze_sentiment_and_risk(summary_text, actions)
            if args.require_actions and not actions:
                raise MeetingMindError("No action items extracted while --require-actions is enabled")
            action_dicts = to_action_dict(actions)
            for a in actions:
                owner_counter[a.owner or "Unassigned"] += 1
            total_actions += len(actions)

            duration = round(time.time() - item_start, 3)
            runtimes.append(duration)
            ok_count += 1

            report_items.append(
                {
                    "file": rel,
                    "file_hash": hash_text(transcript),
                    "status": "ok",
                    "duration_seconds": duration,
                    "input_chars": len(transcript),
                    "response_hash": hash_text(json.dumps(response, sort_keys=True, ensure_ascii=False)),
                    "summary_text": summary_text,
                    "summary_preview": summary_text[:280],
                    "action_items": action_dicts,
                    "action_item_count": len(action_dicts),
                    "risk_level": risk.get("risk_level", "unknown"),
                    "sentiment": risk.get("sentiment", "neutral"),
                    "risk_signals": risk.get("risk_signals", []),
                }
            )
            print(f"[OK] {idx}/{len(files)} {rel} -> {len(action_dicts)} action items")
        except Exception as e:  # noqa: BLE001
            duration = round(time.time() - item_start, 3)
            runtimes.append(duration)
            fail_count += 1
            report_items.append(
                {
                    "file": rel,
                    "status": "error",
                    "duration_seconds": duration,
                    "error_type": type(e).__name__,
                    "error": str(e),
                }
            )
            print(f"[WARN] {idx}/{len(files)} {rel} failed: {e}", file=sys.stderr)

    total_duration = round(time.time() - started, 3)
    avg_runtime = round(sum(runtimes) / len(runtimes), 3) if runtimes else 0.0

    report = {
        "meta": {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "pipeline_id": args.pipeline_id,
            "input_dir": str(input_dir.resolve()),
            "pattern": args.pattern,
            "recursive": args.recursive,
            "files_processed": len(files),
            "mode": args.mode or "default",
            "total_duration_seconds": total_duration,
            "avg_runtime_seconds": avg_runtime,
        },
        "kpis": {
            "successful_runs": ok_count,
            "failed_runs": fail_count,
            "total_action_items": total_actions,
            "avg_action_items_per_success": round(total_actions / ok_count, 2) if ok_count else 0.0,
            "top_owners": [{"owner": k, "count": v} for k, v in owner_counter.most_common(10)],
            "high_risk_runs": sum(1 for r in report_items if r.get("risk_level") == "high"),
            "medium_risk_runs": sum(1 for r in report_items if r.get("risk_level") == "medium"),
            "negative_sentiment_runs": sum(1 for r in report_items if r.get("sentiment") == "negative"),
        },
        "runs": report_items,
    }

    output_path = Path(args.output_json)
    output_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[DONE] Report saved -> {output_path.resolve()}")

    if args.strict and fail_count > 0:
        sys.exit(2)


if __name__ == "__main__":
    main()
