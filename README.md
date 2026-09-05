# Gulf Breeze AI Platform

Production-oriented AI operations platform for Gulf Breeze Air.

The platform combines:

- FastAPI + PostgreSQL backend
- Structured SQL retrieval and pgvector semantic search
- OpenAI Responses API Agent with explicit tools
- Session-scoped conversational context and grounding
- Auditable operational actions, including appointment rescheduling
- Browser text chat and Voice Mode
- Operations dashboard with appointments, conversations, handoffs, and schedule-change history
- Live weather enrichment for scheduled field visits
- Twilio + OpenAI Realtime SIP phone integration (To be corrected)

The original Housecall Pro-style dataset remains immutable. Operational changes are stored in separate audit/overlay tables and resolved through the application layer.

## Architecture

```text
User
  ↓
Web Chat / Browser Voice / PSTN
  ↓
Agent API
  ↓
OpenAI Responses API Agent
  ↓
Explicit Tools
  ├── Structured PostgreSQL retrieval
  ├── pgvector semantic retrieval
  ├── Appointment scheduling overlay
  ├── Weather forecast
  └── Human handoff
  ↓
PostgreSQL + Agent Audit Trail
```


## Repository Structure

- `backend/`: FastAPI backend application and Python project config
- `data/`: Source CSV dataset (must remain unmodified)
- `ingestion/`: Future ingestion pipeline
- `frontend/`: Dashboard/web app
- `harness/`: Future evaluation harness

## Backend Quickstart

1. Copy and fill environment variables:

```bash
cp .env.example .env
```

Set `POSTGRES_PASSWORD` in `.env` to match your local PostgreSQL container.

2. Create a virtual environment and install dependencies:

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

3. Run the API:

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

4. Verify health:

- API health: `GET http://localhost:8000/health`
- OpenAPI docs: `http://localhost:8000/docs`

## V1.1 Data Foundation (Schema Only)

This phase introduces relational schema design and migrations only.

- No ingestion is executed.
- No source CSV rows are inserted.
- No pgvector/embeddings are created.

Run migration from `backend/`:

```bash
source .venv/bin/activate
alembic upgrade head
```

To rollback one revision:

```bash
alembic downgrade -1
```

## V1.2 Data Ingestion and Validation

This phase adds a production-oriented, idempotent ingestion pipeline from CSV files
to the normalized PostgreSQL schema.

### Run ingestion

From repository root:

```bash
backend/.venv/bin/python -m ingestion.scripts.ingest --data-dir data
```

### Supported source files

- `data/customers.csv`
- `data/employees.csv`
- `data/jobs.csv`
- `data/notes.csv`
- `data/invoices.csv`
- `data/invoice_items.csv`

### Canonical source rules

- `customers.addresses` is parsed into normalized `customer_addresses` rows.
- `jobs.techs` is parsed into `job_employees` relationships.
- `jobs.tags` is parsed into `job_tags` relationships.
- `notes.csv` is canonical; denormalized `jobs.notes` is ignored.
- Monetary values are parsed as Decimal and persisted in exact numeric columns.

### Idempotency behavior

Running ingestion repeatedly with the same source data is safe and does not increase
record counts.

### Validation performed

- source duplicate ID checks
- source referential consistency checks
- table row count checks
- post-ingestion referential integrity checks
- duplicate relation checks (`job_employees`, `job_tags`)
- canonical notes check
- monetary and quantity aggregate consistency checks

### Observed source counts (actual dataset)

- customers: 732
- employees: 23
- jobs: 1992
- notes: 6954
- invoices: 1700
- invoice_items: 4390

See [docs/data-ingestion.md](docs/data-ingestion.md) for more detail.

## V1.3.0 Vector Database Foundation

This phase adds only database primitives required for future retrieval work:

- PostgreSQL `vector` extension
- nullable `notes.embedding` column
- HNSW cosine index on `notes.embedding`

No embeddings are generated in this phase.

### Embedding dimension

The project uses a single fixed embedding dimension:

- `1536` in [backend/app/db/vector_settings.py](backend/app/db/vector_settings.py)

The migration also hardcodes `1536` for deterministic schema history.

### Apply migration

From `backend/`:

```bash
.venv/bin/alembic upgrade head
```

## V1.3.1 OpenAI Embedding Pipeline

This phase adds embedding generation and persistence for existing notes.

Scope in V1.3.1:

- provider abstraction (`EmbeddingProvider` protocol)
- OpenAI provider (`OpenAIEmbeddingProvider`)
- batched embedding generation
- retry/backoff for transient provider failures
- persistence into `notes.embedding`
- idempotent default behavior

