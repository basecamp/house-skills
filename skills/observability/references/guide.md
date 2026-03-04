# Observability Guide

Investigate production issues at 37signals using Grafana (Prometheus, Loki, Tempo) and Sentry.

---

## App Identifiers

All apps deploy via Kamal: `bin/kamal deploy -d <destination>`

| App | Repo | Branch | Prometheus | Loki | Sentry Project | Destinations |
|-----|------|--------|-----------|------|----------------|-------------|
| HEY | haystack | master | `hey` | `hey` | `haystack` | production, staging, beta, beta1–8 |
| Basecamp (BC4) | bc3 | master | `bc4` | `bc4` | `bc3` | production, rollout, staging, beta, beta1–10, five |
| Fizzy | fizzy | main | `fizzy` | `fizzy` | `fizzy` | production, staging, beta, beta1–4 |
| Launchpad | launchpad | master | `launchpad` | `launchpad` | `launchpad` | production, staging, beta |
| Queenbee | queenbee | master | `queenbee` | `queenbee` | `queenbee` | production, staging, beta |

**Fizzy note:** Run `bin/rails saas:enable` before deploying (switches OSS → SaaS mode).

---

## Grafana Datasources

| Name | UID | Use |
|------|-----|-----|
| Thanos (Prometheus) | `PC96415006F908B67` | Metrics, latencies |
| Loki | `e38bdfea-097e-47fa-a7ab-774fd2487741` | Application logs |
| Tempo | `6PouH8j4z` | Distributed traces |

---

## Investigation Workflow

Match the starting point to the question type:

| Question type | Start with |
|---------------|-----------|
| "Recent issues / what's broken?" | Sentry → Loki |
| "Is it slow / what's the latency?" | Prometheus → Loki |
| "Why is this specific request slow?" | Loki → Tempo |
| "What errors are users seeing?" | Sentry |

```
Sentry    — What errors? How many users affected?
Metrics   — What's the latency shape? Any spike?
Logs      — What's the detail? Which endpoints/users?
Traces    — Where is the time going in a specific request?
```

Work through each layer as needed. Stop when you have enough to answer the question.

---

## 1. Prometheus Metrics

### Key queries

```promql
# Request latency percentiles
rails_request_duration_seconds_bucket:rate1m:sum_by_app:quantiles{app="APP"}

# Request rate by controller/action
rails_request_total:rate1m:sum_by_controller_action{app="APP"}
```

Replace `APP` with the app's Prometheus identifier (e.g., `hey`, `bc4`, `fizzy`).

### Tips
- Use **instant queries** to probe current state — range queries over >1h return large payloads and may overflow token limits
- For range queries, keep the window short (≤1h) or increase `stepSeconds` to reduce data points

### Tool
Use `mcp__grafana__query_prometheus` with datasource UID `PC96415006F908B67`.

---

## 2. Loki Logs

### Base label selector

```logql
{service_namespace="APP", deployment_environment_name="production", service_name="rails"}
```

Replace `APP` with the app's Loki identifier (same as Prometheus identifier).

### Useful fields (pre-parsed by OTel collector)

| Field | Use |
|-------|-----|
| `event_duration_ms` | Request duration |
| `performance_time_db_ms` | DB time |
| `performance_time_cpu_ms` | CPU time |
| `rails_endpoint` | `Controller#action.format` — specific action + format |
| `rails_controller` | Controller name only — use to match all actions for a controller |
| `url_path` | URL path |
| `authentication_identity_id` | User ID — empty means unauthenticated request |
| `http_response_status_code` | HTTP status (string — use regex `=~ "4..\|5.."` for error filtering) |
| `url_full` | Full URL including account ID prefix — use for BC4 account filtering |
| `account_queenbee_id` | Queenbee account ID — present in BC4 logs |
| `transaction_id` | Rails request ID (UUID) — NOT an OTel trace ID |

### Query patterns

```logql
# Filter by field value
{labels} | field_name = "value"

# Multiple filters
{labels} | field1 = "value1" | field2 = "value2"

# Minimal output (reduces tokens)
{labels} | filters | keep field1,field2 | line_format "{{.field1}}"
```

### Aggregations (use for statistics — don't fetch raw logs for counts)

