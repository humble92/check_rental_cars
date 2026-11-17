import hashlib
import io
import json
from pathlib import Path
import secrets
import tempfile
import time
import zipfile
from contextlib import asynccontextmanager

from email_validator import validate_email, EmailNotValidError
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from .config import Settings, ROOT, VIEWS, VIEW_LABELS
from .db import connect, digest, enqueue, initialize, transaction
from .models import ReportInput, EmailInput
from .reports import report_data, utc
from .storage import ensure_capacity, prepare_image, report_dir, safe_path, annotated_image


def create_app(settings=None):
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app):
        initialize(settings)
        yield

    app = FastAPI(title="Departure - rental condition reports", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.settings = settings
    app.mount("/static", StaticFiles(directory=ROOT / "app/static"), name="static")
    templates = Jinja2Templates(directory=ROOT / "app/templates")
    templates.env.filters["utc"] = utc

    @app.middleware("http")
    async def headers(request, call_next):
        if request.method in {"POST", "PATCH", "DELETE"}:
            origin = request.headers.get("origin")
            if origin and origin != settings.base_url:
                return JSONResponse({"detail": "Request origin is not allowed."}, status_code=403)
            if request.headers.get("sec-fetch-site") == "cross-site":
                return JSONResponse({"detail": "Cross-site writes are not allowed."}, status_code=403)
            length = request.headers.get("content-length")
            limit = settings.max_photo_bytes if "/photos" in request.url.path else 16384
            try:
                if length is None:
                    return JSONResponse({"detail": "Content-Length is required."}, status_code=411)
                if int(length) < 0 or int(length) > limit:
                    return JSONResponse({"detail": "Request is too large."}, status_code=413)
            except ValueError:
                return JSONResponse({"detail": "Invalid Content-Length."}, status_code=400)
        response = await call_next(request)
        response.headers.update({"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "Content-Security-Policy": "default-src 'self'; img-src 'self' blob: data:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"})
        return response

    @app.exception_handler(HTTPException)
    async def errors(request, exc):
        if request.url.path.startswith("/r/"):
            return templates.TemplateResponse(request=request, name="error.html", context={"message": exc.detail, "code": exc.status_code}, status_code=exc.status_code)
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

    def active(row):
        if row is None:
            raise HTTPException(404, "Report not found.")
        if row["status"] in {"deleting", "expired"} or (row["expires_at"] and row["expires_at"] <= time.time()):
            raise HTTPException(410, "This report has expired and is no longer available.")
        if not row["expires_at"] and row["status"] in {"draft", "failed"} and row["updated_at"] + 86400 <= time.time():
            raise HTTPException(410, "This unfinished report has expired.")
        return row

    def owner(db, report_id, request):
        row = db.execute("SELECT * FROM reports WHERE id=?", (report_id,)).fetchone()
        token = request.headers.get("authorization", "").removeprefix("Bearer ")
        if row is None or not secrets.compare_digest(row["owner_hash"], digest(token)):
            raise HTTPException(404, "Report not found.")
        return active(row)

    def shared(db, token):
        row = active(db.execute("SELECT * FROM reports WHERE share_hash=?", (digest(token),)).fetchone())
        if row["status"] != "ready":
            raise HTTPException(409, "This report is not ready yet.")
        return row

    def rate(db, key, maximum, period=3600):
        now = time.time()
        key = digest(key)
        row = db.execute("SELECT * FROM rate_limits WHERE bucket=?", (key,)).fetchone()
        if row and row["expires_at"] > now and row["count"] >= maximum:
            raise HTTPException(429, "Too many requests. Please try again later.")
        if row and row["expires_at"] > now:
            db.execute("UPDATE rate_limits SET count=count+1 WHERE bucket=?", (key,))
        else:
            db.execute("INSERT OR REPLACE INTO rate_limits VALUES(?,?,?)", (key, 1, now + period))

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request):
        return templates.TemplateResponse(request=request, name="index.html", context={"views": VIEWS, "contributor_mode": settings.muse_model.endswith("-contributor")})

    @app.get("/health")
    def health():
        with connect(settings) as db:
            db.execute("SELECT 1").fetchone()
        return {"status": "ok"}

    @app.post("/api/reports", status_code=201)
    def create(request: Request, body: ReportInput):
        report_id = secrets.token_hex(16)
        owner_token, share_token = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        now = time.time()
        with transaction(settings) as db:
            rate(db, "create:" + (request.client.host if request.client else "unknown"), 10)
            db.execute("INSERT INTO reports(id,owner_hash,share_hash,vehicle,plate,company,location,notes,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)", (report_id, digest(owner_token), digest(share_token), body.vehicle, body.plate, body.company, body.location, body.notes, now, now))
        return {"id": report_id, "owner_token": owner_token, "share_token": share_token}

    @app.get("/api/reports/{report_id}")
    def status(report_id: str, request: Request):
        with connect(settings) as db:
            owner(db, report_id, request)
            report = report_data(db, report_id)
            emails = [dict(row) for row in db.execute("SELECT id,status,error FROM jobs WHERE report_id=? AND kind='email' ORDER BY id DESC LIMIT 3", (report_id,))]
        return {"id": report_id, "status": report["status"], "error": report["error"], "expires_at": report["expires_at"],
                "details": {key: report[key] for key in ("vehicle", "plate", "company", "location", "notes")},
                "photos": [{"id": p["id"], "view": p["view"], "filename": p["filename"], "analyzed": bool(p["analysis"])} for p in report["photos"]],
                "email_enabled": settings.email_enabled, "emails": emails}

    @app.patch("/api/reports/{report_id}")
    def update(report_id: str, request: Request, body: ReportInput):
        with transaction(settings) as db:
            report = owner(db, report_id, request)
            if report["status"] != "draft":
                raise HTTPException(409, "Details cannot change after analysis starts.")
            db.execute("UPDATE reports SET vehicle=?,plate=?,company=?,location=?,notes=?,updated_at=? WHERE id=?", (body.vehicle, body.plate, body.company, body.location, body.notes, time.time(), report_id))
        return {"saved": True}

    @app.post("/api/reports/{report_id}/photos", status_code=201)
    async def upload(report_id: str, request: Request, view: str = "extra", filename: str = "photo"):
        if view not in VIEW_LABELS or len(filename) > 255:
            raise HTTPException(422, "Invalid photo label or filename.")
        with connect(settings) as db:
            if owner(db, report_id, request)["status"] != "draft":
                raise HTTPException(409, "Photos cannot change after analysis starts.")
        try:
            ensure_capacity(settings)
            data = bytearray()
            async for chunk in request.stream():
                data.extend(chunk)
                if len(data) > settings.max_photo_bytes:
                    raise HTTPException(413, "Each photo must be 15 MiB or smaller.")
            normalized, extension, sha = await run_in_threadpool(prepare_image, bytes(data))
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(422, "Could not read this image, or storage is full. Use a valid JPEG, PNG, WebP, or HEIC under 40 megapixels.")
        photo_id = secrets.token_hex(16)
        original_rel, normalized_rel = "originals/" + photo_id + extension, "normalized/" + photo_id + ".jpg"
        with transaction(settings) as db:
            report = owner(db, report_id, request)
            if report["status"] != "draft":
                raise HTTPException(409, "Photos cannot change after analysis starts.")
            totals = db.execute("SELECT COUNT(*) n, COALESCE(SUM(size),0) bytes FROM photos WHERE report_id=?", (report_id,)).fetchone()
            if totals["n"] >= settings.max_photos or totals["bytes"] + len(data) > settings.max_report_bytes:
                raise HTTPException(413, "Report limit reached: 30 photos or 200 MiB total.")
            original = safe_path(settings, report_id, original_rel)
            normal = safe_path(settings, report_id, normalized_rel)
            original.parent.mkdir(parents=True, exist_ok=True)
            normal.parent.mkdir(parents=True, exist_ok=True)
            try:
                original.write_bytes(data)
                normal.write_bytes(normalized)
                db.execute("INSERT INTO photos(id,report_id,view,filename,original,normalized,size,sha256,uploaded_at) VALUES(?,?,?,?,?,?,?,?,?)", (photo_id, report_id, view, Path(filename.replace("\\", "/")).name, original_rel, normalized_rel, len(data), sha, time.time()))
                db.execute("UPDATE reports SET updated_at=? WHERE id=?", (time.time(), report_id))
            except Exception:
                original.unlink(missing_ok=True)
                normal.unlink(missing_ok=True)
                raise
        return {"id": photo_id, "view": view}

    @app.get("/api/reports/{report_id}/photos/{photo_id}")
    def preview(report_id: str, photo_id: str, request: Request):
        with connect(settings) as db:
            owner(db, report_id, request)
            photo = db.execute("SELECT * FROM photos WHERE id=? AND report_id=?", (photo_id, report_id)).fetchone()
            if not photo:
                raise HTTPException(404, "Photo not found.")
            data = safe_path(settings, report_id, photo["normalized"]).read_bytes()
        return StreamingResponse(io.BytesIO(data), media_type="image/jpeg")

    @app.delete("/api/reports/{report_id}/photos/{photo_id}")
    def remove_photo(report_id: str, photo_id: str, request: Request):
        with transaction(settings) as db:
            if owner(db, report_id, request)["status"] != "draft":
                raise HTTPException(409, "Photos cannot change after analysis starts.")
            photo = db.execute("SELECT * FROM photos WHERE id=? AND report_id=?", (photo_id, report_id)).fetchone()
            if not photo:
                raise HTTPException(404, "Photo not found.")
            for field in ("original", "normalized"):
                safe_path(settings, report_id, photo[field]).unlink(missing_ok=True)
            db.execute("DELETE FROM photos WHERE id=?", (photo_id,))
        return {"deleted": True}

    @app.post("/api/reports/{report_id}/analyze", status_code=202)
    def analyze(report_id: str, request: Request):
        if not settings.muse_key or not settings.muse_model:
            raise HTTPException(503, "Image analysis is not configured yet.")
        with transaction(settings) as db:
            report = owner(db, report_id, request)
            if report["status"] in {"queued", "processing", "ready"}:
                return {"status": report["status"]}
            if not db.execute("SELECT 1 FROM photos WHERE report_id=?", (report_id,)).fetchone():
                raise HTTPException(422, "Add at least one photo first.")
            if db.execute("SELECT COUNT(*) FROM jobs WHERE report_id=? AND kind='analyze'", (report_id,)).fetchone()[0] >= 3:
                raise HTTPException(429, "This report has reached its retry limit. Please contact the operator.")
            enqueue(db, report_id, "analyze")
            db.execute("UPDATE reports SET status='queued',error=NULL,updated_at=? WHERE id=?", (time.time(), report_id))
        return {"status": "queued"}

    @app.post("/api/reports/{report_id}/email", status_code=202)
    def email(report_id: str, request: Request, body: EmailInput):
        if not settings.email_enabled:
            raise HTTPException(503, "Email delivery is not configured on this server. Download your report or copy its link instead.")
        try:
            address = validate_email(body.email, check_deliverability=False).normalized
        except EmailNotValidError:
            raise HTTPException(422, "Enter a valid email address.")
        with transaction(settings) as db:
            report = owner(db, report_id, request)
            if report["status"] != "ready" or not secrets.compare_digest(report["share_hash"], digest(body.share_token)):
                raise HTTPException(409, "The report or its share link is not ready.")
            rate(db, "email:" + report_id, 3)
            job_id = enqueue(db, report_id, "email", json.dumps({"email": address, "url": settings.base_url + "/r/" + body.share_token}))
        return {"job_id": job_id, "status": "queued"}

    @app.get("/r/{token}", response_class=HTMLResponse)
    def result(token: str, request: Request):
        with connect(settings) as db:
            report = report_data(db, shared(db, token)["id"])
        return templates.TemplateResponse(request=request, name="report.html", context={"report": report, "token": token, "share_url": settings.base_url + "/r/" + token})

    def acquire_download(token):
        with transaction(settings) as db:
            row = shared(db, token)
            lease = secrets.token_hex(16)
            db.execute("INSERT INTO download_leases VALUES(?,?,?)", (lease, row["id"], time.time() + 3600))
            return row["id"], lease

    def release_download(lease):
        with transaction(settings) as db:
            db.execute("DELETE FROM download_leases WHERE id=?", (lease,))

    @app.get("/r/{token}/photos/{photo_id}")
    def shared_photo(token: str, photo_id: str):
        report_id, lease = acquire_download(token)
        try:
            with connect(settings) as db:
                photo = db.execute("SELECT * FROM photos WHERE id=? AND report_id=?", (photo_id, report_id)).fetchone()
                if photo is None:
                    raise HTTPException(404, "Photo not found.")
                photo = dict(photo)
                photo["analysis"] = json.loads(photo["analysis"])
            img = annotated_image(settings, report_id, photo)
            buffer = io.BytesIO()
            img.save(buffer, "JPEG", quality=90)
            buffer.seek(0)
            return StreamingResponse(buffer, media_type="image/jpeg")
        finally:
            release_download(lease)

    @app.get("/r/{token}/download/{kind}")
    def download(token: str, kind: str):
        if kind not in {"pdf", "photos"}:
            raise HTTPException(404, "Download not found.")
        report_id, lease = acquire_download(token)
        try:
            if kind == "pdf":
                return FileResponse(report_dir(settings, report_id) / "artifacts/report.pdf", media_type="application/pdf", filename="rental-condition-report.pdf", background=BackgroundTask(release_download, lease))
            archive = tempfile.TemporaryFile()
            with connect(settings) as db:
                photos = list(db.execute("SELECT * FROM photos WHERE report_id=? ORDER BY uploaded_at,id", (report_id,)))
            with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as output:
                for index, photo in enumerate(photos, 1):
                    path = safe_path(settings, report_id, photo["original"])
                    output.write(path, "%02d_%s_%s%s" % (index, photo["view"], photo["id"], path.suffix))
            archive.seek(0)

            def chunks():
                try:
                    while True:
                        chunk = archive.read(65536)
                        if not chunk:
                            break
                        yield chunk
                finally:
                    archive.close()
                    release_download(lease)

            return StreamingResponse(chunks(), media_type="application/zip", headers={"Content-Disposition": 'attachment; filename="rental-photos.zip"'})
        except BaseException:
            release_download(lease)
            raise

    return app


app = create_app()
