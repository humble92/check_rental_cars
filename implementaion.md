# Rental condition report: implementation plan

## Scope and language

Build an English, login-free mobile-first web application in this repository. All source code, UI text, documentation, and report content are English. License: MIT. Use Python/FastAPI, SQLite, and a persistent local server filesystem. This deployment requires a single server with persistent disk; it is not a static or Cloudflare Workers application.

## Implementation sequence

1. Test Baseten vision input against `https://inference.baseten.co/v1/chat/completions` using the model and key from `.env` and images in `resources/test`. Never log credentials. Record the sanitized outcome. Use Baseten only after a successful image-aware response.
2. Implement configuration, SQLite schema, private file storage, anonymous owner credentials, and bounded image uploads. Offer twelve optional recommended views plus unrestricted view categories for extra photos. Support JPEG, PNG, WebP, and HEIC; retain originals and normalize copies.
3. Add durable SQLite jobs and a separate worker for analysis, document generation, and optional email. Use bounded retries, leases, and checkpointed per-photo analysis. Reject invalid model output instead of inventing successful reports. Present uncertain observations explicitly.
4. Render a structured, versioned report as English HTML and downloadable PDF with the same findings, images, notes, timestamps, and coverage. Provide an original-photo ZIP. Preserve AI observations separately from user notes. Do not assert when damage occurred.
5. Issue an unguessable read URL, valid for seven days from first report completion. Separate owner access from shared read access. Check expiration for every report, photo, and download request. Do not extend lifetime on revisit or email.
6. Implement optional SMTP email with summary, expiring link, and PDF attachment within size limits. Queue sends independently. Apply send limits and erase delivery addresses when no longer needed. SMTP configuration is required for real delivery; never report an unconfigured send as successful.
7. Add an idempotent cleanup CLI and Linux systemd timer/cron examples. Expire access at request time independently of cleanup. Delete completed reports after seven days, abandoned drafts after 24 hours, terminal failures after 24 hours, and orphan temporary files when safe. Coordinate cleanup with active operations and confine deletions to the storage root.
8. Validate the real Baseten integration, upload and authorization boundaries, expiry, durable jobs and retries, cleanup races, HTML/PDF consistency, ZIP content, email behavior, and mobile UI. Render a representative PDF and inspect page images. Summarize setup, operation, limitations, and test results in README.

## Architecture

- FastAPI serves server-rendered pages, static assets, and JSON APIs.
- SQLite stores reports, photos, findings as validated JSON, jobs, delivery requests, and rate-limit counters. WAL mode, busy timeout, and short write transactions allow the web process and one worker to cooperate.
- A worker atomically leases one job at a time. It performs network and image work outside database transactions. Recover expired leases after a crash; checkpoint analysis per photo to reduce duplicate paid calls.
- Data layout: `data/app.sqlite3`, `data/reports/<uuid>/originals`, `normalized`, and `artifacts`. Runtime files and secrets are ignored by version control.
- Each report has a secret owner token and an independent share token. Store their hashes; retain the share token only in the owner's browser and encrypted delivery payload where needed, or pass it explicitly when requesting email. No token-bearing access logs.
- One shared report data model drives both HTML and PDF. Findings retain their evidence photo and normalized bounding box when reliable. Cross-photo similarities are candidates, not asserted identical damage.
- Original images are evidence submissions, not proof of capture time. Server upload time and supplied capture metadata must not be conflated. Analysis uses normalized copies without GPS metadata.

## Initial operating limits

- Up to 30 photos per report, 15 MiB per photo, and 200 MiB total original uploads per report.
- Recommended views are optional; one valid photo is sufficient.
- Bound image dimensions, AI timeouts, output lengths, retries, draft creation, and email requests. Enforce a configurable free-disk floor before accepting uploads.
- Store timestamps as UTC, display explicit UTC dates, and expire at first completion plus seven days.
- Cleanup runs every 15 minutes. Repeated execution and missing files must be safe. Disk pressure blocks new uploads instead of deleting unexpired reports.

## Deployment and operations

Use a Python virtual environment, an ASGI web process, one separate worker, and a scheduled cleanup command on one persistent host. Terminate HTTPS at a reverse proxy. Keep the database and files on the same local disk, outside public static routes. Configure a fixed public origin, SMTP credentials, and secure cookies in production. Use consistent backups; backup retention must also honor data deletion policy. Multi-host deployments require replacing the local storage and SQLite coordination strategy.

## Acceptance criteria

- A user can upload recommended or additional photos without login, resume their draft in the same browser, submit once, and see durable progress.
- Baseten accepts image input and produces validated English observations; a provider failure appears honestly and can be retried within limits.
- HTML and PDF contain matching report content, readable English text, and correctly oriented photos. The ZIP contains the original submissions.
- Shared links allow read-only access for exactly seven days. Owner-only mutations reject shared credentials. All artifact routes reject expired access.
- Cleanup tolerates interrupted deletion and cannot remove files outside the configured report root. Active jobs cannot resurrect deleted reports.
- Optional email only succeeds after actual SMTP acceptance. Missing SMTP configuration produces a clear unavailable state.
- Tests use isolated temporary storage and mocked network calls by default; real paid Baseten testing is an explicit command.

## Deferred work

Billing, accounts, repair estimates, legal determinations, guaranteed damage detection, multi-server scaling, and automatic proof of damage age are outside this MVP. AI observations require human review. Model choice and operating limits remain configurable.

## Provider update and implementation decisions

The original Baseten smoke test returned HTTP 400 because its model version was deactivated. The user then requested Meta Model API instead, with `MUSE_API_KEY`, `MUSE_MODEL`, and `https://api.meta.ai/v1`. Both requested models (`muse-spark-1.3-contributor` and `muse-ember-1.0`) passed image requests and application-schema validation on both supplied photographs. The application now uses Meta, with the configured Contributor model as the default. Contributor-mode data-use disclosure appears before analysis; Ember is available by changing `MUSE_MODEL`.

This MVP retains per-photo observations without asserting cross-photo deduplication. Notes are editable while the report is a draft, then frozen with the evidence. HTML and PDF use the same validated data. Photos ZIPs are generated on demand in temporary files rather than retained. Browser-based visual QA was blocked by unavailable browser access and a native app approval timeout; API and PDF checks remain independently testable.
