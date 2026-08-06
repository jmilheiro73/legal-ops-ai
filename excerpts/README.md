# Code excerpts

Real code from the production system, published under three sanitization rules:

1. **Identities removed** — the firm, its lawyers, clients and case numbers are replaced with neutral placeholders. Any names in examples are fictional.
2. **Trimmed for reading** — long prompt sections and repetitive branches are cut and marked with `# … trimmed …`. Nothing was rewritten to look smarter than it is.
3. **Logic untouched** — the schemas, prompt structure, decision rules and API usage are exactly what runs in production.

| File | What it shows |
|---|---|
| [`docket_classifier.py`](./docket_classifier.py) | The Claude Opus structured-extraction layer of the court-diary pipeline: strict JSON schema, a system prompt that encodes the firm's operating doctrine, few-shot examples from real (fictionalized) publications, and the split of responsibilities between the LLM and the deterministic deadline engine. |
| [`docket_analyst.py`](./docket_analyst.py) | The court-robot analysis step: case PDFs sent to Claude as native document blocks (no OCR), a hand-written analyst manual as system prompt, a fixed output contract with severity labels, and model tiering via env var. |
| [`robot_watchdog.ts`](./robot_watchdog.ts) | The TypeScript side: a serverless cron that watches the resident robot from the cloud (a watchdog on the watched machine is useless when it dies), with heartbeat rows in Postgres, anti-spam alert windows, recovery notices and push via ntfy. |
