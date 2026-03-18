# MeetingMind++ Airia Presentation Notes

Use this while recording or explaining to judges.

## 1) One-line opener
MeetingMind++ is an Airia-native agent that turns meeting transcripts into execution, with dual-mode routing for governance (`manual`) or speed (`auto`), and real delivery to Slack and Jira.

## 2) Airia canvas walk-through (node-by-node)
Active version: `17.00`

1. `Input`
- Receives transcript text and run instruction.
- This is where user starts execution.

2. `Mode Router`
- Reads intent and routes flow:
  - `Manual Approval` path for governance.
  - `Auto Execute` path for fast autonomous delivery.

3. `Approval Request` (manual path only)
- Human-in-the-loop checkpoint.
- If denied, workflow exits safely via error-stop path.

4. `AI Model`
- Produces structured meeting understanding from transcript.
- Shared by both manual/auto branches after routing logic.

5. `Action Planner Agent`
- Converts understanding into actionable outputs:
  - prioritized tasks
  - owners
  - deadlines
  - execution-ready plan text

6. `Slack HTTPS Delivery` (HttpRequest)
- Posts planner output to Slack webhook.
- Uses planner output as message payload.
- This proves external communication delivery is inside Airia flow.

7. `Jira HTTPS Delivery` (HttpRequest)
- Calls Jira REST API to create issue(s) in project `TC`.
- Uses planner result as issue content context.
- This proves tasking system write is inside Airia flow.

8. `Delivery Packager Agent`
- Consolidates final output for user-facing response.
- Ensures execution artifacts are clearly formatted.

9. `Output`
- Returns final packaged response in Airia.

10. `Stop and Error`
- Fail-safe termination node for denied/manual rejection or stop conditions.

## 3) What to say for judging criteria
- Workflow complexity:
  - multi-step orchestration, conditional routing, approval gate, delivery chain.
- Practicality:
  - converts meeting notes into real operational outputs.
- Technical execution:
  - app-native HTTPS integrations in Airia, not only local scripting.
- Governance:
  - manual approval path + safe stop behavior.

## 4) How to prove “it worked” even with current quota limit
Show these files on screen:

1. `raw/demo_auto.run_report.json`
- Evidence of successful end-to-end run.
- Contains:
  - Slack success (`posted_webhook`)
  - Jira ticket creation (`TC-15` to `TC-19`)
  - Email success (`sent`)

2. `raw/demo_manual.run_report.json`
- Evidence of HITL flow:
  - status `pending_approval`
  - execution reference returned

3. `raw/manual_slack_approval.run_report.json`
- Evidence approval notification path posted to Slack.

4. Optional KPI summary:
- `demo_report.md`
- Shows success rate, action count, risk profile.

## 5) Quota-limit explanation (say this exactly)
We hit Airia monthly execution quota today.  
The workflow itself is fully configured and previously validated with real successful runs to Slack, Jira, and email; current non-execution is due to platform quota, not architecture or integration failure.

## 6) What to show in final 3-minute video
1. Airia canvas `v17.00` and node walk-through.
2. Open `Slack HTTPS Delivery` config.
3. Open `Jira HTTPS Delivery` config.
4. Show previous success report (`demo_auto.run_report.json`).
5. Show manual HITL proof (`demo_manual.run_report.json`).
6. Show quota message file (`raw/pipeline_exec_v17_check.json`).
7. End with published Airia URL.

## 7) Final links to show
- Airia agent URL:
  - `https://airia.ai/019cf690-4098-7aba-939c-a6292ca7c563/agents/06713078-ed36-4316-a783-dd89e562ae07/17.00`
- GitHub repo:
  - `https://github.com/Flamki/meetingmind-plusplus-airia`
