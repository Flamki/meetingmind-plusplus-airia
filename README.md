# MeetingMind Runner (Airia Endpoint)

Run your Airia agent from terminal and fan out to Slack/Jira/Email (Teams optional).

## 1) Install

```powershell
cd C:\Users\bbook\Desktop\airia
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## 2) Set env vars

Copy `.env.example` values into your environment (or set manually in PowerShell):

```powershell
$env:AIRIA_API_KEY="YOUR_KEY"
$env:AIRIA_PIPELINE_ID="06713078-ed36-4316-a783-dd89e562ae07"
```

Or create a local `.env` file (same folder). `meetingmind_runner.py`, `meetingmind_batch.py`, and `run_all.ps1` auto-load it.

Webhook mode (recommended if you have a webhook URL):

```powershell
$env:AIRIA_WEBHOOK_URL="https://api.airia.ai/v1/webhook/...."
```

Optional integrations:
- `SLACK_WEBHOOK_URL` (or `SLACK_BOT_TOKEN` + `SLACK_CHANNEL`)
- `TEAMS_WEBHOOK_URL`
- `JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN`, `JIRA_PROJECT_KEY`
- `SMTP_HOST`, `SMTP_PORT`, `SMTP_SECURITY`, `SMTP_USER`, `SMTP_PASSWORD`, `EMAIL_FROM`, `EMAIL_TO`
- Optional Slack approval routing:
- `APPROVAL_APPROVE_URL_TEMPLATE`
- `APPROVAL_DENY_URL_TEMPLATE`
- `SLACK_SIGNING_SECRET`
- `AIRIA_APPROVAL_CALLBACK_URL`

For Gmail SMTP, use:
- `SMTP_HOST=smtp.gmail.com`
- `SMTP_PORT=587`
- `SMTP_SECURITY=starttls`
- `SMTP_USER=<your_gmail>`
- `SMTP_PASSWORD=<Google App Password>`

Slack token mode (if webhook not configured):
- `SLACK_BOT_TOKEN=<token>`
- `SLACK_CHANNEL=#your-channel` (or channel ID)
- Token must have `chat:write` (or `chat:write:bot`) permission. Tokens with only
  `app_configurations:*` scopes cannot post messages.

## 3) Run

Text input:

```powershell
python .\meetingmind_runner.py --user-input "Summarize this meeting and extract action items" --require-actions
```

Text input via webhook explicitly:

```powershell
python .\meetingmind_runner.py --webhook-url "https://api.airia.ai/v1/webhook/...." --user-input "Summarize this meeting and extract action items" --require-actions
```

Force direct `PipelineExecution` mode (ignore webhook):

```powershell
python .\meetingmind_runner.py --no-webhook --transcript-file .\sample_transcript.txt --require-actions
```

From transcript file:

```powershell
python .\meetingmind_runner.py --transcript-file .\sample_transcript.txt --require-actions
```

With integrations:

```powershell
python .\meetingmind_runner.py --transcript-file .\sample_transcript.txt --post-slack --create-jira --send-email --require-actions --strict-integrations
```

Raw API output is saved to `airia_response.json` by default.

Hardening flags:
- `--retries 3 --backoff-seconds 1.25 --jitter-seconds 0.25`
- `--idempotency-file .\.meetingmind_idempotency.json` (default)
- `--dry-run` for validation without external writes
- `--no-idempotency` to disable duplicate protection (not recommended)
- `--mode manual|auto` to force dual-route behavior in Airia `Mode Router`
- `--memory-file .\.meetingmind_memory.json` for cross-meeting memory
- `--disable-memory` to disable local memory insights
- `--strict-integrations` to fail run if selected integrations fail
- `--run-report .\run_report.json` to save structured audit output per run
- `--smtp-security auto|starttls|ssl|none` for provider-specific SMTP compatibility
- `--fanout-mode parallel|sequential` for delivery strategy (parallel is default and fastest)
- `--notify-slack-approval` to post approval cards when manual route is pending

Example winner-demo command:

```powershell
python .\meetingmind_runner.py `
  --transcript-file .\transcripts\meeting_01_product_sync.txt `
  --mode auto `
  --post-slack --create-jira --send-email `
  --require-actions --strict-integrations
```

## 3.1) Polished Frontend UI (Streamlit)

Run a polished control-center UI (frontend + backend demo):

```powershell
streamlit run .\meetingmind_dashboard.py
```

In the UI you can:
- Choose router mode (`manual` or `auto`)
- Run Airia execution from transcript upload/paste
- Show parsed action items + sentiment/risk
- Show meeting-memory insights
- Trigger real Slack/Teams/Jira/Asana/Email integrations live
- Trigger real Slack/Jira/Email integrations live (Teams optional)
- View service readiness indicators (green/red)
- Browse past meeting memory history
- Inspect a risk heatmap over recent runs
- Run one-click manual+auto demo flow

## 4) Batch Mode (Auto Demo Report JSON)

