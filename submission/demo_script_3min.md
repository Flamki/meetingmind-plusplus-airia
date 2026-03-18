# MeetingMind++ Demo Script (Quota-Safe, 3-4 Minutes)

## 0:00 - 0:25 Problem
"Teams spend 30-60 minutes after meetings doing manual follow-up. MeetingMind++ turns transcripts into accountable execution automatically."

## 0:25 - 1:05 Architecture (Airia App-Native)
Show Airia canvas for active `v17.00`:
- `Mode Router`
- `Manual Approval` + `Auto Execute`
- `Action Planner Agent`
- `Slack HTTPS Delivery` (HttpRequest)
- `Jira HTTPS Delivery` (HttpRequest)
- `Delivery Packager Agent` -> `Output`

Say:
"This is app-native in Airia, not a wrapper script. Integrations are in the workflow itself."

## 1:05 - 1:45 Integration Proof in Configuration
Open `Slack HTTPS Delivery` node:
- Show `Post` method
- Show webhook URL host
- Show body uses `<stepResult value='Action Planner Agent'/>`

Open `Jira HTTPS Delivery` node:
- Show Jira REST URL `/rest/api/3/issue`
- Show JSON body for issue creation in project `TC`

## 1:45 - 2:35 Execution Evidence (Already Completed)
Show these local proof files:
- `raw/demo_auto.run_report.json`
- `raw/demo_manual.run_report.json`
- `raw/manual_slack_approval.run_report.json`

Call out from evidence:
- Auto run succeeded
- Slack posted successfully
- Jira issues created: `TC-15` to `TC-19`
- Email sent
- Manual run paused for approval as expected

## 2:35 - 3:10 Metrics + Outcomes
Show:
- `demo_report.md`
- success rate
- total action items
- risk distribution
- owner accountability summary

## 3:10 - 3:35 Quota Constraint Disclosure
Show latest execution response:
- `raw/pipeline_exec_v17_check.json`
- message: `Monthly agent executions limit reached`

Say:
"The current limit is platform quota. Workflow configuration and prior real integrations are already validated."

## 3:35 - 4:00 Close
"MeetingMind++ is a dual-mode governed execution system built natively on Airia. It converts meetings into Slack communication, Jira tasking, and operational follow-through."
