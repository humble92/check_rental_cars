"""Explicit paid end-to-end check with the configured Meta model and test images."""
import argparse
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import fitz
from fastapi.testclient import TestClient
from app.config import Settings
from app.main import create_app
from app.worker import claim, run_job
from app.db import connect
from app.reports import report_data, generate_pdf


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--render-only", action="store_true", help="Re-render the most recent completed live test without API calls.")
    args = parser.parse_args()
    settings = Settings(ROOT / "tmp/live-report-data")
    if args.render_only:
        with connect(settings) as db:
            row = db.execute("SELECT id FROM reports WHERE status='ready' ORDER BY completed_at DESC LIMIT 1").fetchone()
            if not row:
                raise SystemExit("No completed live test report found.")
            report = report_data(db, row["id"])
        pdf = generate_pdf(settings, report)
        output = ROOT / "tmp/live-report"
        shutil.copyfile(pdf, output / "report.pdf")
        with fitz.open(pdf) as document:
            for index, page in enumerate(document):
                page.get_pixmap(matrix=fitz.Matrix(1.25, 1.25)).save(str(output / ("page-%d.png" % (index + 1))))
        print("Re-rendered existing live report without API calls.")
        return
    with TestClient(create_app(settings)) as client:
        response = client.post("/api/reports", json={"vehicle": "Sample rental vehicle", "notes": "Test report generated from the two supplied close-up photographs. Exact vehicle and panel details were not provided."})
        response.raise_for_status()
        session = response.json()
        headers = {"Authorization": "Bearer " + session["owner_token"]}
        for path in sorted((ROOT / "resources/test").glob("*.jpg")):
            upload = client.post("/api/reports/%s/photos?view=extra&filename=%s" % (session["id"], path.name), content=path.read_bytes(), headers=headers)
            upload.raise_for_status()
        submitted = client.post("/api/reports/%s/analyze" % session["id"], headers=headers)
        submitted.raise_for_status()
        print("Running the real image-analysis worker with " + settings.muse_model, flush=True)
        job = claim(settings)
        if job is None or job["report_id"] != session["id"]:
            raise SystemExit("Another test job is pending; finish it before running this check.")
        run_job(settings, job)
        status = client.get("/api/reports/" + session["id"], headers=headers).json()
        if status["status"] != "ready":
            raise SystemExit("Live report did not complete: " + str(status["error"]))
        base = "/r/" + session["share_token"]
        html, pdf, archive = client.get(base), client.get(base + "/download/pdf"), client.get(base + "/download/photos")
        for response in (html, pdf, archive):
            response.raise_for_status()
        output = ROOT / "tmp/live-report"
        output.mkdir(parents=True, exist_ok=True)
        (output / "report.html").write_text(html.text, encoding="utf-8")
        (output / "report.pdf").write_bytes(pdf.content)
        (output / "photos.zip").write_bytes(archive.content)
        with fitz.open(stream=pdf.content, filetype="pdf") as document:
            for index, page in enumerate(document):
                page.get_pixmap(matrix=fitz.Matrix(1.25, 1.25)).save(str(output / ("page-%d.png" % (index + 1))))
            print("PASS: real AI -> ready report -> HTML, %d-page PDF, original-photo ZIP." % len(document))
        print("Artifacts saved under tmp/live-report. No email was sent.")


if __name__ == "__main__":
    main()
