# NovaHearth Warranty Case Agent

An assessment-ready local warranty case application built with **FastAPI**, a **React** dashboard, **LangGraph**, configurable in-house/OpenAI-compatible, Groq, OpenAI, and Ollama model providers, deterministic CSV-backed checks, and SQLite persistence.

## Workflow architecture

```text
Claim CSV
   ↓
Claim Intake Agent ── structured intent, product mentioned, issue, requested remedy (product text is grounded to the claim)
   ↓
Warranty Assessment Agent ── model selects evidence tools; replacement history is conditional (no generated cause narrative)
   ↓
Warranty Assessment Agent ── exact product-ref lookup + policy support check tools
   ↓
Resolution Policy Agent ── policy gates; model output cannot bypass these
   ├── eligible refund / credit / like-for-like replacement → simulated action tool → resolved
   └── uncertain, unsupported, high-value, repeat, or mismatched → LangGraph human interrupt
                                                        ↓ reviewer approve/deny
                                                   simulated action / denial
```

Each graph node has one responsibility. The assessment agent gives the model bounded control of three read-only evidence tools: warranty lookup and coverage check are required, while replacement history is required only for replacement requests. The model chooses whether and when to gather that optional evidence; tool arguments are sanitized and missing required tools alone are completed by deterministic fallback. The ordered model and fallback tool calls are logged separately. The deterministic policy agent alone decides eligibility and the action tools remain behind that gate. Model implementations satisfy the `ChatModelProvider` interface, so the configurable in-house OpenAI-compatible endpoint, Groq, OpenAI, and local Ollama can be switched without rewriting agent logic. The structured CSV data uses direct keyed lookups; vector search/RAG is intentionally not included. LangGraph's SQLite checkpointer preserves human-review workflows across page refreshes and app restarts. FastAPI serves the frontend and JSON endpoints; the application database separately stores claim decisions, actions, and audit events.

The **Agent activity** view and each claim's audit timeline record UTC timestamps for agent and tool start/completion/failure, review requests, reviewer decisions, workflow errors, actions, and LLM usage. The **Token consumption** view aggregates prompt/completion/total tokens by provider and model using usage returned by the API provider; missing usage metadata is called out instead of estimated. The UI displays evidence and concise decision reasons rather than private model chain-of-thought.

The **Agent completed** tab, above **Human review**, lists active automatic resolutions. A reviewer can select a claim and use **Override → Denied**. `POST /api/claims/{claim_id}/reverse` appends a reversal action, preserves the original action, records the reviewer and audit events, and moves the claim into the denied status/filter. This is a simulated reversal; it does not cancel a real payment or shipment.

## Policy behavior

`policy.json` is the single source for the auto-resolution cap, eligible warranty categories, and whether each resolution type requires approval. The default covered categories include manufacturing defects, known component recalls, and covered shipping damage as explicitly marked by the supplied records. A known component recall is treated as product-record evidence for that exact looked-up product; reports of customer-caused damage or normal wear still fail the support check. The default follows the build brief: **supported eligible refund/credit amounts and like-for-like replacement unit values strictly below $100 may resolve automatically**. Values at or above the cap require human review. Unsupported claims, ambiguous claims, replacement requests with a prior replacement or explicit repeat wording, and mismatched replacement targets wait for a reviewer.

The evidence assessment step uses the LLM's structured intake plus the verbatim claim and exact warranty row. It does not ask a second LLM call to invent a root-cause narrative. Summaries must be verbatim excerpts; a paraphrase that cannot be found in the submitted text falls back to the full customer text. Remedy intent is normalized deterministically: explicit contrasts such as “replacement instead of refund” are handled, and text with no clearly requested remedy is routed as unclear. Follow-up/repeat wording and recorded replacement history require human review for replacement requests. Provider rate-limit responses are not retried automatically, and the UI shows a concise message rather than exposing raw provider quota details. Provider claims are acquired with an atomic database status update, so concurrent workers cannot start the same new claim.

The intake model extracts the product name or description only when it appears in the customer's text; the UI displays that interpretation beside the product resolved from the exact claim `product_ref`. The extracted phrase is checked against the submitted text, and a mismatch with the authoritative warranty row requires human review. The claim reference—not model-generated text—controls lookup and action. A claimant/warranty-holder mismatch or missing product record also always requires review. The seed data includes one missing reference (WC-6080 → PW-9999) and one claimant/holder mismatch (WC-6023); neither may auto-resolve. Explicit customer-text cues for accidental damage and normal wear also block automatic coverage even if the model labels the issue as a manufacturing defect.

Higher-value monetary claims and other uncertain cases require human review. These policy flags remain configurable in `policy.json`.

## Actions are simulated

The bundled action tools only write `simulated_success` / `simulated_scheduled` records to SQLite. They do not issue money, reserve inventory, create real RMAs, or contact customers. A production rollout must replace these adapters with authenticated payment and fulfillment APIs. Keep idempotency keys, authorization, audit logging, error reconciliation, and human approval for financial actions. Never tell customers a transaction completed until the external provider confirms it.

