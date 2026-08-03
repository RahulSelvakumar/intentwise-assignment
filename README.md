# Generic Data Ingestion Service

A config-driven ingestion service that pulls data from arbitrary REST APIs and
persists it to a database — **adding a new data source requires writing a
YAML file, not new code.**

Built for the Intentwise "AI-Native Software Engineer" take-home assignment.

---

## 1. How to run it

Requires Docker + Docker Compose only (no local Python/Postgres install
needed).

```bash
git clone <this-repo>
cd intentwise-ingestion
cp .env.example .env
# edit .env and add GITHUB_TOKEN if you want authenticated GitHub calls
# (works unauthenticated too, at a lower rate limit)

docker compose up -d --build
```

This starts two containers: `db` (Postgres 16) and `app` (FastAPI on
`:8000`). The app waits for Postgres to be healthy, then creates its schema
automatically on startup — no manual migration step.

Trigger ingestion and observe it:

```bash
# list configured sources (auto-loaded from sources/*.yaml)
curl http://localhost:8000/sources

# trigger a run
curl -X POST http://localhost:8000/sources/github_issues/ingest
curl -X POST http://localhost:8000/sources/rickandmorty_characters/ingest
curl -X POST http://localhost:8000/sources/nasa_apod/ingest

# check a run's status/metrics
curl http://localhost:8000/runs/1

# sample the records it actually stored
curl http://localhost:8000/runs/1/records
```

Interactive API docs: `http://localhost:8000/docs`.

Run tests (inside the app container, or locally with `pip install -r requirements.txt`):

```bash
docker compose exec app pytest -q
```

### Adding a new source (no code changes)

Drop a new YAML file into `sources/` and restart the app container (or add a
`/reload-sources` endpoint — noted under "with more time" below). See
`sources/*.yaml` for three working examples with different auth and
pagination styles.

---

## 2. Public APIs used for the demo

Three sources, chosen to be structurally different on **both** the auth axis
and the pagination axis, to prove the design isn't tailored to one API shape:

| Source | Auth style | Pagination style | Response shape |
|---|---|---|---|
| **GitHub REST API** (`/repos/octocat/Hello-World/issues`) | Bearer token (`Authorization: Bearer <token>`) | `Link` HTTP header (`rel="next"`) | Bare top-level JSON array |
| **Rick and Morty API** (`/api/character`) | None (public) | Next-page URL embedded in the JSON body (`info.next`) | `{ "info": {...}, "results": [...] }` |
| **NASA APOD API** (`/planetary/apod`) | API key in a query parameter | None (single call returns a fixed batch via `count`) | Bare top-level JSON array |

(WeatherAPI was the original planned third source to demonstrate the
api-key-in-query-param style; its key was still going through activation
during the build window, so NASA's APOD API — which uses the same auth
style and is instantly usable via the public `DEMO_KEY` — was substituted.
Swapping it back is a one-file change: replace `sources/nasa_apod.yaml`
with a `weatherapi.yaml` using the same `api_key_query` auth type.)

All three ran through the *same* orchestrator code with zero source-specific
`if github / elif rickandmorty` branching anywhere in the codebase.

---

## 3. Architecture

```
sources/*.yaml  ──►  SourceConfig (pydantic)
                          │
                          ▼
                 IngestionOrchestrator
        ┌───────────┬───────────┬────────────┬──────────────┐
        ▼           ▼           ▼            ▼              ▼
   AuthStrategy  Pagination  HttpFetcher  ResponseExtractor  DestinationAdapter
   (attach       Strategy    (retries,    (jmespath: find    (upsert into
    creds)       (compute    backoff,      record list +      Postgres/
                 next page)  rate limits)  external id)       SQLite; S3
                                                                stub for
                                                                extensibility)
```

