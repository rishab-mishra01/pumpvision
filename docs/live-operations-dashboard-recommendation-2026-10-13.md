# PumpVision Live Operations Dashboard Recommendation

**Date:** 2026-10-13
**Executor:** GPT-5.6 Sol via Codex CLI in WSL
**Scope:** Design only; no files, services, deployment, or production data changed.

## Recommendation
Build a new read-only `/ops` control panel inside the existing Flask/PostgreSQL application. Use conditional HTTP polling for the MVP rather than WebSockets or SSE.

- Poll every 10 seconds while visible.
- Poll jobs/reconciliation every 30–60 seconds.
- Use ETag/304 responses, no-store caching, exponential backoff, and stale-age indicators.
- Pause polling when the tab is hidden and refresh on focus.
- Do not claim live total fuel sales until reliable intraday IRAS/Paytm feeds exist.

## Dashboard sections

1. **Current cycle**
   - Operational date and exact covered window.
   - Open, submitted, provisional, degraded, or final state.
   - Shift-close progress for all liquid nozzles and CNG.

2. **Financial snapshot**
   - Gross fuel sales, POS/Paytm sales, credit, fleet card, expenses.
   - Expected sales cash after expenses.
   - Separate account collections from POS sales.
   - Explicitly state that physical cash is unknown until counted-cash movements exist.

3. **Live activity feed**
   - Credit sales, expenses, customer payments, shift readings, price changes, alerts, and corrections.

4. **Source and service health**
   - IRAS prices, Paytm, SDMS/eLedger, CGD/CNG, ATG, totalizer reconciliation, PostgreSQL, web app, VPS, cron, backups, and review job.

5. **Per-nozzle reconciliation**
   - Manual reading, IRAS reading, difference, source, source timestamp, freshness, and provisional/final badge.

6. **CNG status**
   - eLedger cost per kg, fixed Rs 2.47 margin, derived selling price, manual kg, SDMS kg, variance, and pending/late CGD state.

7. **Alerts and audit**
   - Critical/warning/info severity.
   - Open, acknowledged, and resolved lifecycle.
   - Actor, timestamps, corrections, price changes, shift submissions, and job results.

## Recommended backend foundation

Add explicit durable tables:

### `ops_events`
- Monotonic ID, event type, entity type/id, operational date, source, time basis.
- Business/observed/recorded timestamps.
- Actor, correlation ID, idempotency key, JSON payload, sensitivity.
- Insert app events transactionally with the business mutation.

### `job_runs`
- Job/source, scheduler key, host, target operational date, attempt, state.
- Scheduled/started/heartbeat/finished timestamps.
- Exit code, code revision, row counts, redacted error, log reference, spool count.
- States: scheduled, running, success, partial, failed, spooled, skipped, busy.

### `shift_closes`
- One row per operational date.
- Policy version, state, window, submitted time/actor.
- Manual and auto-idle counts, method, and revision.

## Data freshness contract
Every metric must show:

- Source.
- Covered window.
- Time basis (`app_cycle`, `iras_0600`, `sdms_pad_date`, or `calendar_date`).
- Observed, ingested, and computed times.
- Expected-by time.
- Provisional/final/degraded state.

Missing data must never appear as zero.

## Current issues to resolve before MVP

1. Rotate and remove the hardcoded IRAS credential from the scraper and repository history.
2. Fix the remaining CNG fallback value of 87.0.
3. Align VPS reconciliation and scraper policy with the midnight shift boundary.
4. Fix nozzle 16 boundary-floor handling.
5. Keep missing IRAS prices visibly provisional and show the actual fallback rate.
6. Separate Paytm POS from customer debt repayments.
7. Label expected sales cash separately from physical cash.
8. Show missing CGD as pending/late/aged-out, not healthy zero.
9. Show manual-vs-SDMS CNG readings and variance.
10. Treat XG ATG reliability warnings as visible source metadata.

## Suggested API surface

- `GET /ops/`
- `GET /ops/api/v1/snapshot?cycle_date=YYYY-MM-DD`
- `GET /ops/api/v1/events?after_id=<id>&limit=100`
- `GET /ops/api/v1/reconciliation/YYYY-MM-DD`
- `GET /ops/api/v1/alerts?state=open`
- `GET /ops/api/v1/jobs?since=<timestamp>`
- `GET /ops/api/v1/audit?after_id=<id>`

