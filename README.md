# Legacy Code Automation

An AI-assisted modernization platform that takes a legacy **COBOL** repository to **Python** without losing business behaviour.

> **Understand before transforming. Verify before accepting. Preserve existing business behaviour throughout modernization.**

## Clone and run

### Prerequisites

* **Python 3.10+** (available as `python` on Windows or `python3` on macOS/Linux)
* **Node.js 18+** and npm
* Git (only needed to clone the repository)

### Start the application

```powershell
git clone https://github.com/Benedictpatrick/shrikanth-project.git
cd shrikanth-project

# Windows PowerShell -- creates the Python virtual environment, installs Python
# and UI dependencies, builds the UI, and starts the app at http://127.0.0.1:8000
.\start.ps1
```

```sh
git clone https://github.com/Benedictpatrick/shrikanth-project.git
cd shrikanth-project

# macOS / Linux -- same one-time setup and launch
./start.sh
```

Open <http://127.0.0.1:8000>, choose the bundled `legacy_bank` sample (or upload a `.zip` / point at a folder) and press **Start analysis**. Stop the server with `Ctrl+C`.

If a launcher cannot find Python or npm, install the prerequisite, close and reopen your terminal, then run it again. The launchers only install dependencies and build the UI when those artifacts are absent; to refresh them manually, use `backend\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt` and `cd frontend; npm install; npm run build` on Windows (use the equivalent `python3` paths on macOS/Linux).

### Optional: enable LLM assistance

Copy `.env.example` to `.env`, set `GROQ_API_KEY`, then restart the server. You can also paste a key in the UI; that copy is held in memory only.

## Quick start

```powershell
# Windows
.\start.ps1
# macOS / Linux
./start.sh
```

Everything except the LLM-specific steps works without a key.

## What it does

```
ingest → analyze → understand → baseline → plan → [HUMAN: approve plan]
       → ( migrate → verify/repair → [HUMAN: approve unit] → git commit ) × N → report
```

| Stage | What happens | Engine |
|---|---|---|
| Analyze | Fixed/free-format reader, COPY expansion, structural parser (data division, FDs, statement AST), dependency graph, metrics, security scan | deterministic |
| Understand | Business rules extracted from the AST, then interpreted in plain language; per-program technical docs | static + Groq LLM |
| Baseline | Behavioural oracle for each program (see below) | `cobc` → fixtures → LLM |
| Plan | Dependency-ordered migration plan with per-unit risks and decisions | deterministic + LLM rationale |
| Migrate | Rule-based COBOL→Python generator on top of a tested COBOL runtime | deterministic |
| Verify / repair | Run the migrated program in a subprocess against the baseline, byte-for-byte; failures go to the LLM for targeted repair (max `MAX_REPAIR_ATTEMPTS`) | pytest-style harness + Groq LLM |
| Commit | Approved units land in an **output git repo**, one commit per unit, with tests and docs | git |

### The behavioural oracle (read this)

Equivalence can only be as trustworthy as the thing you compare against. The platform picks the strongest available oracle per program and **shows it in the UI and in every commit message**:

1. `cobc` – the original COBOL is compiled with GnuCOBOL and executed; its output is the truth.
2. `fixture` – golden cases shipped with the repo (`tests/<PROGRAM>.json`). The bundled sample's fixtures are computed independently of the runtime (`samples/legacy_bank/tests/_build_fixtures.py`).
3. `llm-unverified` – the LLM predicted the expected output. Units that only pass this are marked **provisional**, never "verified", and always need a human.
4. `none` – no baseline; the unit is marked unverified.

A unit that fails or still contains `NotImplementedError` stubs cannot be committed without an explicit *force* decision, which is recorded in the commit message.

### Groq rate limits

Groq limits requests/minute, tokens/minute and requests/tokens per day, and the numbers depend on plan and model. The provider (`backend/app/llm/provider.py`) is built around that:

* **Self-throttling** - a sliding 60 s window over requests *and* tokens (budgets set in Settings: `GROQ_RPM` / `GROQ_TPM`); a call waits until it fits instead of being rejected.
* **Prompt shaping** - every agent sizes its prompt to the token budget (`prompt_char_budget`) and keeps `max_tokens` small; an over-large request gets one compact retry rather than failing.
* **Server hints** - `retry-after` and `x-ratelimit-*` headers are honoured; 429/5xx are retried with bounded back-off and jitter; failed requests refund their token reservation.
* **Quota circuit breaker** - if the daily quota is gone (or the wait would exceed 90 s) the provider stops calling and reports unavailable for a while. Every agent already has a deterministic fallback, so the run **continues** (static rules/docs, rule-based translation) instead of stalling; units that needed the LLM are marked blocked with the reason.
* **Visibility** - waits appear in the run's Activity log; Settings shows live requests/tokens per minute, totals, time spent waiting, daily requests left and a *Retry now* button for the breaker.

