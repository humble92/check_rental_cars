# Departure - rental condition reports

An English, login-free web app for documenting a rental vehicle before driving away. Users upload photos, request an AI review, read an HTML report, download a PDF and original-photo ZIP, and optionally email the report. A read-only result link expires seven days after report generation.

**AI provider: Meta Model API.** Both `muse-spark-1.3-contributor` and `muse-ember-1.0` successfully analyzed the two supplied test images and returned valid application JSON. The default is the configured `MUSE_MODEL` (Contributor). Baseten was tested first but its configured model was deactivated; the application no longer calls Baseten. It never substitutes fabricated observations for a failed provider request.

**Contributor data policy:** Meta may use Contributor-mode inputs and outputs, including uploaded photos, to train its models. The application discloses this before analysis when a Contributor model is selected. `muse-ember-1.0` runs in Standard mode, where content is not used to train Meta models. Choose based on both data handling and cost; do not confuse seven-day local deletion with provider retention. See [Meta models](https://dev.meta.ai/docs/models) and [pricing modes](https://dev.meta.ai/docs/pricing-rate-limits).

## Features

- Twelve optional recommended exterior views, plus any additional interior/exterior/detail photos. One photo is sufficient; missing views appear in the report.
- JPEG, PNG, WebP, and HEIC uploads, orientation correction, private original storage, normalized analysis copies, per-photo retry, and draft recovery in the same browser.
- AI observations with evidence photos, optional numbered bounding boxes, uncertainty, quality limitations, and separate user notes.
- Matching HTML/PDF content, original-photo ZIP downloads, and optional SMTP delivery with PDF attachment up to 8 MiB.
- Independent read and owner tokens; shared URLs cannot modify reports or send email.
- Durable SQLite jobs, bounded retries, per-photo checkpoints, lease recovery, and separate cleanup scheduling.
- English UI, documentation, and code. MIT license.

## Quick start

Python 3.9 or newer can run the code; use a supported Python release such as 3.12 for deployment. The local checks were run on Python 3.9.19 / Windows. A single host with a persistent local disk is required.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
```

On Linux/macOS, use `.venv/bin/python` in place of `.\.venv\Scripts\python.exe`. `requirements.lock.txt` records the versions tested locally; `requirements.txt` declares the compatible ranges.

Create `.env` from `.env.example` **only if you do not already have one**, then set `MUSE_API_KEY` and `MUSE_MODEL`. The existing `.env` is never printed or committed.

Start the web server:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-access-log
```

Start the worker in a second terminal:

```powershell
.\.venv\Scripts\python.exe -m app.worker
```

Open **http://127.0.0.1:8000**. Use this exact origin with the default configuration; if you use another hostname or port, update `PUBLIC_BASE_URL` to match. The worker must be running for analysis and email delivery. `python -m app.worker --once` processes at most one eligible job and exits.

## Meta vision comparison

This explicitly makes four paid requests: two supplied test images per model. It loads `MUSE_API_KEY` from `.env`, corrects orientation, resizes images, and submits image data URLs to the OpenAI-compatible chat endpoint:

```powershell
.\.venv\Scripts\python.exe scripts/test_muse.py
```

- Endpoint: `https://api.meta.ai/v1/chat/completions`
- Authentication: `Authorization: Bearer <MUSE_API_KEY>`
- Application model: `MUSE_MODEL` (default: `muse-spark-1.3-contributor`)
- Inputs: `resources/test/*.jpg`
- The comparison writes responses to ignored `tmp/muse-smoke.json`, without keys or request headers.
- The script validates the same structured photo schema used by the application. Add `--models muse-ember-1.0` to test only one model.
- The application validates returned JSON. It rejects invalid/truncated output, never treats a provider error as a successful empty damage report, and retries up to three times per job. A user may request at most three analysis jobs per report.

Observed results on the supplied close-ups (one request per image/model, not a performance benchmark):

| Model | Exterior photo | Interior photo | JSON schema |
| --- | --- | --- | --- |
| `muse-spark-1.3-contributor` | HTTP 200, 12.97 s, 1 observation | HTTP 200, 8.03 s, 1 observation | Both valid |
| `muse-ember-1.0` | HTTP 200, 2.94 s, 2 observations | HTTP 200, 3.20 s, 1 observation | Both valid |

Both located the exterior panel crease and the damaged interior trim. Ember additionally reported an uncertain exterior scuff. Damage terminology differed: Spark described gouged/lifted interior material, while Ember described a depression. These two photos verify integration and basic image grounding, not general detection accuracy. See [the test record](docs/model-evaluation.md).

To exercise the real application adapter, worker, HTML, PDF, and ZIP with paid API calls:

```powershell
.\.venv\Scripts\python.exe scripts/test_live_report.py
```

This saves private test artifacts under ignored `tmp/live-report`. It sends no email. The retained `scripts/test_baseten.py` is only a historical diagnostic and is not used by the application.

## How it works

1. The browser creates an anonymous draft and keeps its owner/share credentials in local storage. There is no account or server-side user profile.
2. Each upload is validated and saved under its report ID. Original bytes are retained with a SHA-256 hash; normalized JPEGs have orientation applied and metadata removed.
3. Analysis submission freezes photos, vehicle details, and user notes. A SQLite job is queued. The browser polls durable status; closing the browser does not cancel the worker.
4. The worker analyzes each photo with Meta, validates its structured response, and checkpoints completed photo results. It generates the PDF before marking the report ready.
5. The result URL supports read-only HTML, annotated evidence images, PDF, and original-photo ZIP. Every request checks expiration. Lifetime does not extend on revisits or email sends.
6. Optional email is a separate job. SMTP acceptance is shown as such; it does not guarantee inbox delivery. Delivery addresses and link payloads are cleared after success or terminal failure.
7. The cleanup CLI removes expired files and database records independently of request-time expiry checks.

Photos are analyzed independently. Similar observations across photographs are **not automatically deduplicated**: the report labels its count as observations, not unique damage. User notes are captured before analysis; post-generation editing and cross-photo association are future enhancements. The UI and PDF are English; user-entered text is preserved as supplied.

## Storage and retention

```text
data/
  app.sqlite3
  cleanup-status.json
  reports/<report-id>/
    originals/
    normalized/
    artifacts/report.pdf
```

SQLite uses WAL mode, foreign keys, short write transactions, and a busy timeout. The web server, worker, and cleaner use the same `DATA_DIR`. Run one worker initially. Do not place the SQLite database on a network filesystem or distribute these processes across hosts.

| Resource | Default policy |
| --- | --- |
| Completed reports, photos, PDF | Seven days from first generation; access rejected immediately at expiry |
| Unsubmitted drafts | 24 hours after last saved detail or upload |
| Failed or abandoned processing | 24 hours after last state update, except active worker leases |
| Download already started | May finish after link expiry; protects files for at most one hour |
| Orphan report directories | Removed after a 24-hour grace period |
| Email recipient/link payload | Cleared on delivery success, terminal failure, or report deletion |
| Original uploads | At most 30 photos, 15 MiB each, 200 MiB total per report |
| Image dimensions | At most 40 megapixels; at least 64 pixels per side |
| Free disk floor | 512 MiB plus upload headroom; configurable |

The ZIP is built on demand in an OS temporary file, streamed, and closed; it is not permanently cached. Unexpired reports are not deleted to recover space. New uploads fail when the disk floor is reached. Downloaded or emailed copies remain outside the server after expiry. Server deletion does not automatically erase any provider-side processing records or external backups; configure those retention policies separately.

## Cleanup scheduling

Manual run:

```powershell
.\.venv\Scripts\python.exe -m app.cleanup
```

The command atomically marks eligible records as deleting, excludes active job/download leases, removes confined report directories, and then removes database records. Repeating it is safe. Interrupted deletion is resumed next time. Path traversal and symlinks resolving outside the report root are rejected. It writes `cleanup-status.json` with its last run, deletion failures, and free disk space. Monitor that file and the service exit status.

Linux production examples are in `deploy/`. Install the app at `/opt/rental-reports`, create a dedicated `rental` system user and `/var/lib/rental-reports` owned by that user, set `DATA_DIR=/var/lib/rental-reports` in `.env`, and install the service units. Set restrictive permissions on `.env` and the data directory. Enable the web service, worker, and timer:

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now rental-web.service rental-worker.service rental-cleanup.timer
systemctl list-timers rental-cleanup.timer
```

`Persistent=true` catches missed calendar-triggered runs after downtime. A systemd oneshot service does not start a second instance of itself while it is running. These are templates to install on your host; this repository does not install services automatically.

Cron alternative, running as the application user:

```cron
*/15 * * * * cd /opt/rental-reports && /usr/bin/flock -n /var/lib/rental-reports/cleanup.lock /opt/rental-reports/.venv/bin/python -m app.cleanup
```

On Windows, create a Task Scheduler task every 15 minutes with the virtual environment's `python.exe` as Program, `-m app.cleanup` as Arguments, and the repository directory as Start in. Select the option not to start a new instance when the task is already running.

## Email and public deployment

Set `SMTP_HOST`, `SMTP_PORT`, `SMTP_FROM`, and, if required, `SMTP_USER` / `SMTP_PASSWORD`. Port 587 with STARTTLS is the default. This adapter uses SMTP with optional STARTTLS, not implicit TLS on port 465. Until configured, email is visibly disabled; downloads and share links still work. Verify your sender domain with your email provider before production use.

Set `PUBLIC_BASE_URL` to the exact HTTPS public origin with no trailing path. Terminate TLS at a reverse proxy; an Nginx example is included. Disable access logging for token-bearing URLs at both proxy and application layers. Keep storage out of the static web root. Configure trusted proxy forwarding only for your own proxy. Basic creation limits are 10 reports/hour/client IP; email is limited to 3 requests/hour/report. Add edge-level connection/rate limits and an overall provider budget before accepting untrusted public traffic.

Owner credentials are only in the originating browser. Clearing browser data loses editing/email controls, but a saved read link continues to work until expiry. Anyone with the read link can access the report. The application uses a fixed public origin, same-origin write checks, no third-party scripts, no-referrer responses, escaped templates, and no-store response headers.

Email delivery is at-least-once: a crash after SMTP acceptance but before the SQLite commit can cause a duplicate message on retry. A stable Message-ID is used, but deduplication depends on the receiving system. Similarly, a crash immediately after an AI response but before its checkpoint can repeat a paid inference call.

## Verification

```powershell
.\.venv\Scripts\python.exe -m pytest -q --basetemp=tmp/pytest
node --check app/static/app.js
.\.venv\Scripts\python.exe scripts/preview_pdf.py
```

The automated suite uses isolated storage and mocked AI/SMTP; it makes no paid requests or real email sends. **14 tests passed** in the final local run. It covers report generation, PDF text/images, ZIP originals, owner/read separation, HTML escaping, request origin checks, file limits, expiry on all result routes, cleanup idempotency, active leases, crashed-job recovery, checkpoints, retry exhaustion, email payload erasure, SMTP attachment construction, homepage/disclosure rendering, and path confinement.

The real configured Meta adapter also completed an end-to-end run: two photos, ready HTML report, three-page PDF, and original-photo ZIP. All three rendered PDF pages were visually inspected; numbered markers and text were checked after the final rendering change. Run `scripts/test_live_report.py --render-only` to re-render the retained live test without new paid requests.

`scripts/preview_pdf.py` creates an explicitly synthetic PDF fixture using the supplied photos and renders page PNGs under ignored `tmp/pdf-preview`. Its sample observations are not AI findings. Browser automation was unavailable in this session, so interactive/mobile visual verification remains a deployment check. No SMTP credentials were supplied; real email delivery remains unverified. Deployment units are provided but were not installed or exercised on a Linux host in this session.

## Project map

| Path | Purpose |
| --- | --- |
| `implementaion.md` | Implementation plan written before application code |
| `app/main.py` | Web pages, upload/status/result/download/email APIs |
| `app/ai.py` | Meta image request and strict response validation |
| `app/worker.py` | Durable analysis/email job execution |
| `app/reports.py` | Shared report data and PDF rendering |
| `app/cleanup.py` | Scheduled retention cleanup |
| `app/templates`, `app/static` | English responsive UI |
| `tests` | Offline regression tests |
| `deploy` | Single-host service, timer, and reverse-proxy examples |

## License

MIT. See [LICENSE](LICENSE).