Create a folder like `.\transcripts\` with multiple `.txt` meeting transcripts, then run:

```powershell
python .\meetingmind_batch.py --input-dir .\transcripts --pattern *.txt --output-json .\demo_report.json
```

Useful flags:
- `--recursive` to include nested folders
- `--limit 5` to process only first 5 files
- `--save-raw-dir .\raw` to store per-file raw API outputs
- `--strict` to exit non-zero if any file fails
- `--require-actions` to fail runs with no extracted action items
- `--max-input-chars 300000` to reject oversized transcripts
- `--retries 3 --backoff-seconds 1.25 --jitter-seconds 0.25`

Example:

```powershell
python .\meetingmind_batch.py `
  --input-dir .\transcripts `
  --pattern *.txt `
  --recursive `
  --save-raw-dir .\raw `
  --output-json .\demo_report.json `
  --require-actions `
  --strict
```

## 5) Generate Devpost-Ready Markdown Report

Convert JSON output into a polished markdown summary:

```powershell
python .\meetingmind_report_md.py --input-json .\demo_report.json --output-md .\demo_report.md
```

Custom title example:

```powershell
python .\meetingmind_report_md.py `
  --input-json .\demo_report.json `
  --output-md .\demo_report.md `
  --title "MeetingMind Hackathon Evaluation"
```

## 6) One-Command End-to-End Run

After setting env vars, run:

```powershell
.\run_all.ps1 -InputDir .\transcripts -Pattern *.txt -Recursive -RequireActions
```

With explicit webhook:

```powershell
.\run_all.ps1 -InputDir .\transcripts -Pattern *.txt -Recursive -RequireActions -WebhookUrl "https://api.airia.ai/v1/webhook/...."
```

With custom env file:

```powershell
.\run_all.ps1 -EnvFile .\.env -InputDir .\transcripts -Pattern *.txt -Recursive -RequireActions
```

Force direct `PipelineExecution` in batch mode:

```powershell
.\run_all.ps1 -NoWebhook -EnvFile .\.env -InputDir .\transcripts -Pattern *.txt -Recursive -RequireActions
```

If your machine blocks local scripts, run:

```powershell
powershell -ExecutionPolicy Bypass -File .\run_all.ps1 -InputDir .\transcripts -Pattern *.txt -Recursive
```

Outputs:
- `demo_report.json`
- `demo_report.md`
- raw responses in `.\raw`

## 6.1) One-Command Winning Demo Run

```powershell
powershell -ExecutionPolicy Bypass -File .\run_winning_demo.ps1 -PostSlack -CreateJira -SendEmail -StrictIntegrations
```

Optional switches:
- `-UseWebhook`
- `-PostSlack`
- `-PostTeams`
- `-CreateJira`
- `-DryRun`
- `-StrictBatch`
- `-BatchRequireActions`

## 6.2) Weekly Cross-Meeting Intelligence (KPI Report)

Generate weekly intelligence artifacts from memory:

```powershell
powershell -ExecutionPolicy Bypass -File .\run_weekly_intelligence.ps1 -Force -SendSlack -SendEmail
```

Outputs:
- `weekly_intelligence.json`
- `weekly_intelligence.md`
- `weekly_intelligence.pdf` (if `reportlab` is installed)

Register weekly scheduler (Friday):

```powershell
powershell -ExecutionPolicy Bypass -File .\register_weekly_intelligence_task.ps1 -TaskName "MeetingMindWeeklyIntel" -WeeklyAt "17:00" -WorkingDir "C:\Users\bbook\Desktop\airia"
```

## 6.3) Slack Interactive Approval Webhook Handler

Run local webhook server for Slack interactive approval buttons:

```powershell
python .\meetingmind_slack_approvals.py --host 0.0.0.0 --port 8787
```

Set Slack App Interactivity Request URL to your public endpoint pointing to this server.
Handler expects:
- `AIRIA_APPROVAL_CALLBACK_URL` (where approve/deny decisions are forwarded)
- Optional `SLACK_SIGNING_SECRET` (for signature validation)

## 7) Scheduled Daily Job (Windows Task Scheduler)

Create `.env` in this folder (copy from `.env.example`) and include at minimum:
- `AIRIA_API_KEY`
- `AIRIA_PIPELINE_ID`

Register daily task (cron equivalent):

```powershell
powershell -ExecutionPolicy Bypass -File .\register_scheduled_task.ps1 -TaskName "MeetingMindDaily" -DailyAt "09:00" -WorkingDir "C:\Users\bbook\Desktop\airia" -NoWebhook -Recursive -RequireActions
```

Run immediately:

```powershell
Start-ScheduledTask -TaskName "MeetingMindDaily"
```

Remove task:

```powershell
powershell -ExecutionPolicy Bypass -File .\unregister_scheduled_task.ps1 -TaskName "MeetingMindDaily"
```

Scheduled logs are written to `.\logs\`.

## 8) Unit Tests

Run:

```powershell
python -m unittest discover -s .\tests -p "test_*.py" -v
```

## 9) Submission Assets

Ready-to-use files are in `.\submission\`:
- `devpost_submission.md`
- `demo_script_3min.md`
- `judging_mapping.md`
- `final_checklist.md`
