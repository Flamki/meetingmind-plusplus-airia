# Judging Criteria Mapping (MeetingMind++)

## Technological Implementation (25%)
- Published Airia agent (`v5.00`) with multi-step orchestration.
- Dual-route conditional architecture:
- `Manual Approval` (HITL governance)
- `Auto Execute` (fast autonomous execution)
- Real integration hooks:
- Slack formatted post
- Jira issue creation (REST v3, due dates, labels)
- Email follow-up send (SMTP with retries)
- Reliability mechanisms:
- idempotency ledger
- retries/backoff/jitter
- strict integration failure mode (`--strict-integrations`)

## Design (25%)
- Polished frontend via Streamlit dashboard (`meetingmind_dashboard.py`).
- Clear UX for mode selection, transcript input, action table, risk panel, memory panel.
- Balanced frontend + backend demonstration:
- frontend control center
- backend orchestration and external writes
- Human-readable reports (`demo_report.md`) for judges and stakeholders.

## Potential Impact (25%)
- Solves a broad enterprise pain point: post-meeting execution debt.
- Converts discussion into accountable actions with owners/deadlines/priorities.
- Risk layer surfaces urgency and deadline danger immediately.
- Memory layer catches repeated unresolved commitments across meetings.
- Applies across product, sales, support, operations, and leadership teams.

## Quality of Idea (25%)
- Strong novelty through combined:
- dual-mode governance + autonomy
- risk/sentiment intelligence
- cross-meeting memory continuity
- real downstream execution
- Build is practical and demoable in one workflow, not fragmented prototypes.
- Clear path from hackathon MVP to production policy controls and deeper integrations.