**Key design decision: strategy pattern for auth and pagination.**
Each is an abstract base class with a `factory(config) -> instance` function
keyed off a `type` enum in the YAML. Adding a *new API* that uses an
*existing* auth/pagination style (e.g. another bearer-token, Link-header API)
is a pure-config change. Only a genuinely new auth or pagination *mechanism*
requires a new class — and even then, only in one isolated file
(`app/auth.py` or `app/pagination.py`), never in the orchestrator.

**Schema-agnostic storage.** `raw_records` stores the entire response
payload as a JSON column, plus four lineage columns (`source_name`,
`external_id`, `run_id`, timestamps). This means the database schema never
needs to change when a new source with a different shape is added — the
generality lives in config + extraction, not in per-source tables.

**Idempotent upserts.** A unique constraint on `(source_name, external_id)`
plus dialect-specific `ON CONFLICT DO UPDATE` means re-running ingestion
(scheduled re-syncs, retries after a partial failure) never creates
duplicates — it updates in place.

**Centralized resilience.** All retry/backoff/rate-limit/timeout logic lives
in one `HttpFetcher` class used by every source, so every new source
automatically benefits from:
- Exponential backoff with jitter, retrying only on 429/5xx/timeouts
  (never on other 4xx — those are non-transient client errors).
- `Retry-After` handling (supports both delta-seconds and HTTP-date formats).
- Proactive throttling on `X-RateLimit-Remaining` / `X-RateLimit-Reset`
  headers, so we slow down *before* hitting 429, not just after.
- A concurrency cap (semaphore) and a descriptive `User-Agent` header, so the
  service is a good citizen against real production APIs.

**Run tracking & partial-failure tolerance.** Every ingestion is recorded as
an `IngestionRun` row: pages fetched, records fetched/saved/skipped, the last
successful page URL as a checkpoint, and any error. A single malformed
record (e.g. missing the configured `external_id_path`) is logged and
skipped rather than aborting the whole run; a page-level or network-level
failure marks the run `partial` (if some records were already saved) or
`failed` (if none were), rather than crashing the process.

**Pagination infinite-loop guard.** Every `PaginationConfig` has a
`max_pages` safety cap, and the orchestrator also stops if a page returns
zero records — protects against a misbehaving or misconfigured API looping
forever.

---

## 4. Tradeoffs and assumptions

- **Postgres in Docker, SQLite as a zero-setup fallback.** `DATABASE_URL`
  defaults to local SQLite for a dependency-free quick start; docker-compose
  wires up real Postgres for anything resembling production use.
- **Two structurally different demo APIs (plus a third) rather than many.**
  Depth over breadth — the assignment explicitly rewards proving generality,
  not maximizing source count.
- **jmespath for all path-based config** (`records_path`, `external_id_path`,
  `next_url_path`, `has_more_path`) rather than a bespoke DSL. It's a mature,
  well-tested library and keeps config declarative and source-author-friendly.
- **S3 destination is a stub, not implemented.** `DestinationConfig.type`
  already supports `s3` in the schema and `DestinationAdapter` is an
  interface with one concrete implementation (`RelationalDestination`) — the
  extension point exists and is exercised by the config layer, but writing
  actual boto3 code was deprioritized in favor of the ingestion side, which
  is where the assignment's grading emphasis clearly sits ("how well you
  handle the realities of pulling data from live APIs").
- **No auth-refresh flow (e.g. OAuth2 token refresh).** Bearer/API-key/Basic
  cover the two demo APIs and the common cases; a refreshable-OAuth strategy
  would be the next one to add if a target API needed it.
- **Synchronous-per-run execution**, not a background task queue. Ingestion
  runs happen inline within the request that triggers them. Fine for a
  demo/take-home; a real system would hand off to Celery/RQ/an async worker
  so `POST /sources/{name}/ingest` returns immediately and the run proceeds
  in the background.
- **Sources are loaded from disk once at startup**, not hot-reloaded. Adding
  a YAML file requires an app restart (`docker compose restart app`) to be
  picked up — acceptable for a demo, but a `/sources/reload` endpoint would
  remove even that friction (see below).