## Run the project on Windows PowerShell

### Requirements

- Python 3.10 or newer
- Node.js 18 or newer (includes `npm`)
- PowerShell opened in the project root, the folder containing `api.py`, `requirements.txt`, and `frontend/`

Check that both runtimes are installed:

```powershell
py --version
node --version
npm --version
```

If `py` is unavailable, use `python` in its place. If `node` or `npm` is unavailable, install the Node.js LTS release and reopen PowerShell.

### First-time setup

Run these commands from the project root. Python dependencies and frontend dependencies are separate, so install and build both:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
if (!(Test-Path .env)) { Copy-Item .env.example .env }
cd frontend
npm install
npm run build
cd ..
```

The build step creates `frontend/dist`, which FastAPI serves as the dashboard. If you build the frontend after starting FastAPI, stop and restart the server so it mounts the generated assets.

### Configure an LLM provider

Open `.env` and configure at least one provider before processing claims:

```powershell
notepad .env
```

The project defaults to the in-house provider to match the brief. Set `INHOUSE_LLM_BASE_URL` to your OpenAI-compatible in-house endpoint and `INHOUSE_LLM_MODEL` to its model name (set `INHOUSE_LLM_API_KEY` if required). The model must support structured output and tool calling. Groq (`GROQ_API_KEY`), OpenAI (`OPENAI_API_KEY`), and local Ollama are available as alternatives for development; Ollama requires its server and configured model (default `qwen3:4b-instruct`) to be installed. The app and CSV import can start without an endpoint or key, but claim processing stays unavailable until a provider is configured. Keep secrets in `.env`; it is excluded by `.gitignore`. Restart FastAPI after changing `.env`.

The claim ledger persists between app restarts; importing the CSV files adds missing seed claims and does not erase previous decisions. For the trusted local demo, `.env.example` enables the **Clear all data** button through `ENABLE_DEMO_DATA_RESET=true`; the first-time setup copies this setting into `.env`. The button clears saved decisions, actions, reviews, token usage, checkpoints, and audit events, then reloads all 90 CSV seed claims as `new`. Keep this setting off in any exposed or production deployment.

### Preparing a clean submission

The seed CSV files in `data/` are required and should remain in the submission. Do not include `.venv/`, `frontend/node_modules/`, `__pycache__/`, `.env`, or `warranty_agent.db`; these are local environments, caches, secrets, or saved run data. The ignore rules exclude these generated/local files from Git. A fresh install creates a new SQLite database and imports the CSV claims as `new`. Keep `frontend/src/`, `frontend/package.json`, and `frontend/package-lock.json`; the setup steps install the frontend dependencies and build the dashboard from source.

For a manually created ZIP, stop the app and remove the local database before archiving so earlier demo decisions are not submitted:

```powershell
Remove-Item .\warranty_agent.db, .\warranty_agent.db-shm, .\warranty_agent.db-wal -Force -ErrorAction SilentlyContinue
```

This removes only the saved ledger/checkpoints. The CSV files remain in `data/` and are imported into a new database the next time the app starts.

### Start the application

From the project root, start one server process:

```powershell
.\.venv\Scripts\python.exe -m uvicorn api:app --host 127.0.0.1 --port 8000
```

Open the dashboard at [http://127.0.0.1:8000/](http://127.0.0.1:8000/) or API documentation at [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs). Check that the API started with:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/health
```

Keep the server window open while using the app. Press **Ctrl+C** in that window to stop it. Use `--reload` for backend development if needed:

```powershell
.\.venv\Scripts\python.exe -m uvicorn api:app --host 127.0.0.1 --port 8000 --reload
```

Do not start a second server on the same port. If port 8000 is already occupied, stop the existing server with **Ctrl+C** or identify its process with `Get-NetTCPConnection -LocalPort 8000 -State Listen` before starting another one.

### Frontend development (optional)

To use Vite's live frontend reload instead of the built dashboard, first start FastAPI as above. Open a second PowerShell window in the project root and run:

```powershell
cd frontend
npm run dev
```