Use monotonic event IDs for pagination, not timestamps.

## Role access

- Owner: business operations and business alert acknowledgement.
- Developer: technical health, jobs, and technical acknowledgement.
- Manager: narrow current-shift/task view.
- Super-admin: read-only preview according to existing capabilities.
- Attendant: no control-panel access.

Keep the MVP read-only. Any later remediation action requires CSRF, re-authentication, confirmation, idempotency, actor logging, and before/after audit events.

## Phased rollout

### Phase 0 — Correctness/security
- Credential rotation.
- Define time bases and finalization policy.
- Fix CNG fallback and post-cutover reconciliation policy.
- Decide whether post-cutover auto-idle is possible or must be disabled.
- Create golden fixtures for legacy, cutover, normal, and late-CGD days.

### Phase 1 — Telemetry
- Add `ops_events` and `job_runs` with indexes.
- Instrument app mutations and scraper wrappers.
- Persist structured job attempts and provenance-returning metric services.

### Phase 2 — MVP `/ops`
- Read-only dashboard.
- 10-second conditional polling.
- Current cycle, financial snapshot, activity, reconciliation, CNG, source health, alerts, and audit.
- Keep existing dashboard unchanged during validation.
- No WebSockets, materialized views, or job controls.

### Phase 3 — Reconciliation hardening
- Explicit shift-close lifecycle.
- Unified alert acknowledgement.
- Immutable price history.
- Customer payment actor/business timestamps.
- Separate expected cash, account collections, and physical cash.

### Phase 4 — True intraday operations
- Immutable IRAS transaction records with deduplication.
- Overlapping late-data fetches.
- Provisional intraday nozzle/product totals.
- Supported near-live Paytm feed.
- Reconcile provisional values to final boundaries.

### Phase 5 — Controlled actions and resilience
- Audited retry queue.
- Narrow remediation controls.
- Stronger external identity gate.
- Backup restore drills and service supervision.

## MVP acceptance criteria

1. Every number shows source, window, freshness, and provisional/final state.
2. Missing data never appears as zero.
3. Paytm POS and customer repayments are visibly separate.
4. Expected sales cash is not labelled physical cash.
5. Every nozzle shows manual/IRAS source and reconciliation state.
6. Missing CGD stays visible beyond the current age-out period.
7. Price fallback is visible and auditable.
8. Job history survives restarts and log rotation.
9. Events are transactional and deduplicated.
10. All role permissions are tested.
11. Polling meets latency/load targets without exhausting Gunicorn.
12. Existing financial outputs remain unchanged until the new view is approved.

## Approved production UI and accessibility addendum

The `/ops` MVP is a professional five-view operations console, not a single dense
screen. Its views are Overview, Activity, Reconciliation, Sources, and Audit. The
overview is limited to four headline metrics; supporting provenance is revealed in
the relevant view without hiding source, freshness, or state.

The live activity stream includes the actor or system, role, operational date,
business time in IST, source, and event state. It covers shift-close submissions,
manual CNG readings, expenses, account collections, price imports, IRAS nozzle
boundaries, SDMS/eLedger summaries, and ATG snapshots. Current-state cards show the
latest manual and system readings together rather than presenting the event stream as
the current truth.

Accessibility is built in by default and targets WCAG 2.2 AA. Requirements include
semantic landmarks and headings, a skip link, complete keyboard operation, visible
focus, 44 px touch targets, text labels in addition to colour, responsive reflow,
reduced-motion support, and restrained live-region announcements. Live refreshes must
not steal focus, move the reader's scroll position, or automatically prepend ordinary
events while the activity stream is being read.

An Accessibility control provides persistent text-size, contrast, information-density,
reduced-motion, automatic-update, and critical-announcement preferences. Pausing live
updates freezes only the browser presentation; it does not stop database collection.

Authenticated JSON responses use conditional ETags with
`Cache-Control: private, no-cache, max-age=0, must-revalidate`. The HTML shell remains
`no-store`. Missing physical cash is explicitly disclosed, and missing source data is
never rendered as a healthy zero.