Out of scope in V1.3.1:

- semantic retrieval
- vector search endpoints
- hybrid retrieval/reranking
- agent/tool integrations

### Environment variables

- `OPENAI_API_KEY` (required)
- `OPENAI_EMBEDDING_MODEL` (default: `text-embedding-3-small`)
- `EMBEDDING_DIMENSION` (default: `1536`)
- `EMBEDDING_BATCH_SIZE` (default: `100`)
- `EMBEDDING_MAX_RETRIES` (default: `3`)
- `EMBEDDING_TIMEOUT` (default: `30`)

### Provider contract

The provider accepts a list of texts and must return one embedding per input in
the same order.

The pipeline validates:

- returned embedding count equals input count
- each vector has exactly `EMBEDDING_DIMENSION`

Validation failures are non-retryable and fail the batch.

### Batch and transaction strategy

Default flow:

1. select notes ordered by `notes.id`
2. fetch one batch
3. request embeddings for the batch
4. validate response count and dimensions
5. persist only embeddings
6. commit batch
7. repeat

Failures are isolated at batch level. Already committed batches remain valid.

### Idempotency behavior

Default mode only processes `embedding IS NULL` notes.

- First run embeds pending notes.
- Second run (without `--force`) should process zero notes.

Use `--force` to explicitly re-embed notes.

### CLI

From repository root:

```bash
backend/.venv/bin/python -m embedding.scripts.embed_notes
```

Supported options:

- `--batch-size`
- `--limit`
- `--force`

### Test without OpenAI API key

Unit tests use fake providers and do not call OpenAI:

```bash
backend/.venv/bin/python -m unittest discover -v
```

Embedding generation is separate from CSV ingestion.

## V1.3.2 Semantic Retrieval

This phase adds semantic retrieval only, independent from future Agent/Tool
routing decisions.

Retrieval flow:

query
	-> embedding provider
	-> query vector
	-> pgvector HNSW cosine search
	-> ranked notes

### Architecture boundary

Two retrieval concepts are now explicit:

- `StructuredRetrieval` (concept only for now, implementation deferred)
- `SemanticRetrieval` (implemented in this phase)

Routing between SQL and vector retrieval is intentionally deferred to V1.4,
where an Agent layer will orchestrate tool selection.

### Semantic retrieval interface

`PgVectorSemanticRetriever.search(query, top_k=10, customer_id=None, job_id=None)`

Result type:

- `note_id`
- `job_id`
- `customer_id`
- `content`
- `similarity_score`

### Behavior

- query is embedded exactly once per search request
- `notes.embedding IS NULL` rows are excluded from search
- cosine ranking via pgvector (`ORDER BY embedding <=> query_embedding`)
- deterministic ordering: similarity descending, then `notes.id` ascending
- filters are applied in SQL:
	- optional `customer_id`
	- optional `job_id`
	- if both are provided, both are applied
- top-k constraints:
	- `top_k >= 1`
	- bounded by `SEMANTIC_MAX_TOP_K` (default `50`)
- no similarity threshold in V1.3.2

### Environment variables

- `SEMANTIC_MAX_TOP_K` (default: `50`)

### Run semantic retrieval

From repository root:

```bash
backend/.venv/bin/python -m retrieval.scripts.search_notes "previous refrigerant leak" --top-k 5
```

Filter examples:

```bash
backend/.venv/bin/python -m retrieval.scripts.search_notes "previous AC issues" --top-k 5 --customer-id cus_123
backend/.venv/bin/python -m retrieval.scripts.search_notes "compressor problem" --top-k 5 --job-id job_123
```

### Testing

Semantic retrieval tests use fake embedding providers and do not require OpenAI
credentials.

## V1.4 Agent and Tool Foundation

This phase introduces a read-oriented office assistant agent that answers Gulf
Breeze Air questions through explicit tools.

Architecture:

User
	-> Agent
	-> Explicit tools
		 - search_customers
		 - get_customer
		 - get_customer_jobs
		 - get_job
		 - get_customer_balance
		 - get_upcoming_appointments
		 - get_job_invoice
		 - semantic_search_notes
	-> Structured repositories or semantic retriever
	-> Agent response

### Key boundaries

- Tool-first architecture: the agent cannot query SQL directly.
- No arbitrary SQL generation from user input.
- Structured facts come from structured tools.
- Free-text service history comes from semantic notes retrieval.
- Only audit tables are writable by the agent workflow:
	- `agent_sessions`
	- `agent_actions`