---

## 5. What I'd do with more time

- Implement the real S3 `DestinationAdapter` (e.g. writing partitioned
  Parquet/JSON files with a manifest) to actually prove destination
  extensibility, not just the type stub.
- Add a `POST /sources/reload` endpoint (or file-watcher) so new YAML
  configs are picked up without a restart.
- Move ingestion runs onto a background task queue (Celery/RQ/arq) with a
  scheduler (e.g. APScheduler or a cron trigger) for recurring syncs, rather
  than only supporting on-demand triggering via the API.
- Add an OAuth2-with-refresh auth strategy for APIs that need it.
- Add structured logging/metrics (e.g. OpenTelemetry) instead of stdlib
  `logging`, and expose Prometheus-style metrics for run duration, error
  rates, and records/sec per source.
- Add a config validation CLI (`python -m app.validate sources/new.yaml`) so
  a new source's YAML can be sanity-checked (schema + a live dry-run against
  its first page) before wiring it into the running service.
- Host the service somewhere reachable (Render/Fly.io/Railway) instead of
  relying solely on docker-compose, per the assignment's stated preference
  for a hosted endpoint.
- More exhaustive tests: property-based tests for the pagination strategies
  (e.g. via Hypothesis) and a full end-to-end test that mocks a multi-page
  API via `respx` and asserts the final DB state.

---

## 6. AI usage note

This project was built with AI pair-programming assistance (GitHub Copilot
CLI) for scaffolding, boilerplate, and design discussion, with all resulting
code reviewed, run, and debugged by me before being considered "done."

**A concrete place the AI got something wrong, and how I caught it:**

While wiring up the `HttpFetcher` class, the AI set a default `User-Agent`
header on the shared `httpx.AsyncClient` instance (`self._client = httpx.AsyncClient(headers={"User-Agent": ...})`), on the assumption that `client.send(request)`
would automatically merge the client's default headers onto any request it
sends. That assumption is **wrong** — `httpx.Client.send()` only merges
default headers for requests built via `client.build_request(...)` /
`client.get(...)` /etc.; a manually-constructed `httpx.Request()` passed
straight to `send()` goes out exactly as built, with no `User-Agent` at all.

This didn't show up in isolation — it only surfaced once I ran the actual
demo against the live GitHub API inside Docker: the Rick and Morty source
ingested fine (200 records), but GitHub returned:

```
403 Forbidden: "Request forbidden by administrative rules. Please make sure
your request has a User-Agent header..."
```

My first instinct was to suspect the bearer token or its scopes. I isolated
the problem by testing the exact same request three ways from inside the
running container: (1) a bare `httpx.get()` with headers set by hand →
`200 OK`; (2) building the request the way our own `auth.py` builds it and
inspecting the resulting `Authorization` header directly → correct token,
correct format; (3) running it through the actual `HttpFetcher.fetch()` path
→ `403`. That isolated the bug precisely to the fetcher, not the token or
the auth strategy, and inspecting `HttpFetcher` showed the real cause
immediately.

**Fix:** `HttpFetcher.fetch()` now explicitly merges the client's default
headers onto the outgoing request (via `request.headers.setdefault(...)`)
before sending, so every request is guaranteed to carry the `User-Agent`
(and any other client-level default) regardless of how the request object
was constructed — while still letting a source-specific header (like
`Authorization`) take precedence if already set.

This is exactly the kind of "realities of live APIs" issue the assignment
calls out — it's invisible in unit tests with mocked responses and only
appears against a real, production HTTP server with its own defensive rules,
which is why the demo was run against genuinely live public APIs rather than
stopping at mocked tests.

---

## 7. Repository access

Per the assignment, `hrintentwise` should be given read-only access to this
repository once it's pushed to a remote (e.g. GitHub).