```logql
# Count over time window
sum(count_over_time({labels} | filters [12h]))

# Percentile of a numeric field
quantile_over_time(0.95, {labels} | filters | unwrap event_duration_ms | __error__="" [12h]) by ()

# Average
avg_over_time({labels} | filters | unwrap event_duration_ms | __error__="" [12h]) by ()

# Min/Max
min_over_time({labels} | filters | unwrap event_duration_ms | __error__="" [1h]) by ()
max_over_time({labels} | filters | unwrap event_duration_ms | __error__="" [1h]) by ()
```

### Critical rules

- **Always probe first:** Use `limit: 3` before running larger queries — check response size
- **Never use `| json`** — fields are already parsed by OTel; this causes JSONParserErr
- **Prefer field filters over `|=`** — `|=` is a substring match and will false-positive on partial IDs (e.g. searching `43483623` matches `1143483623`). Use `authentication_identity_id = "VALUE"` etc.
- **`|=` is still useful** when you don't know which field holds the value — but verify matches aren't substrings by inspecting raw line content
- **`http_response_status_code` is a string** — numeric comparisons (`>= 400`) silently return nothing; use regex: `http_response_status_code =~ "4..|5.."`
- **`list_loki_label_names` only shows stream index labels** — it won't list pre-parsed fields like `authentication_identity_id`, `rails_endpoint`, etc. Refer to the fields table in this guide instead
- **Never use `sum by (field)`** — returns a time series per value, explodes token usage
- For breakdowns by field: fetch raw logs with `| keep field | line_format "{{.field}}"` and count client-side
- `mcp__grafana__query_loki_logs` returns max ~100 results; use aggregations for large datasets

### Tool
Use `mcp__grafana__query_loki_logs` with datasource UID `e38bdfea-097e-47fa-a7ab-774fd2487741`.

LogQL docs: https://grafana.com/docs/loki/latest/query/

---

### Grafana dashboard panels

The `quantile_over_time` pattern works directly as a dashboard panel target (Loki datasource, range query type):

```logql
# Median response time for a specific controller, 5m rolling window
quantile_over_time(0.50, {service_namespace="APP", deployment_environment_name="production", service_name="rails"} | rails_controller = "controller_name" | unwrap event_duration_ms | __error__="" [5m]) by ()
```

Use `unit: ms` in field config. Add p95 as a second target for context.

---

## 3. Tempo Traces

### Finding traces

Use `mcp__grafana__tempo_traceql-search` to search for traces:

```traceql
{resource.service.name="APP" && duration > 2s}           # Slow requests
{resource.service.name="APP" && status=error}             # Failed requests
```

Replace `APP` with the app's service name (matches Loki identifier).

The search returns trace IDs, root span names, duration, and span counts. Pick traces with manageable span counts (<300) for detailed inspection.

### Inspecting a trace

Use `mcp__grafana__tempo_get-trace` with the trace ID from the search.

**Response structure:** `data.trace.resourceSpans[].scopeSpans[].spans[]`. Each span has `name`, `startTimeUnixNano`, `endTimeUnixNano`, and `attributes`.

**Critical: large traces overflow.** A trace with 3696 spans produces 1MB+ of JSON and will be truncated. For traces with >500 spans, prefer the TraceQL search with structural queries to pinpoint slow spans directly rather than fetching the full trace.

**Parsing pattern for extracted traces:**
```python
# Sort spans by duration, show the slowest
for span in all_spans:
    dur_ns = int(span['endTimeUnixNano']) - int(span['startTimeUnixNano'])
    dur_ms = dur_ns / 1e6
```

### Connecting Loki → Tempo

Loki logs have a `transaction_id` field — this is the **Rails request ID** (UUID), not an OTel trace ID. There is currently no direct Loki → Tempo correlation field. Use TraceQL search by endpoint and time window instead:
```traceql
{resource.service.name="APP" && name="GET /endpoint" && duration > 1s}
```

### Tool
Use `mcp__grafana__tempo_traceql-search` and `mcp__grafana__tempo_get-trace` with datasource UID `6PouH8j4z`.

---

## 4. Sentry

### Quick reference

```bash
# List recent issues
sentry issue list basecamp/<sentry-project>

# Full stacktrace
sentry issue view <issue-id>

# Resolve an issue
sentry api /issues/<id>/ --method PUT --field status=resolved
```

