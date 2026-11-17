"""Idempotent retention cleanup; schedule this command outside the web process."""
import json
import shutil
import time
from .config import Settings
from .db import connect, initialize, transaction
from .storage import report_dir


def cleanup(settings, now=None):
    now = now or time.time()
    deleted, failures = 0, 0
    # Claim targets atomically. Active work and downloads keep their files until release.
    with transaction(settings) as db:
        db.execute("DELETE FROM rate_limits WHERE expires_at<=?", (now,))
        db.execute("DELETE FROM download_leases WHERE expires_at<=?", (now,))
        rows = db.execute("""SELECT r.id FROM reports r WHERE
            (r.status='deleting' OR r.expires_at<=? OR
             (r.expires_at IS NULL AND r.updated_at<=?))
            AND NOT EXISTS(SELECT 1 FROM jobs j WHERE j.report_id=r.id AND j.status='running' AND j.lease_until>?)
            AND NOT EXISTS(SELECT 1 FROM download_leases d WHERE d.report_id=r.id AND d.expires_at>?)""",
            (now, now - 86400, now, now)).fetchall()
        ids = [row["id"] for row in rows]
        for report_id in ids:
            db.execute("UPDATE reports SET status='deleting' WHERE id=?", (report_id,))
            db.execute("UPDATE jobs SET status='cancelled',payload=NULL WHERE report_id=?", (report_id,))
    for report_id in ids:
        try:
            path = report_dir(settings, report_id)
            if path.exists():
                shutil.rmtree(path)
            with transaction(settings) as db:
                db.execute("DELETE FROM reports WHERE id=? AND status='deleting'", (report_id,))
            deleted += 1
        except (OSError, ValueError):
            failures += 1
    # No app writes orphan report directories: rows are created before directories.
    # A 24-hour grace period protects interrupted creation and manual recovery.
    with connect(settings) as db:
        known = {row[0] for row in db.execute("SELECT id FROM reports")}
    for directory in (settings.data_dir / "reports").iterdir():
        if directory.name not in known and directory.is_dir() and directory.stat().st_mtime < now - 86400:
            try:
                safe = report_dir(settings, directory.name)
                shutil.rmtree(safe)
            except (OSError, ValueError):
                failures += 1
    result = {"deleted_reports": deleted, "failures": failures, "checked_at": now, "free_bytes": shutil.disk_usage(settings.data_dir).free}
    (settings.data_dir / "cleanup-status.json").write_text(json.dumps(result), encoding="utf-8")
    return result


def main():
    settings = Settings()
    initialize(settings)
    result = cleanup(settings)
    print(json.dumps(result))
    if result["failures"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