Defaults (25 req/min, 12 000 tokens/min) are conservative for a free-tier 70B model; raise them if your plan allows more.
### Why a runtime library

COBOL semantics (PIC-sized truncation, ROUNDED only when asked, COMP-3 packed decimals, overpunched signs, numeric-edited pictures, group moves, REDEFINES, fixed-width record I/O, FILE STATUS) are implemented **once** in `backend/app/runtime/cobol_runtime.py` (standard library only, 43 unit tests) and shipped with every migrated program. Generated Python is thin control flow over that runtime, so translations are reviewable and the LLM is never trusted to re-derive numeric semantics.

### Supported COBOL subset

`MOVE`, `COMPUTE`, `ADD`/`SUBTRACT`/`MULTIPLY`/`DIVIDE` (incl. `GIVING`, `ROUNDED`, `ON SIZE ERROR`), `IF`/`ELSE`, `EVALUATE` (single subject, `TRUE`, `THRU`, `OTHER`), `PERFORM` (paragraph, `THRU`, `UNTIL`, `TIMES`, `VARYING`, inline, `TEST AFTER`), `DISPLAY`, `ACCEPT`, `OPEN`/`CLOSE`/`READ`/`WRITE` on sequential and line-sequential files, `INITIALIZE`, `SET … TO TRUE`, level-88 conditions, `OCCURS`, `REDEFINES`, `COPY`, DISPLAY/COMP/COMP-3/COMP-5 numerics, numeric-edited pictures.

Anything else (`STRING`, `INSPECT`, `CALL`, `GO TO`, `SORT`, indexed files, `OCCURS DEPENDING ON`, intrinsic functions, …) is **flagged during analysis**, emitted as a `NotImplementedError` stub, and routed to the LLM repair loop – never silently dropped. The bundled `NAMEFMT` program uses `STRING`/`INSPECT` to demonstrate this path.

## Architecture

```
frontend/              React + TypeScript (Vite), styled with GitHub Primer tokens (dark/light) – migrations list, run page, plan/unit review, output browser
backend/app/
  cobol/               source reader, parser, project graph/metrics, code generator
  runtime/             cobol_runtime.py – the COBOL semantics library shipped with outputs
  agents/              security, rules, docs, planner, testgen, translator (+repair loop)
  llm/                 provider interface + Groq implementation (retry/backoff, JSON mode)
  verification/        subprocess harness, comparison, optional cobc oracle
  graph/               LangGraph pipeline, SQLite checkpointer, run store, git ops, run service
  main.py              FastAPI REST + SSE
samples/legacy_bank/   3 COBOL programs, 1 copybook, golden fixtures
```

* **Human checkpoints** are LangGraph `interrupt()`s persisted by a SQLite checkpointer, so a run waiting for approval survives a server restart (tested: kill the server at plan approval, restart, approve, run completes). A run that was mid-execution when the server died is marked failed on the next start and has to be re-run.
* **Provider interface**: agents depend on `LLMProvider`; Ollama/Anthropic can be added next to `GroqProvider` without touching the agents.
* **Privacy**: sources never leave the machine unless a Groq key is configured; then only the program text and failing diffs of the unit being processed are sent.

### API

`POST /api/runs` · `GET /api/runs/{id}` · `GET …/analysis|plan|report|units/{prog}|baseline/{prog}` · `POST …/plan/decision` · `POST …/units/{prog}/decision` · `GET …/stream` (SSE) · `GET …/output/tree|file|archive` · `GET /api/health` · `POST /api/settings/llm`. Interactive docs at `/docs`.

## Development

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest          # 71 tests: runtime, rate limiting, parser, translator, repair loop, full API pipeline
cd ..\frontend
npm run dev                                   # UI on :5173, proxies /api to :8000
```

Adding a golden test for your own program: drop `tests/<PROGRAM-ID>.json` into the repo:

```json
[{"name": "basic", "inputs": {"ACCOUNTS.DAT": "…fixed-width records…\n"},
  "expected": {"stdout": "…", "files": {"POSTED.DAT": "…"}}}]
```

## Known limits (honest list)

* Verification of translated code runs it in a local subprocess with a timeout. That is process isolation, not a security sandbox; run untrusted repos in a container/VM.
* Signed DISPLAY numerics use GnuCOBOL's ASCII overpunch convention (`p`–`y` for negative); EBCDIC conventions are not modelled. `DISPLAY` of numerics follows GnuCOBOL's "pretty display".
* One program per source file; nested programs, `CALL` interfaces (`LINKAGE`/`USING`), indexed/relative files, SQL (`EXEC SQL`) and CICS are out of scope for the rule-based generator.
* Without `cobc` or fixtures the baseline is LLM-predicted and explicitly labelled unverified.
* The LLM repair path has been exercised against a scripted stand-in model in tests; it has not been run against live Groq in this repository's CI.


