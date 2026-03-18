# MeetingMind++ Demo Script (3-4 Minutes)

## 0:00 - 0:25 Problem
"Post-meeting execution is broken. Teams spend 30-60 minutes manually converting notes into tasks, updates, and follow-ups. MeetingMind++ automates that end-to-end."

## 0:25 - 0:45 Architecture Snapshot
Show Airia `v5.00` canvas:
- `Mode Router`
- `Manual Approval` + `Auto Execute` paths
- Planner + delivery packager chain
- HITL denial path to `Stop and Error`

Say: "This is a real dual-mode autonomous workflow, not a single prompt."

## 0:45 - 1:40 Manual Mode (Governed Flow)
In Streamlit UI:
- Select `manual` mode
- Paste/upload transcript
- Run

Narrate:
- "Manual mode routes through human approval."
- "The agent extracts action items, scores risk/sentiment, and prepares execution payloads."

Show:
- summary panel
- action table (`task/owner/deadline/priority`)
- risk panel
- memory insights panel

## 1:40 - 2:35 Auto Mode (Fast Lane)
In Streamlit UI:
- Switch to `auto` mode
- Run second transcript

Narrate:
- "Auto mode bypasses approval and executes directly."
- "Same intelligence stack, faster execution path."

Show improved output and risk signals.

## 2:35 - 3:20 Real Integrations
Enable integration toggles and run:
- Slack post (show message in channel)
- Jira issue creation (show issues appear live)
- Email send (show email received)

Narrate:
- "This is real operational closure: communication, tasking, and follow-up delivery."
- "Strict integration mode fails the run if these writes fail, so no fake-success demos."

## 3:20 - 3:45 Measurable Outcomes
Show:
- `demo_report.json`
- `demo_report.md`

Call out:
- success rate
- action volume
- risk profile
- top owners

## 3:45 - 4:00 Close
"MeetingMind++ turns meetings into accountable execution with governance and speed. Public Airia agent link is included in our Devpost submission."