### Structured vs semantic responsibilities

Use structured tools for:

- customer identity and profile
- schedules and appointment timing
- job and employee assignment details
- balances and invoice values

Use semantic search for:

- technician observations
- diagnoses and symptoms
- recurring free-text issues in service notes

No similarity threshold is applied in V1.4 semantic tool usage.

### Multi-tool execution

The agent supports multi-step tool calling with a bounded loop.

- tool-call round limit: `AGENT_MAX_TOOL_ROUNDS` (default `8`)
- if the safe bound is exceeded, a handoff result is produced

### Human handoff behavior

The agent can signal handoff for unsupported mutation requests and unresolved
operational cases. Handoff state is returned in the agent result and persisted
in the session status.

### Agent audit tables

Each run creates an `agent_sessions` row. Each tool execution writes an
`agent_actions` row including action type, arguments, result, status, and
latency.

### Environment variables

- `OPENAI_AGENT_MODEL` (default: `gpt-5.4-mini`)
- `AGENT_MAX_TOOL_ROUNDS` (default: `8`)

### CLI usage

From repository root:

```bash
backend/.venv/bin/python -m app.agents.scripts.chat "When was Robert Smith's last service?"
```

Interactive mode:

```bash
backend/.venv/bin/python -m app.agents.scripts.chat
```

## V1.4.1 Session Context and Grounding Improvements

This phase adds short-term, session-scoped conversation continuity while keeping
the V1.4 read-oriented tool architecture.

Flow:

User turn
		-> Agent
		-> Session context
			 - active customer
			 - last job
			 - last invoice
			 - recent note references
			 - recent messages
		-> Tools
		-> Context update
		-> Next user turn

### Session-scoped context

Context is persisted in `agent_sessions.metadata` under
`conversation_context` (versioned payload).

Stored context includes:

- `customer_id`
- `customer_display_name`
- `last_job_id`
- `last_invoice_id`
- ordered `recent_note_references`
- ordered `recent_job_references`
- bounded recent messages

This context is scoped to one Gulf Breeze session only. There is no cross-session
long-term memory.

### Message history bounds

The model receives bounded session history plus current turn.

- `AGENT_MAX_HISTORY_MESSAGES` (default `20`)

Older messages are trimmed; no summarization agent is used in this phase.

### Numbered note references

Semantic results are retained with deterministic positions, enabling follow-ups
such as:

- `note 1`
- `the first one`
- `the second one`
- `that visit`
- `that invoice`

When a customer switch is resolved, customer-specific references are cleared to
prevent stale context leaks across customers.

### Grounding improvements

The V1.4.1 prompt and response safeguards enforce stricter record-grounded
behavior:

- avoid unsupported HVAC causal claims
- avoid turning uncertain notes into confirmed diagnoses
- separate explicit evidence from uncertainty
- keep company-record answers grounded in tool results

### Session lifecycle behavior

- Single-turn mode: session typically ends as `completed`/`handoff`/`failed`.
- Interactive mode: one session is reused across turns and remains `active`
	until the CLI exits, then it is marked `completed`.

### CLI behavior

`python -m app.agents.scripts.chat` now creates one session and reuses it for
all interactive turns. The session ID is shown once for debugging.

## V1.4.2 Deterministic Note Follow-up

This patch makes note follow-ups deterministic when an exact note reference is
already known from session context.

Behavior:

- If the user references a known note position (for example, note 1, first one,
	second one, or that note), the orchestrator resolves position to note ID from
	`recent_note_references`.
- If the model requests `semantic_search_notes` in that situation, the
	orchestrator coerces the call to `get_note` with the resolved `note_id`.
- For direct `get_note` calls with missing arguments, resolved note reference
	takes precedence over generic fallback defaults.

Result:

- No redundant semantic re-query for known-note follow-ups.
- Deterministic mapping: position -> note ID -> exact note fetch.
- Structured handled error envelope for missing notes:
	`{ "ok": false, "error": { "code": "note_not_found", "message": "..." } }`.

## Evidence-Aware Grounding

Semantic relevance is not factual confirmation.

Service-note evidence is interpreted as one of the following:

- confirmed
- explicitly not found / denied
- suspected / possible
- related but not confirming
- insufficient

Example:

- "system may be low on refrigerant" must not become "confirmed refrigerant leak".

## OpenAI Responses API

The OpenAI AgentModel adapter now uses the Responses API instead of Chat
Completions.