Organization: `basecamp` | Region: `https://us.sentry.io`

Auth: `sentry auth login` (browser OAuth)

---

## Eval Checks

Run after completing an investigation.

| # | Check | Pass criteria | If fail |
|---|-------|---------------|---------|
| 1 | Probed before large Loki query | Used `limit: 3` on first query | Go back, probe first next time |
| 2 | Used correct app identifier | APP matches the identifier table | Re-run with correct identifier |
| 3 | No `\| json` in Loki queries | Grep for `\| json` in queries used | Remove it; fields are pre-parsed |
| 4 | Aggregations for stats (not raw fetch) | Used `count_over_time` etc. for counts | Rewrite as aggregation |
| 5 | Investigation covers the right layer | Matched tool to question type | Follow metrics→logs→traces→errors order |

---

## Failure Modes

| Symptom | Cause | Fix |
|---------|-------|-----|
| JSONParserErr in Loki | Used `\| json` on pre-parsed fields | Remove `\| json`; fields are already labels |
| Empty Loki results | Wrong `service_namespace` (APP value) | Check App Identifiers table for correct value |
| Token explosion from Loki | Used `sum by (field)` or large raw fetch | Use aggregations; fetch with `keep` + `line_format` |
| Loki response truncated | Queried too many logs without aggregation | Switch to `count_over_time` or `quantile_over_time` |
| Tempo returns no results | Wrong `resource.service.name` | Use Loki identifier as service name |
| Prometheus returns no data | Wrong `app` label value | Check App Identifiers table |
| Prometheus range query overflows | Long window (>1h) + fine step = many data points | Use instant query or increase `stepSeconds` |
| `http_response_status_code >= 400` returns nothing | Field is a string, not numeric | Use regex: `http_response_status_code =~ "4..\|5.."` |
| `|=` matches unexpected logs | Substring match — `43483623` hits `1143483623` | Verify with `authentication_identity_id = "VALUE"` field filter instead |
| `list_loki_label_names` misses known fields | Only returns stream index labels, not pre-parsed OTel fields | Use the fields table in this guide; don't rely on label discovery |
| Identity not found across all apps | Searched wrong app, or ID is a resource ID not an identity | Search all apps; try both `authentication_identity_id` and `url_path =~ ".*ID.*"` |
| BC4 account ID not found in `url_path` | BC4 strips account prefix at router; `url_path` is `/projects/...` not `/2914079/projects/...` | Use `url_full =~ ".*ACCOUNT_ID.*"` instead |
| 302s look like errors but aren't | Unauthenticated bots/link-unfurlers always get 302 to login | Check `authentication_identity_id` — empty means unauthenticated, not broken |
| Missed account-specific errors | Searched Loki before Sentry for account errors | Use `sentry issue list basecamp/PROJECT --query "url:*ACCOUNT_ID*"` first for account errors |
| Tempo get-trace returns 1MB+ / truncated | Trace has thousands of spans | Use TraceQL structural queries or pick a trace with <300 spans |
| Tried to correlate Loki `transaction_id` to Tempo | `transaction_id` is Rails request ID, not OTel trace ID | Use TraceQL search by endpoint + time window instead |
| Tempo search returns no results | Wrong `resource.service.name` | Use the Loki/Prometheus identifier (e.g., `bc4`, `hey`) |
| Sentry auth fails | Not logged in | Run `sentry auth login` |
| "No such project" in Sentry | Using wrong project name | Check Sentry column in App Identifiers table |

---

## Exemplars

Study before using:

### Exemplar 4: Tempo trace drill-down for slow BC4 request (2026-03-04)

**Question:** "Why is GET /my/readings slow in BC4?"

**Process:**
1. Used `mcp__grafana__tempo_traceql-search` with `{resource.service.name="bc4" && duration > 2s}` — found 20 slow traces
2. Picked trace `7f9711c9271e5f5fe34ce6378fec592e` (259 spans, 2993ms) — manageable size
3. Fetched full trace with `mcp__grafana__tempo_get-trace` — 81KB, parseable
4. Extracted slowest spans with Python: `render_collection.action_view` (1642ms) + `SolidCache::Entry.upsert_all` (repeated, ~1.5s total)
5. Also attempted 3696-span trace — 1MB+, truncated, unusable

