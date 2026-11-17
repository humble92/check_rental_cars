import io
import json
import time
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pypdf import PdfReader

from app.config import Settings
from app.db import connect, transaction, enqueue
from app.main import create_app
from app.cleanup import cleanup
from app.worker import claim, run_job
from app.storage import report_dir


ANALYSIS = {"vehicle_visible": True, "view_description": "A silver vehicle door is visible.", "quality": "limited", "limitations": ["Reflections obscure part of the lower door."], "findings": [{"part": "Front-left door", "kind": "scratch", "description": "A narrow light mark is visible near the handle; confirm it is not a reflection.", "certainty": "uncertain", "box": [0.2, 0.3, 0.3, 0.2]}]}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    settings = Settings(tmp_path)
    settings.muse_key = "test-key"
    settings.muse_model = "test-model"
    settings.min_free_bytes = 0
    settings.smtp_host = ""
    settings.smtp_from = ""
    monkeypatch.setattr("app.worker.analyze_photo", lambda *args: ANALYSIS)
    with TestClient(create_app(settings)) as client:
        yield client, settings


def image_bytes():
    buffer = io.BytesIO()
    Image.new("RGB", (800, 600), "silver").save(buffer, "JPEG")
    return buffer.getvalue()


def create_report(client):
    response = client.post("/api/reports", json={"vehicle": "Silver test vehicle", "notes": "Check the passenger door."})
    assert response.status_code == 201
    session = response.json()
    headers = {"Authorization": "Bearer " + session["owner_token"]}
    return session, headers


def upload(client, session, headers, view="front"):
    response = client.post("/api/reports/%s/photos?view=%s&filename=test.jpg" % (session["id"], view), headers=headers, content=image_bytes())
    assert response.status_code == 201, response.text
    return response.json()["id"]


def ready(client, settings):
    session, headers = create_report(client)
    photo_id = upload(client, session, headers)
    assert client.post("/api/reports/%s/analyze" % session["id"], headers=headers).status_code == 202
    run_job(settings, claim(settings))
    status = client.get("/api/reports/" + session["id"], headers=headers).json()
    assert status["status"] == "ready", status
    return session, headers, photo_id


def test_end_to_end_pdf_html_zip(setup):
    client, settings = setup
    session, headers, photo_id = ready(client, settings)
    base = "/r/" + session["share_token"]
    html = client.get(base)
    assert html.status_code == 200
    assert "Check the passenger door." in html.text
    assert ANALYSIS["findings"][0]["description"] in html.text
    pdf = client.get(base + "/download/pdf")
    assert pdf.status_code == 200
    reader = PdfReader(io.BytesIO(pdf.content))
    assert len(reader.pages) == 2
    text = " ".join(page.extract_text() for page in reader.pages)
    assert "Check the passenger door." in text
    assert "narrow light mark" in text
    assert len(reader.pages[1].images) == 1
    archive = client.get(base + "/download/photos")
    with zipfile.ZipFile(io.BytesIO(archive.content)) as z:
        assert len(z.namelist()) == 1
        assert z.read(z.namelist()[0]) == image_bytes()
    assert client.get(base + "/photos/" + photo_id).status_code == 200
    assert html.headers["cache-control"] == "no-store"
    assert html.headers["referrer-policy"] == "no-referrer"
    with connect(settings) as db:
        assert db.execute("SELECT COUNT(*) FROM download_leases").fetchone()[0] == 0


def test_homepage_assets_and_contributor_disclosure(setup):
    client, settings = setup
    settings.muse_model = "muse-spark-1.3-contributor"
    page = client.get("/")
    assert page.status_code == 200
    assert 'lang="en"' in page.text
    assert page.text.count('class="photo-slot"') == 12
    assert 'data-upload="extra"' in page.text
    assert "Meta may use submitted photos" in page.text
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/app.css").status_code == 200
    settings.muse_model = "muse-ember-1.0"
    assert "Meta may use submitted photos" not in client.get("/").text


def test_permissions_and_escaping(setup):
    client, settings = setup
    session, headers = create_report(client)
    report = "/api/reports/" + session["id"]
    assert client.get(report).status_code == 404
    assert client.get(report, headers={"Authorization": "Bearer " + session["share_token"]}).status_code == 404
    assert client.patch(report, headers=headers, json={"notes": "<script>alert(1)</script>"}).status_code == 200
    upload(client, session, headers)
    client.post(report + "/analyze", headers=headers)
    run_job(settings, claim(settings))
    assert client.patch(report, headers=headers, json={"notes": "changed"}).status_code == 409
    html = client.get("/r/" + session["share_token"]).text
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
    assert client.post("/api/reports", json={}, headers={"Origin": "https://evil.example"}).status_code == 403


def test_expiration_all_routes_and_cleanup(setup):
    client, settings = setup
    session, headers, photo_id = ready(client, settings)
    with transaction(settings) as db:
        db.execute("UPDATE reports SET expires_at=? WHERE id=?", (time.time() - 1, session["id"]))
    base = "/r/" + session["share_token"]
    for suffix in ("", "/download/pdf", "/download/photos", "/photos/" + photo_id):
        assert client.get(base + suffix).status_code == 410
    assert client.get("/api/reports/" + session["id"], headers=headers).status_code == 410
    assert cleanup(settings)["deleted_reports"] == 1
    assert not report_dir(settings, session["id"]).exists()
    assert cleanup(settings)["deleted_reports"] == 0


