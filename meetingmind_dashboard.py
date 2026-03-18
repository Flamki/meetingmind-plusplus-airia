#!/usr/bin/env python3
from __future__ import annotations

import concurrent.futures
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

import streamlit as st

from meetingmind_runner import (
    ActionItem,
    MeetingMindError,
    analyze_sentiment_and_risk,
    append_memory_run,
    best_text_from_airia_response,
    build_memory_insights,
    call_airia,
    create_jira_issue,
    extract_action_items,
    format_risk_brief,
    is_pending_human_approval,
    load_dotenv_file,
    load_memory_store,
    parse_deadline_to_date,
    parse_recipients,
    post_to_slack,
    post_to_slack_via_api,
    save_memory_store,
    smtp_send_with_retries,
)

APP_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500&display=swap');
:root {
  --mm-bg-1: #f7f5ef;
  --mm-bg-2: #e9f2ea;
  --mm-ink: #121a22;
  --mm-muted: #34424d;
  --mm-accent: #1c7c54;
  --mm-accent-2: #d96f3c;
  --mm-card: #ffffff;
  --mm-border: #d8e2de;
  --mm-danger: #b8382c;
}
.stApp {
  background:
    radial-gradient(1000px 400px at 12% 0%, #fff5de 0%, transparent 70%),
    radial-gradient(1200px 550px at 100% 20%, #dff5ef 0%, transparent 65%),
    linear-gradient(145deg, var(--mm-bg-1) 0%, var(--mm-bg-2) 100%);
}
.block-container {
  padding-top: 1.2rem;
  padding-bottom: 1.2rem;
}
h1, h2, h3, [data-testid="stMarkdownContainer"] h4 {
  font-family: "Space Grotesk", "Segoe UI", sans-serif;
  color: var(--mm-ink);
  letter-spacing: 0.01em;
}
p, li, [data-testid="stMarkdownContainer"] {
  font-family: "Space Grotesk", "Segoe UI", sans-serif;
  color: var(--mm-ink);
}
[data-testid="stSidebar"] * {
  font-family: "Space Grotesk", "Segoe UI", sans-serif !important;
}
[data-testid="stSidebar"] {
  background: linear-gradient(180deg, #f8fafb 0%, #ecf1f3 100%);
}
.mm-hero {
  border: 1px solid var(--mm-border);
  border-radius: 16px;
  padding: 20px 24px;
  background: linear-gradient(120deg, rgba(255,255,255,0.94) 0%, rgba(246,255,252,0.95) 55%, rgba(255,247,238,0.94) 100%);
  box-shadow: 0 10px 24px rgba(36, 44, 41, 0.08);
  margin-bottom: 0.75rem;
}
.mm-hero h1 {
  margin: 0 0 6px 0;
  font-size: 2rem;
  line-height: 1.1;
}
.mm-hero p {
  margin: 0;
  color: var(--mm-muted);
}
.mm-status-card {
  border: 1px solid var(--mm-border);
  border-left: 5px solid var(--mm-muted);
  border-radius: 12px;
  background: var(--mm-card);
  padding: 10px 12px;
  margin-bottom: 8px;
}
.mm-status-card strong {
  color: var(--mm-ink);
}
.mm-status-card .mm-detail {
  color: var(--mm-muted);
  font-size: 0.86rem;
  margin-top: 4px;
}
.mm-status-ok {
  border-left-color: var(--mm-accent);
}
.mm-status-bad {
  border-left-color: var(--mm-danger);
}
.mm-chip {
  display: inline-block;
  padding: 3px 8px;
  border-radius: 999px;
  font-size: 0.73rem;
  font-weight: 700;
  letter-spacing: 0.03em;
  margin-left: 6px;
  vertical-align: middle;
}
.mm-chip-ok {
  color: #0f5132;
  background: #d8f2e2;
}
.mm-chip-bad {
  color: #6d1f19;
  background: #f8ddda;
}
[data-testid="stMetric"] {
  background: rgba(255, 255, 255, 0.72);
  border: 1px solid var(--mm-border);
  border-radius: 12px;
  padding: 8px 12px;
}
[data-testid="stMetricLabel"] {
  color: var(--mm-muted);
}
[data-testid="stMetricValue"] {
  color: var(--mm-ink);
  font-family: "Space Grotesk", "Segoe UI", sans-serif;
}
[data-baseweb="tab-list"] button {
  color: #20303b !important;
  font-weight: 600 !important;
}
[data-baseweb="tab-list"] button[aria-selected="true"] {
  color: #12212b !important;
  border-bottom-color: #d96f3c !important;
}
.stTextArea textarea, .stTextInput input, .stSelectbox div[data-baseweb="select"] {
  background-color: #ffffff !important;
  color: #12212b !important;
}
.stButton > button {
  border-radius: 10px;
  border: 1px solid #d1ddd8;
  font-weight: 600;
}
.stButton > button[kind="primary"] {
  border: 1px solid #ca5f2f;
  background: linear-gradient(135deg, #d96f3c 0%, #ea8453 100%);
}
.stButton > button:hover {
  border-color: #8ea39a;
}
.mm-panel {
  border: 1px solid var(--mm-border);
  border-radius: 14px;
  padding: 12px;
  background: rgba(255, 255, 255, 0.78);
}
.mm-mono {
  font-family: "IBM Plex Mono", "Consolas", monospace;
}
</style>
"""


def _with_mode_prefix(text: str, mode: str) -> str:
    lowered = text.lower()
    if mode == "manual" and "manual mode" not in lowered:
        return f"manual mode: review first. {text}"
    if mode == "auto" and "auto mode" not in lowered:
        return f"auto mode: bypass approval. {text}"
    return text


def _actions_table(actions: List[ActionItem]) -> List[dict]:
    return [{"task": a.task, "owner": a.owner, "deadline": a.deadline, "priority": a.priority} for a in actions]


def _service_status() -> Dict[str, Tuple[bool, List[str]]]:
    checks = {
        "Airia API": ["AIRIA_API_KEY", "AIRIA_PIPELINE_ID"],
        "Airia Webhook": ["AIRIA_WEBHOOK_URL"],
        "Slack": [["SLACK_WEBHOOK_URL"], ["SLACK_BOT_TOKEN", "SLACK_CHANNEL"]],
        "Jira": ["JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN", "JIRA_PROJECT_KEY"],
        "Email SMTP": ["SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "EMAIL_FROM", "EMAIL_TO"],
    }
    out: Dict[str, Tuple[bool, List[str]]] = {}
    for name, req in checks.items():
        if isinstance(req[0], list):
            ok = False
            missing_all: List[str] = []
            for clause in req:  # type: ignore[arg-type]
                missing = [k for k in clause if not os.getenv(k, "").strip()]
                if not missing:
                    ok = True
                    missing_all = []
                    break
                missing_all.extend(missing)
            out[name] = (ok, sorted(set(missing_all)) if not ok else [])
        else:
            missing = [k for k in req if not os.getenv(k, "").strip()]  # type: ignore[arg-type]
            out[name] = (not missing, missing)
    return out


def _log(msg: str) -> None:
    if "live_logs" not in st.session_state:
        st.session_state.live_logs = []
    ts = datetime.now().strftime("%H:%M:%S")
    st.session_state.live_logs.append(f"[{ts}] {msg}")
    st.session_state.live_logs = st.session_state.live_logs[-200:]


def _render_risk_heatmap(memory_store: Dict[str, Any]) -> None:
    runs = memory_store.get("runs", [])
    if not runs:
        st.info("No memory history yet for heatmap.")
        return

    bucket: Dict[str, Dict[str, int]] = {}
    for run in runs[-50:]:
        if not isinstance(run, dict):
            continue
        ts = str(run.get("timestamp", ""))[:10]
        risk = str(run.get("risk_level", "unknown")).lower()
        if len(ts) != 10:
            continue
        bucket.setdefault(ts, {"low": 0, "medium": 0, "high": 0, "unknown": 0})
        bucket[ts][risk if risk in bucket[ts] else "unknown"] += 1

    rows: List[Dict[str, Any]] = []
    for day in sorted(bucket.keys()):
        rows.append({"date": day, **bucket[day]})

    total_high = sum(r.get("high", 0) for r in rows)
    total_medium = sum(r.get("medium", 0) for r in rows)
    total_low = sum(r.get("low", 0) for r in rows)
    m1, m2, m3 = st.columns(3)
    m1.metric("High Risk (50 runs)", total_high)
    m2.metric("Medium Risk (50 runs)", total_medium)
    m3.metric("Low Risk (50 runs)", total_low)
    st.dataframe(rows, use_container_width=True, hide_index=True)


def _status_card(name: str, ok: bool, detail: str) -> str:
    state = "READY" if ok else "MISSING"
    chip_class = "mm-chip-ok" if ok else "mm-chip-bad"
    card_class = "mm-status-ok" if ok else "mm-status-bad"
    return (
        f"<div class='mm-status-card {card_class}'>"
        f"<strong>{name}</strong><span class='mm-chip {chip_class}'>{state}</span>"
        f"<div class='mm-detail'>{detail}</div>"
        "</div>"
    )


def _dispatch_integrations(
    *,
    parallel: bool,
    post_slack_enabled: bool,
    create_jira_enabled: bool,
    send_email_enabled: bool,
    summary_text: str,
    actions: List[ActionItem],
    risk: Dict[str, Any],
    memory_insights: List[str],
) -> List[Tuple[str, bool, str]]:
    results: List[Tuple[str, bool, str]] = []

    def do_slack() -> Tuple[str, bool, str]:
        webhook = os.getenv("SLACK_WEBHOOK_URL", "").strip()
        token = os.getenv("SLACK_BOT_TOKEN", "").strip()
        channel = os.getenv("SLACK_CHANNEL", "").strip()
        if webhook:
            post_to_slack(
                webhook_url=webhook,
                summary_text=summary_text,
                actions=actions,
                risk=risk,
                memory_insights=memory_insights,
            )
            return ("Slack", True, "posted_webhook")
        if token and channel:
            post_to_slack_via_api(
                token=token,
                channel=channel,
                summary_text=summary_text,
                actions=actions,
                risk=risk,
                memory_insights=memory_insights,
            )
            return ("Slack", True, f"posted_api:{channel}")
        raise MeetingMindError("Slack not configured (need SLACK_WEBHOOK_URL or SLACK_BOT_TOKEN+SLACK_CHANNEL)")

    def do_jira() -> Tuple[str, bool, str]:
        jira_url = os.getenv("JIRA_BASE_URL", "").strip()
        jira_email = os.getenv("JIRA_EMAIL", "").strip()
        jira_token = os.getenv("JIRA_API_TOKEN", "").strip()
        jira_project = os.getenv("JIRA_PROJECT_KEY", "").strip()
        if not all([jira_url, jira_email, jira_token, jira_project]):
            raise MeetingMindError("Jira not configured (need JIRA_BASE_URL/JIRA_EMAIL/JIRA_API_TOKEN/JIRA_PROJECT_KEY)")
        created = 0
        for a in actions or [ActionItem(task="Meeting follow-up task (unparsed)")]:
            deadline_date = parse_deadline_to_date(a.deadline)
            create_jira_issue(
                base_url=jira_url,
                email=jira_email,
                api_token=jira_token,
                project_key=jira_project,
                summary=a.task,
                description=(
                    f"Owner: {a.owner}\nDeadline: {a.deadline}\nPriority: {a.priority}\n"
                    f"Risk Brief: {format_risk_brief(risk)}\n\nGenerated by MeetingMind Dashboard."
                ),
                due_date_iso=(deadline_date.isoformat() if deadline_date else None),
                labels=["meetingmind", f"risk-{risk.get('risk_level', 'unknown')}", f"priority-{a.priority}"],
            )
            created += 1
        return ("Jira", True, f"created_{created}_issues")

    def do_email() -> Tuple[str, bool, str]:
        smtp_host = os.getenv("SMTP_HOST", "").strip()
        smtp_port = int(os.getenv("SMTP_PORT", "587"))
        smtp_user = os.getenv("SMTP_USER", "").strip()
        smtp_password = os.getenv("SMTP_PASSWORD", "").strip()
        email_from = os.getenv("EMAIL_FROM", "").strip()
        email_to = os.getenv("EMAIL_TO", "").strip()
        if not all([smtp_host, smtp_user, smtp_password, email_from, email_to]):
            raise MeetingMindError("Email not configured (need SMTP_* and EMAIL_*)")
        smtp_send_with_retries(
            smtp_host=smtp_host,
            smtp_port=smtp_port,
            smtp_user=smtp_user,
            smtp_password=smtp_password,
            from_addr=email_from,
            to_addrs=parse_recipients(email_to),
            subject=f"[MeetingMind][{str(risk.get('risk_level', 'unknown')).upper()}] Meeting Follow-up",
            body=(
                f"{summary_text}\n\nRisk Brief: {format_risk_brief(risk)}\n\nAction items:\n"
                + "\n".join([f"- {a.task} (Owner: {a.owner}, Deadline: {a.deadline}, Priority: {a.priority})" for a in actions])
            ),
            smtp_security=os.getenv("SMTP_SECURITY", "auto"),
        )
        return ("Email", True, "sent")

    jobs: List[Tuple[str, Any]] = []
    if post_slack_enabled:
        jobs.append(("Slack", do_slack))
    if create_jira_enabled:
        jobs.append(("Jira", do_jira))
    if send_email_enabled:
        jobs.append(("Email", do_email))

    if not jobs:
        return [("Integrations", True, "none_selected")]

    if parallel and len(jobs) > 1:
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(5, len(jobs))) as executor:
            future_map = {executor.submit(fn): name for name, fn in jobs}
            for future in concurrent.futures.as_completed(future_map):
                name = future_map[future]
                try:
                    results.append(future.result())
                except Exception as exc:
                    results.append((name, False, str(exc)))
    else:
        for name, fn in jobs:
            try:
                results.append(fn())
            except Exception as exc:
                results.append((name, False, str(exc)))

    return results


def _execute_once(*, content: str, mode: str, use_webhook: bool) -> Dict[str, Any]:
    user_input = _with_mode_prefix(content, mode)
    pipeline_id = os.getenv("AIRIA_PIPELINE_ID", "").strip()
    api_key = os.getenv("AIRIA_API_KEY", "").strip()
    webhook_url = os.getenv("AIRIA_WEBHOOK_URL", "").strip() if use_webhook else None
    if not webhook_url and (not pipeline_id or not api_key):
        raise MeetingMindError("Need AIRIA_WEBHOOK_URL or AIRIA_PIPELINE_ID + AIRIA_API_KEY")
    return call_airia(
        pipeline_id=pipeline_id,
        api_key=api_key,
        user_input=user_input,
        async_output=False,
        webhook_url=webhook_url,
    )


def main() -> None:
    load_dotenv_file(Path(".env"))
    st.set_page_config(
        page_title="MeetingMind++ Control Center",
        page_icon="MM",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    st.markdown(APP_CSS, unsafe_allow_html=True)
    st.markdown(
        """
        <div class="mm-hero">
          <h1>MeetingMind++ Command Deck</h1>
          <p>Enterprise meeting operations cockpit with dual-route execution, live integrations, risk intelligence, and memory telemetry.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    memory_path = Path(".meetingmind_memory.json")
    memory_store = load_memory_store(memory_path)
    statuses = _service_status()

    ready_services = sum(1 for ok, _ in statuses.values() if ok)
    total_services = len(statuses)
    recent_runs = len(memory_store.get("runs", []))
    high_risk_recent = sum(
        1
        for run in memory_store.get("runs", [])[-10:]
        if isinstance(run, dict) and str(run.get("risk_level", "")).lower() == "high"
    )

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Services Ready", f"{ready_services}/{total_services}")
    k2.metric("Recent Runs", recent_runs)
    k3.metric("High-Risk in Last 10", high_risk_recent)
    k4.metric("Active Profile", "Hackathon Live")

    with st.sidebar:
        st.subheader("Execution Setup")
        mode = st.radio("Routing Mode", ["manual", "auto"], horizontal=True)
        use_webhook = st.toggle("Use Airia Webhook", value=bool(os.getenv("AIRIA_WEBHOOK_URL")))
        parallel_fanout = st.toggle("Parallel Fanout Delivery", value=True)
        post_slack_enabled = st.toggle("Post to Slack", value=False)
        create_jira_enabled = st.toggle("Create Jira Tasks", value=False)
        send_email_enabled = st.toggle("Send Follow-up Email", value=False)
        dry_run = st.toggle("Dry Run (no external writes)", value=False)

        st.divider()
        st.subheader("Service Health")
        for name, (ok, missing) in statuses.items():
            detail = "Configured and ready" if ok else f"Missing: {', '.join(missing[:4])}"
            st.markdown(_status_card(name, ok, detail), unsafe_allow_html=True)

    tab_exec, tab_observe, tab_memory = st.tabs(["Execution", "Observability", "Memory History"])

    with tab_exec:
        col_left, col_right = st.columns([1.35, 1.0], gap="large")
        with col_left:
            st.markdown("<div class='mm-panel'>", unsafe_allow_html=True)
            transcript_file = st.file_uploader("Upload Meeting Transcript (.txt)", type=["txt"])
            transcript_text = st.text_area("Paste Transcript / Meeting Notes", height=260)
            run_clicked = st.button("Run MeetingMind++", type="primary", use_container_width=True)
            one_click_demo = st.button("One-Click Demo (Manual + Auto)", use_container_width=True)
            st.markdown("</div>", unsafe_allow_html=True)
            result_placeholder = st.container()

        with col_right:
            st.markdown("<div class='mm-panel'>", unsafe_allow_html=True)
            st.subheader("Live Execution Log")
            log_placeholder = st.empty()
            log_placeholder.code("\n".join(st.session_state.get("live_logs", [])) or "(no logs yet)")
            st.markdown("</div>", unsafe_allow_html=True)

    with tab_observe:
        st.subheader("Risk Distribution Over Time")
        _render_risk_heatmap(memory_store)
        st.subheader("Integration Readiness Matrix")
        status_rows = []
        for name, (ok, missing) in statuses.items():
            status_rows.append(
                {
                    "service": name,
                    "state": "READY" if ok else "MISSING",
                    "missing_keys": ", ".join(missing) if missing else "-",
                }
            )
        st.dataframe(status_rows, use_container_width=True, hide_index=True)

    with tab_memory:
        st.subheader("Past Meeting History")
        runs = memory_store.get("runs", [])
        history_rows = []
        for run in reversed(runs[-30:]):
            if not isinstance(run, dict):
                continue
            actions = run.get("actions", [])
            history_rows.append(
                {
                    "timestamp": run.get("timestamp", ""),
                    "risk_level": run.get("risk_level", "unknown"),
                    "sentiment": run.get("sentiment", "unknown"),
                    "action_count": len(actions) if isinstance(actions, list) else 0,
                }
            )
        if history_rows:
            st.dataframe(history_rows, use_container_width=True, hide_index=True)
        else:
            st.info("No history yet.")

    def refresh_logs() -> None:
        log_placeholder.code("\n".join(st.session_state.get("live_logs", [])) or "(no logs yet)")

    def process_single(content: str, selected_mode: str) -> None:
        _log(f"Starting {selected_mode} execution")
        refresh_logs()

        response = _execute_once(content=content, mode=selected_mode, use_webhook=use_webhook)
        raw_dir = Path("raw")
        raw_dir.mkdir(exist_ok=True)
        (raw_dir / f"live_run_{selected_mode}.airia.json").write_text(
            json.dumps(response, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        if is_pending_human_approval(response):
            execution_ref = response.get("executionId") or response.get("result")
            _log(f"{selected_mode} paused for approval: {execution_ref}")
            refresh_logs()
            st.warning(f"{selected_mode.capitalize()} run is waiting for approval: {execution_ref}")
            return

        summary_text = best_text_from_airia_response(response)
        actions = extract_action_items(summary_text, max_items=100)
        risk = analyze_sentiment_and_risk(summary_text, actions)
        current_store = load_memory_store(memory_path)
        memory_insights = build_memory_insights(actions, current_store)

        st.markdown("### Summary")
        st.write(summary_text[:12000] if summary_text else "(No summary returned)")

        st.markdown("### Action Items")
        if actions:
            st.dataframe(_actions_table(actions), use_container_width=True, hide_index=True)
        else:
            st.info("No structured action items parsed.")

        st.markdown("### Sentiment + Risk")
        st.write(format_risk_brief(risk))

        st.markdown("### Memory Insights")
        st.write("\n".join([f"- {x}" for x in memory_insights[:8]]))

        if not dry_run:
            _log(f"Dispatching integrations ({'parallel' if parallel_fanout else 'sequential'})")
            refresh_logs()
            integration_results = _dispatch_integrations(
                parallel=parallel_fanout,
                post_slack_enabled=post_slack_enabled,
                create_jira_enabled=create_jira_enabled,
                send_email_enabled=send_email_enabled,
                summary_text=summary_text,
                actions=actions,
                risk=risk,
                memory_insights=memory_insights,
            )
            ok_parts = [f"{name}:{detail}" for name, ok, detail in integration_results if ok]
            bad_parts = [f"{name}:{detail}" for name, ok, detail in integration_results if not ok]
            if ok_parts:
                st.success(" | ".join(ok_parts))
            if bad_parts:
                st.error(" | ".join(bad_parts))
            for name, ok, detail in integration_results:
                _log(f"{name} -> {'OK' if ok else 'FAIL'} ({detail})")
            refresh_logs()
        else:
            st.info("Dry run enabled: integrations skipped.")

        updated_store = append_memory_run(current_store, actions, risk)
        save_memory_store(memory_path, updated_store)
        _log(f"{selected_mode} execution completed and memory updated")
        refresh_logs()

    try:
        if run_clicked:
            content = transcript_text.strip()
            if transcript_file and not content:
                content = transcript_file.read().decode("utf-8", errors="ignore")
            if not content:
                st.error("Please provide transcript text (upload or paste).")
            else:
                with result_placeholder:
                    process_single(content, mode)

        if one_click_demo:
            t1 = Path("transcripts/meeting_01_product_sync.txt")
            t2 = Path("transcripts/meeting_02_customer_escalation.txt")
            if not t1.exists() or not t2.exists():
                raise MeetingMindError(
                    "One-click demo expects transcripts/meeting_01_product_sync.txt and meeting_02_customer_escalation.txt"
                )
            with result_placeholder:
                process_single(t1.read_text(encoding="utf-8"), "manual")
                process_single(t2.read_text(encoding="utf-8"), "auto")

    except MeetingMindError as exc:
        _log(f"ERROR: {exc}")
        refresh_logs()
        st.error(str(exc))
    except Exception as exc:  # pragma: no cover
        _log(f"UNHANDLED ERROR: {exc}")
        refresh_logs()
        st.exception(exc)


if __name__ == "__main__":
    main()