Open the Vite URL printed in that window (usually [http://localhost:5173](http://localhost:5173)); it proxies `/api` to FastAPI on port 8000. To return to the built dashboard, stop Vite, run `npm run build` in `frontend/`, then restart FastAPI.

### Troubleshooting a blank page

1. Confirm the address is exactly `http://127.0.0.1:8000/` (with the final slash), not an extra path.
2. Confirm the frontend build exists: `Test-Path frontend/dist/index.html`. If it returns `False`, run `cd frontend; npm install; npm run build; cd ..`.
3. Stop FastAPI with **Ctrl+C** and start it again from the project root. The static asset directory is selected when the server starts; building after startup requires a restart.
4. Check `http://127.0.0.1:8000/api/health`. If this responds but the dashboard remains blank, open browser developer tools (**F12 → Console/Network**) and check whether `/static/assets/*.js` and `*.css` requests return `200`.

The dashboard includes overview, claim queue, human review, demo actions, warranty records, audit trail, token usage, and policy. Seed claims import idempotently on startup; the Import button can be used again safely. Run one selected claim, or choose a batch size of 10, 30, 50, or all queued claims. Batch requests return a job ID immediately; the dashboard polls progress while FastAPI processes claims in the background, so long local-model batches do not hold one HTTP request open. Job progress is kept in process memory for this single-worker demo; production should persist jobs and use a durable worker queue. Inspect the per-claim timeline or global **Audit trail** view for agent/tool timestamps; **Demo actions** includes denials and other decisions alongside recorded actions, and **Token usage** shows provider-reported totals. Batch processing handles claims in `new` or `error` status.
## Environment settings

| Setting | Default | Description |
|---|---|---|
| `LLM_PROVIDER` | `inhouse` | Default provider for the brief; the dashboard can switch among in-house, Groq, OpenAI, and Ollama |
| `GROQ_API_KEY` | — | Groq API key |
| `GROQ_MODEL` | `openai/gpt-oss-20b` | Default Groq model; use a model available to your account |
| `OPENAI_API_KEY` | — | Optional OpenAI key |
| `OPENAI_MODEL` | `gpt-4o-mini` | Default model if OpenAI is selected |
| `INHOUSE_LLM_BASE_URL` | — | Base URL of an OpenAI-compatible in-house chat-completions API, for example an internal `/v1` endpoint |
| `INHOUSE_LLM_API_KEY` | — | Optional key for the in-house endpoint |
| `INHOUSE_LLM_MODEL` | `in-house-model` | Model identifier accepted by the in-house endpoint |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Local Ollama server URL |
| `OLLAMA_MODEL` | `qwen3:4b-instruct` | Local model name; must already be pulled in Ollama |
| `OLLAMA_NUM_CTX` | `8192` | Context window sent to Ollama; tune lower if local memory is limited |
| `DATABASE_PATH` | `warranty_agent.db` | SQLite app database and LangGraph checkpoint storage |
| `ENABLE_DEMO_DATA_RESET` | `false` if unset (`.env.example` sets `true`) | Enables the destructive seed-data reset endpoint for trusted local demos |
| `DATA_DIR` | `data` | Folder containing the three supplied CSV files |
| `POLICY_PATH` | `policy.json` | Business policy JSON file |

Structured output depends on model support. If a selected model rejects structured output, choose a supported model/provider. Ollama must be running and the configured model must be available locally; a missing service/model is reported as a processing error, and no resolution action is executed.

## Project layout

- `api.py` — FastAPI app, request validation, REST API, and static frontend serving
- `frontend/src/` — responsive React dashboard and styles
- `warranty_agent/domain.py` — typed agent state and domain schemas
- `warranty_agent/providers.py` — configurable in-house/OpenAI-compatible, Groq, OpenAI, and Ollama adapters
- `warranty_agent/agents.py` — intake, evidence assessment, and deterministic policy agents
- `warranty_agent/workflow.py` — LangGraph state machine, conditional routing, and human pause
- `warranty_agent/action_tools.py` — idempotent simulated action tools
- `warranty_agent/data_sources.py` — CSV loading and exact-key lookup
- `warranty_agent/repositories.py` — SQLite persistence and audit history
- `warranty_agent/token_usage.py` — normalized extraction and persistence of provider token usage
- `warranty_agent/service.py` — application orchestration and graph resume
- `policy.json` — versioned business rules
- `data/` — the three supplied seed CSVs

## Persistence and security

SQLite stores customer claim text, interpretations, decisions, reviewer identity/notes, action results, event history, and LangGraph checkpoints. Protect the database and define an approved data-retention policy before production use. This local demo has no login, authentication, or role-based access; bind it to a trusted local environment only. The supplied local `.env.example` enables the destructive clear-data endpoint for demo use; set `ENABLE_DEMO_DATA_RESET=false` before exposing any deployment. Add authentication, authorization, request limits, secrets management, and deployment controls before exposing it to users or a network.

## Extension points

Implement the `ChatModelProvider` protocol to add another supported provider. Replace `SimulatedActionTools` with payment/fulfillment adapters implementing the same action-tool interface. Replace `CsvDataSource` with a service-backed repository while leaving the graph and policy interfaces unchanged.

## Official framework references

- [LangGraph workflow and human-interrupt concepts](https://docs.langchain.com/oss/python/langgraph/overview)
- [LangGraph SQLite checkpoint saver](https://github.com/langchain-ai/langgraph/tree/main/libs/checkpoint-sqlite)
- [Groq OpenAI-compatible API](https://console.groq.com/docs/openai)
- [Groq structured outputs and supported models](https://console.groq.com/docs/structured-outputs)