def test_active_download_and_job_protect_cleanup(setup):
    client, settings = setup
    session, headers = create_report(client)
    upload(client, session, headers)
    client.post("/api/reports/%s/analyze" % session["id"], headers=headers)
    job = claim(settings)
    with transaction(settings) as db:
        db.execute("UPDATE reports SET updated_at=? WHERE id=?", (time.time() - 90000, session["id"]))
    assert cleanup(settings)["deleted_reports"] == 0
    with transaction(settings) as db:
        db.execute("UPDATE jobs SET lease_until=0 WHERE id=?", (job["id"],))
        db.execute("INSERT INTO download_leases VALUES(?,?,?)", ("test", session["id"], time.time() + 100))
    assert cleanup(settings)["deleted_reports"] == 0
    with transaction(settings) as db:
        db.execute("DELETE FROM download_leases")
    assert cleanup(settings)["deleted_reports"] == 1
    assert claim(settings) is None


def test_retries_checkpoint_and_crash_recovery(setup, monkeypatch):
    client, settings = setup
    session, headers = create_report(client)
    upload(client, session, headers)
    upload(client, session, headers, "rear")
    client.post("/api/reports/%s/analyze" % session["id"], headers=headers)
    calls = []

    def partial(*args):
        calls.append(args)
        if len(calls) == 2:
            raise RuntimeError("provider unavailable")
        return ANALYSIS

    monkeypatch.setattr("app.worker.analyze_photo", partial)
    job = claim(settings)
    assert claim(settings) is None
    run_job(settings, job)
    with transaction(settings) as db:
        assert db.execute("SELECT COUNT(*) FROM photos WHERE analysis IS NOT NULL").fetchone()[0] == 1
        db.execute("UPDATE jobs SET available_at=0")
    recovered = claim(settings)
    with transaction(settings) as db:
        db.execute("UPDATE jobs SET lease_until=0")
    reclaimed = claim(settings)
    assert reclaimed["lease_token"] != recovered["lease_token"]
    run_job(settings, reclaimed)
    assert len(calls) == 3
    assert client.get("/api/reports/" + session["id"], headers=headers).json()["status"] == "ready"


def test_invalid_uploads_limits_and_empty_analysis(setup):
    client, settings = setup
    session, headers = create_report(client)
    base = "/api/reports/" + session["id"]
    assert client.post(base + "/analyze", headers=headers).status_code == 422
    assert client.post(base + "/photos", headers=headers, content=b"not an image").status_code == 422
    assert client.post(base + "/photos?view=not-valid", headers=headers, content=image_bytes()).status_code == 422
    settings.max_photos = 1
    upload(client, session, headers)
    assert client.post(base + "/photos", headers=headers, content=image_bytes()).status_code == 413
    response = client.post(base + "/photos", headers={**headers, "Content-Length": str(16 * 1024 * 1024)}, content=b"x")
    assert response.status_code == 413


def test_email_queue_success_failure_and_address_erasure(setup, monkeypatch):
    client, settings = setup
    session, headers, _ = ready(client, settings)
    endpoint = "/api/reports/%s/email" % session["id"]
    payload = {"email": "renter@example.com", "share_token": session["share_token"]}
    assert client.post(endpoint, headers=headers, json=payload).status_code == 503
    settings.smtp_host, settings.smtp_from = "smtp.example.com", "reports@example.com"
    assert client.post(endpoint, headers=headers, json={**payload, "email": "bad"}).status_code == 422
    calls = []
    monkeypatch.setattr("app.worker.send_report", lambda *args: calls.append(args))
    assert client.post(endpoint, headers=headers, json=payload).status_code == 202
    run_job(settings, claim(settings))
    assert len(calls) == 1
    with connect(settings) as db:
        row = db.execute("SELECT * FROM jobs WHERE kind='email'").fetchone()
        assert row["status"] == "done"
        assert row["payload"] is None
    before = client.get("/api/reports/" + session["id"], headers=headers).json()["expires_at"]
    client.post(endpoint, headers=headers, json=payload)
    assert client.get("/api/reports/" + session["id"], headers=headers).json()["expires_at"] == before


def test_terminal_failure_is_honest_and_bounded(setup, monkeypatch):
    client, settings = setup
    session, headers = create_report(client)
    upload(client, session, headers)
    endpoint = "/api/reports/" + session["id"]
    client.post(endpoint + "/analyze", headers=headers)

    def failing(*args):
        raise RuntimeError("secret must not be persisted")

    monkeypatch.setattr("app.worker.analyze_photo", failing)
    for _ in range(3):
        with transaction(settings) as db:
            db.execute("UPDATE jobs SET available_at=0")
        run_job(settings, claim(settings))
    state = client.get(endpoint, headers=headers).json()
    assert state["status"] == "failed"
    assert "secret" not in state["error"]
    assert client.get("/r/" + session["share_token"]).status_code == 409


def test_safe_deletion_rejects_paths_outside_root(setup):
    _, settings = setup
    for value in ("..", "../outside", "", str(settings.data_dir.parent)):
        with pytest.raises(ValueError):
            report_dir(settings, value)


def test_draft_retention_rate_limit_and_no_lifetime_extension(setup):
    client, settings = setup
    session, headers = create_report(client)
    with transaction(settings) as db:
        db.execute("UPDATE reports SET updated_at=? WHERE id=?", (time.time() - 86401, session["id"]))
    assert client.get("/api/reports/" + session["id"], headers=headers).status_code == 410
    assert cleanup(settings)["deleted_reports"] == 1
    for _ in range(9):
        assert client.post("/api/reports", json={}).status_code == 201
    assert client.post("/api/reports", json={}).status_code == 429