- Tool calling is translated into Responses API function tools.
- Application session state remains Gulf Breeze-owned.
- `previous_response_id` is intentionally not used.
- `store=True` is enabled on OpenAI agent requests.
- The AgentModel abstraction keeps provider-specific behavior isolated.

## V1.5 Agent API

This phase adds a thin HTTP boundary over the existing agent core so the agent
can be exercised as a service without changing its orchestration rules.

### Endpoints

- `POST /agent/chat`
- `POST /agent/sessions`
- `GET /agent/sessions/{session_id}`
- `GET /agent/sessions/{session_id}/actions`
- `POST /agent/sessions/{session_id}/close`

### Chat request

```bash
curl -X POST http://localhost:8000/agent/chat \
	-H 'Content-Type: application/json' \
	-d '{"message":"How much does Tamara Cruz owe?"}'
```

Pass `session_id` on later turns to reuse the same conversation. Set `debug` to
`true` when you want tool-call details and active context identifiers returned
in the response.

### Session inspection

The session routes expose the persisted session record and action audit trail so
operators can inspect how a turn was handled without touching the database
directly.

## V1.5 Evaluation Harness

The harness under `harness/` runs deterministic scenarios against the agent.
Scenario files are JSON-compatible YAML and can describe scripted model output,
tool fixtures, and assertions.

### Sample scenario

```bash
backend/.venv/bin/python -m harness.runner.runner harness/scenarios/sample_balance_lookup.yaml
```

### Real mode

Use `--mode real` to execute a scenario against the live default agent instead
of the scripted fake model:

```bash
backend/.venv/bin/python -m harness.runner.runner --mode real harness/scenarios/sample_balance_lookup.yaml
```

### Result output

Add `--output` to write a JSON result file for CI or later comparison.

## V1.6 Voice Agent (Twilio + OpenAI Realtime)

V1.6 adds phone-call integration while keeping Gulf Breeze business intelligence
in the existing Agent API and Agent Core.

## Known Limitation

The PSTN phone integration through Twilio Elastic SIP Trunking and OpenAI Realtime SIP is implemented, but it is currently affected by a reproducible provider-side session lifecycle issue.

Inbound calls successfully reach the OpenAI `realtime.call.incoming` webhook, and the call acceptance request succeeds. However, the associated Realtime SIP session is subsequently terminated or becomes unavailable, causing the sideband connection to fail with `call_id_not_found`.

The issue has been isolated to the Twilio ↔ OpenAI Realtime SIP path after validating the project configuration, call identifiers, credentials, webhook flow, acceptance timing, retry behavior, and secure trunking settings.

A similar recent issue has also been reported in the OpenAI Developer Community:
https://community.openai.com/t/realtime-api-sip-accept-endpoint-takes-5-3s-to-return-404-call-id-not-found/1394315

As a result, the PSTN phone number should currently be considered experimental.

The same Agent Core remains fully functional through:
- Web text chat
- Browser Voice Mode using STT/TTS

Both interfaces use the same backend session, tool-calling, retrieval, scheduling, and operational logic as the phone integration.

Architecture:

Caller
		-> Twilio phone number
		-> Twilio Elastic SIP Trunk
		-> OpenAI Realtime API
		-> Voice Gateway
		-> Gulf Breeze Agent API (`/agent/chat`)
		-> Existing Agent Core

### Responsibility boundary

- OpenAI Realtime is voice I/O only.
- Business reasoning, grounding, and tool selection stay in Gulf Breeze Agent
	Core.
- Voice turns are forwarded as completed transcripts to `POST /agent/chat`.
- One phone call maps to one Agent session ID for full multi-turn continuity.

### Voice endpoint

- `POST /voice/openai/webhook`

Supported webhook event types in this phase:

- `realtime.call.incoming`

After accepting the incoming call, the backend opens a server-side Realtime
sideband connection associated with that `call_id`. Completed caller
transcripts and disconnect/lifecycle events are consumed from that active
Realtime connection, not from webhook POST callbacks.

Optional read-only debug endpoints:

- `GET /voice/calls/{id}`
- `GET /voice/calls`

### Voice call persistence

V1.6 introduces `voice_calls` mapping records for telephony lifecycle state.

Stored fields include:

- provider and provider call ID
- mapped Gulf Breeze Agent session ID
- caller identifier when present
- status (`ringing`, `active`, `handoff`, `completed`, `failed`)
- timestamps and metadata

### Markdown-to-speech sanitization