**Result:** `/my/readings` bottleneck is collection rendering (1642ms) + SolidCache bulk writes. Not a DB query issue.

**Key learnings:**
- `transaction_id` in Loki is Rails request ID, not OTel trace ID — no direct Loki→Tempo correlation
- Trace JSON structure: `data.trace.resourceSpans[].scopeSpans[].spans[]`
- Large traces (>500 spans) overflow — filter by span count in search results before fetching
- `account_queenbee_id` exists as a Loki field in BC4 logs (discovered from raw log inspection)

---

### Exemplar 3: Find error for BC4 account 2914079 (2026-03-04)

**Question:** "Find the error that affected account 2914079"

**Process:**
1. Tried `url_path =~ "/2914079/.*"` — no results. BC4 strips the account from `url_path` at the router level
2. Tried `url_full =~ ".*2914079.*"` — worked. `url_full` retains the original full URL including account ID
3. Found all responses were 302 — investigated, but these were unauthenticated link-unfurler requests (empty `authentication_identity_id`), not errors
4. Searched Sentry with `url:*2914079*` — found escalating asset pipeline errors
5. Identified root cause: `image_tag("icons/reversed/memory-remove--white")` missing `.svg` extension in `recordings/menu_helper.rb:106`

**Result:** **BC3-5ZSD** — `Sprockets::Rails::Helper::AssetNotFound: icons/reversed/memory-remove--white` not in asset pipeline. 196 events, 3 users, first seen 3/4/2026 12:10 AM. Fix: add `.svg` extension on line 106.

**Key learnings:**
- For BC4, use `url_full =~ ".*ACCOUNT_ID.*"` not `url_path` — account ID is stripped from path
- Sentry `url:*ACCOUNT_ID*` query is the fastest path to account-specific errors
- 302s with empty `authentication_identity_id` = unauthenticated bots/link-unfurlers, not errors

---

### Exemplar 2: Find error for account 43483623 (2026-03-04)

**Question:** "Find the error that affected account 43483623"

**Process:**
1. Tried `authentication_identity_id = "43483623"` in BC4, Fizzy → no results
2. Tried `url_path =~ ".*43483623.*"` in BC4, Queenbee → no results
3. Tried `|= "43483623"` in HEY → matched! But raw line inspection revealed it was matching `1143483623` (a posting ID) — false positive
4. Confirmed via `authentication_identity_id = "43483623"` exact field filter in HEY and BC4 → no identity exists with that ID

**Result:** Identity 43483623 not found. User confirmed to double-check the ID.

**Friction fixed:**
- `|=` substring false-positive: documented to verify matches by inspecting raw line content
- `http_response_status_code >= 400` silently fails: field is a string, must use regex
- `list_loki_label_names` only shows stream labels: doesn't help discover pre-parsed fields
- Multi-app search pattern established: search all apps before concluding not found

---

### Exemplar 1: Fizzy recent issues + BC4 slow endpoints (2026-03-04)

**Questions:** "Recent issues in Fizzy" + "Slow endpoints in Basecamp"

**Fizzy findings:**
- `FIZZY-D7`: `no queenbee.yml found` (48 hits/13h) — `Queenbee::AccountsController#show`, missing `QUEENBEE_SECRET` env var on fizzy-app-101. Escalating issue first seen Nov 2025.
- `FIZZY-NR/NS/NQ`: MySQL read-only errors — 2-second window at 7:58 PM, likely a brief DB failover. Not actionable.

**BC4 findings (p99 up to 710ms in df-iad):**
Slowest endpoints (>1s in last 30 min):
- `Projects::RecordingsController#index.json` — 3701ms
- `My::AssignmentsController#index.html` — 2895ms
- `Uploads::Versions::RepresentationsController#show.html` — up to 2066ms (multiple hits)
- `Blobs::PreviewsController#show.html` — up to 2701ms
- `My::Schedules::DisplaysController#show.json` — 1625ms

**Workflow used:** Sentry first (right call for "recent issues") → Prometheus instant query for BC4 shape → Loki raw fetch for top slow endpoints

**Friction fixed:**
- Workflow was metrics-first; updated to question-type routing table
- Prometheus range query over 3h overflowed; added instant query guidance
- `rails_controller` vs `rails_endpoint` distinction documented
