# MeetingMind++ (Airia Active Agent)

## Elevator Pitch
MeetingMind++ turns meeting transcripts into real execution.  
It does not stop at summaries: it routes work through human approval or auto-execution, creates operational outputs, and can push real actions to Slack, Jira, and email.

## Problem
Teams waste 30-60 minutes after meetings doing repetitive follow-up work:
- writing summaries,
- assigning ownership,
- creating tasks,
- sending updates.

Most summaries never become executed workflows.

## Solution
MeetingMind++ is a dual-mode Airia agent:
- `Manual Approval` mode: human-in-the-loop review before actions continue.
- `Auto Execute` mode: fast lane for direct post-meeting execution.

Core capabilities:
- Structured summary generation.
- Action extraction (`task`, `owner`, `deadline`, `priority`).
- Sentiment + risk scoring (deadline risk, missing owner/deadline, escalation language).
- Cross-meeting memory insights (repeated unresolved actions).
- Real integration fan-out (Slack/Jira/Email) from the orchestration layer.

## Built With
- Airia Agent Studio (published agent, conditional routing, human approval, multi-step pipeline)
- Python (`meetingmind_runner.py`, `meetingmind_batch.py`, `meetingmind_report_md.py`)
- Streamlit (`meetingmind_dashboard.py`) for polished frontend demo
- Slack Incoming Webhooks
- Jira REST API v3
- SMTP (Gmail/Office365 compatible)

## Architecture
Airia (published v5.00):
- Input -> Mode Router (Conditional Branch)
- Manual Approval route -> Approval Request -> AI Model -> Action Planner Agent -> No-Op -> Delivery Packager Agent -> Output
- Auto Execute route -> AI Model (same downstream chain)
- Denied approval route -> Stop and Error

Local orchestration:
- Executes Airia endpoint/webhook
- Parses and validates outputs
- Applies idempotency ledger to avoid duplicate writes
- Dispatches Slack/Jira/Email
- Persists memory + risk metadata

## What Makes It Different
- It is an execution system, not only a summarizer.
- It supports both safe governance (`Manual Approval`) and speed (`Auto Execute`).
- It includes risk intelligence + historical memory, which makes the agent proactive instead of reactive.
- It provides both backend automation and a demo-friendly frontend UI.

## Impact
- Cuts post-meeting ops overhead dramatically.
- Improves accountability (clear owners and deadlines).
- Reduces dropped tasks with repeated-action memory detection.
- Gives managers a risk signal immediately after each meeting.

## Demo Flow (Suggested)
1. Open Streamlit dashboard (`meetingmind_dashboard.py`).
2. Run `manual` mode with transcript and show approval-gated behavior.
3. Run `auto` mode and show direct completion.
4. Show:
- parsed action table,
- risk/sentiment panel,
- memory insights.
5. Trigger real Slack/Jira/Email integrations live.
6. Show generated KPI report (`demo_report.md`).

## Validation
- Unit tests: `22/22` passing.
- Published Airia agent: v5.00.
- Dual-route behavior validated through live execution.

## Public Airia URL
`https://airia.ai/019cf690-4098-7aba-939c-a6292ca7c563/agents/06713078-ed36-4316-a783-dd89e562ae07/5.00`