Before speaking Agent responses, the gateway removes minimal markdown artifacts,
for example:

- `**text**` -> `text`
- ``text`` -> `text`

This prevents phone output from reading formatting symbols aloud while
preserving factual content.

### Voice environment variables

- `OPENAI_API_KEY`
- `OPENAI_PROJECT_ID`
- `OPENAI_REALTIME_MODEL` (default: `gpt-4o-realtime-preview`)
- `OPENAI_REALTIME_VOICE` (default: `alloy`)
- `VOICE_AGENT_ENABLED` (default: `false`)
- `VOICE_AGENT_API_BASE_URL` (default: `http://localhost:8000`)
- `VOICE_AGENT_API_TIMEOUT_SECONDS` (default: `12`)
- `VOICE_REALTIME_TIMEOUT_SECONDS` (default: `10`)
- `TWILIO_PHONE_NUMBER`
- `TWILIO_ACCOUNT_SID`
- `TWILIO_AUTH_TOKEN`
- `TWILIO_SIP_TRUNK_SID`

Twilio values are configuration references for deployment and operations in this
phase; the backend does not require every Twilio secret for local unit tests.

### Twilio manual setup

1. Create or sign in to a Twilio account.
2. Buy a phone number with Voice capability.
3. Create an Elastic SIP Trunk (for example, Gulf Breeze OpenAI).
4. Configure Origination URI:

```text
sip:<OPENAI_PROJECT_ID>@sip.api.openai.com;transport=tls
```

5. Attach the purchased phone number to the SIP trunk.
6. Configure OpenAI Realtime webhook delivery to:

```text
https://<PUBLIC_BACKEND>/voice/openai/webhook
```

### Local development tunnel

Because OpenAI webhook delivery must reach a public HTTPS endpoint, expose your
local backend with a tunnel during development.

Example:

```bash
ngrok http 8000
```

Then set the HTTPS forwarding URL in your Realtime webhook configuration.

### Manual phone flow checklist

After Twilio and webhook configuration are active:

1. Place a call to the configured Twilio number.
2. Ask: How much does Tamara Cruz currently owe?
3. Ask: Has she had refrigerant leaks before?
4. Ask: Tell me more about note one.
5. Ask: Does she have any upcoming appointments?
6. End the call and confirm one voice-call record maps to one Agent session
	 with preserved action history.

## V1.7 Web Demo (Single-Origin Chat + Voice + Ops Dashboard)

V1.7 adds a browser experience intended for live demos:

- customer-facing AI chat at `/chat`
- browser Voice Mode (speech recognition + speech synthesis) in `/chat`
- office operations dashboard at `/dashboard`
- session visibility and action trace views at:
	- `/dashboard/sessions`
	- `/dashboard/sessions/{session_id}`
	- `/dashboard/handoffs`
    - `/dashboard/appointments`
    - `/dashboard/schedule-changes`

The backend serves the built frontend static assets so one public origin can be
shared in interviews or stakeholder demos.

V1.7 operational enhancement also includes:

- appointment rescheduling via Agent tool `reschedule_appointment`
- immutable source schedule policy: imported `jobs` rows are never edited
- auditable schedule overlay in `job_schedule_overrides`
- effective schedule reads across tools and dashboard
- weather enrichment for upcoming appointments via Open-Meteo (server-side)

### Local development workflow

1. Start backend API:

```bash
cd backend
source .venv/bin/activate
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

2. In a second terminal, run frontend dev mode (hot reload):

```bash
cd frontend
npm install
npm run dev
```

Use `http://localhost:5173` during UI development.

### Demo/production-style single-origin run

1. Build frontend assets:

```bash
cd frontend
npm run build
```

2. Start backend:

```bash
cd backend
source .venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

3. Open the app from backend origin:

- `http://localhost:8000/chat`
- `http://localhost:8000/dashboard`

### ngrok one-tunnel demo

Expose only the backend port. The backend serves API + web app from one origin:

```bash
ngrok http 8000
```

Use the generated HTTPS URL as your single public demo link.

If you also demo phone calls, point OpenAI/Twilio webhook configuration to:

- `https://<ngrok-host>/voice/openai/webhook`

### Weather enrichment settings

- `WEATHER_TIMEOUT_SECONDS` (default `3`)
- `WEATHER_CACHE_TTL_SECONDS` (default `2700`)

## Notes

- PostgreSQL is the system of record.
- Credentials are loaded from environment variables only.
- Ingestion uses CSV as the primary source format.
- The dataset in `data/` is not modified.
