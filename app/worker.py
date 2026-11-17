"""Durable single-host worker. Run separately: python -m app.worker."""
import argparse
import json
import secrets
import time
import threading

from .ai import analyze_photo
from .config import Settings, VIEW_LABELS
from .db import connect, initialize, transaction
from .mail import send_report
from .reports import report_data, generate_pdf
from .storage import safe_path


def claim(settings):
    now = time.time()
    with transaction(settings) as db:
        db.execute("UPDATE jobs SET status='queued', lease_token=NULL, lease_until=NULL WHERE status='running' AND lease_until<?", (now,))
        row = db.execute("SELECT j.* FROM jobs j JOIN reports r ON r.id=j.report_id WHERE j.status='queued' AND j.available_at<=? AND r.status NOT IN ('deleting','expired') AND (r.expires_at IS NULL OR r.expires_at>?) ORDER BY j.id LIMIT 1", (now, now)).fetchone()
        if row is None:
            return None
        token = secrets.token_hex(16)
        db.execute("UPDATE jobs SET status='running', attempts=attempts+1, lease_until=?,lease_token=? WHERE id=?", (now + settings.lease_seconds, token, row["id"]))
        return dict(db.execute("SELECT * FROM jobs WHERE id=?", (row["id"],)).fetchone())


def owns(db, job):
    return db.execute("SELECT 1 FROM jobs j JOIN reports r ON r.id=j.report_id WHERE j.id=? AND j.lease_token=? AND j.status='running' AND r.status NOT IN ('deleting','expired')", (job["id"], job["lease_token"])).fetchone() is not None


def run_job(settings, job):
    stop = threading.Event()

    def heartbeat():
        while not stop.wait(30):
            try:
                with transaction(settings) as db:
                    db.execute("UPDATE jobs SET lease_until=? WHERE id=? AND lease_token=? AND status='running'", (time.time() + settings.lease_seconds, job["id"], job["lease_token"]))
            except Exception:
                pass

    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    try:
        if job["kind"] == "analyze":
            with transaction(settings) as db:
                if not owns(db, job):
                    return
                db.execute("UPDATE reports SET status='processing',error=NULL WHERE id=?", (job["report_id"],))
                photos = [dict(p) for p in db.execute("SELECT * FROM photos WHERE report_id=? ORDER BY uploaded_at,id", (job["report_id"],))]
            for photo in photos:
                if photo["analysis"]:
                    continue
                result = analyze_photo(settings, safe_path(settings, job["report_id"], photo["normalized"]), VIEW_LABELS[photo["view"]])
                with transaction(settings) as db:
                    if not owns(db, job):
                        return
                    db.execute("UPDATE photos SET analysis=? WHERE id=?", (json.dumps(result), photo["id"]))
            with connect(settings) as db:
                report = report_data(db, job["report_id"])
            report["completed_at"] = report["completed_at"] or time.time()
            report["expires_at"] = report["expires_at"] or report["completed_at"] + 7 * 86400
            report["model"] = settings.muse_model
            report["summary"] = "%d photo(s) reviewed. %d visible or uncertain observation(s) recorded. Review the evidence and coverage below." % (len(report["photos"]), report["finding_count"])
            generate_pdf(settings, report)
            with transaction(settings) as db:
                if not owns(db, job):
                    return
                db.execute("UPDATE reports SET status='ready',completed_at=?,expires_at=?,summary=?,model=?,updated_at=?,error=NULL WHERE id=?", (report["completed_at"], report["expires_at"], report["summary"], report["model"], time.time(), report["id"]))
        elif job["kind"] == "email":
            with connect(settings) as db:
                report = report_data(db, job["report_id"])
            if report["status"] != "ready" or report["expires_at"] <= time.time():
                raise RuntimeError("The report is no longer available.")
            send_report(settings, report, json.loads(job["payload"]), job["id"])
        else:
            raise RuntimeError("Unknown job type.")
        with transaction(settings) as db:
            db.execute("UPDATE jobs SET status='done',payload=NULL,lease_until=NULL,lease_token=NULL,error=NULL WHERE id=? AND lease_token=?", (job["id"], job["lease_token"]))
    except Exception as exc:
        # Do not persist raw exceptions, provider responses, addresses, or credentials.
        message = "Analysis could not be completed. Check the provider configuration and try again." if job["kind"] == "analyze" else "Email delivery failed. Check SMTP configuration or try again."
        print("Job %s (%s): %s" % (job["id"], job["kind"], type(exc).__name__), flush=True)
        with transaction(settings) as db:
            if not owns(db, job):
                return
            terminal = job["attempts"] >= 3
            db.execute("UPDATE jobs SET status=?,available_at=?,lease_until=NULL,lease_token=NULL,error=?,payload=CASE WHEN ? THEN NULL ELSE payload END WHERE id=?",
                       ("failed" if terminal else "queued", time.time() + min(300, 10 * 2 ** job["attempts"]), message, terminal, job["id"]))
            if job["kind"] == "analyze":
                db.execute("UPDATE reports SET status=?,error=?,updated_at=? WHERE id=?", ("failed" if terminal else "queued", message, time.time(), job["report_id"]))
    finally:
        stop.set()
        thread.join(timeout=2)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Process at most one job and exit.")
    args = parser.parse_args()
    settings = Settings()
    initialize(settings)
    print("Worker ready.", flush=True)
    while True:
        job = claim(settings)
        if job:
            run_job(settings, job)
        elif not args.once:
            time.sleep(2)
        if args.once:
            return


if __name__ == "__main__":
    main()
