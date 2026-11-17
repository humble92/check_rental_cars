from contextlib import contextmanager
import hashlib
import sqlite3
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS reports (
 id TEXT PRIMARY KEY, owner_hash TEXT NOT NULL, share_hash TEXT NOT NULL UNIQUE,
 status TEXT NOT NULL DEFAULT 'draft', vehicle TEXT NOT NULL DEFAULT '',
 plate TEXT NOT NULL DEFAULT '', company TEXT NOT NULL DEFAULT '',
 location TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '',
 created_at REAL NOT NULL, updated_at REAL NOT NULL, completed_at REAL,
 expires_at REAL, error TEXT, summary TEXT NOT NULL DEFAULT '', model TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS photos (
 id TEXT PRIMARY KEY, report_id TEXT NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
 view TEXT NOT NULL, filename TEXT NOT NULL, original TEXT NOT NULL,
 normalized TEXT NOT NULL, size INTEGER NOT NULL, sha256 TEXT NOT NULL,
 uploaded_at REAL NOT NULL, analysis TEXT
);
CREATE TABLE IF NOT EXISTS jobs (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 report_id TEXT NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
 kind TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued', payload TEXT,
 attempts INTEGER NOT NULL DEFAULT 0, available_at REAL NOT NULL,
 lease_until REAL, lease_token TEXT, error TEXT, created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS jobs_ready ON jobs(status, available_at);
CREATE INDEX IF NOT EXISTS photos_report ON photos(report_id);
CREATE TABLE IF NOT EXISTS rate_limits (
 bucket TEXT PRIMARY KEY, count INTEGER NOT NULL, expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS download_leases (
 id TEXT PRIMARY KEY, report_id TEXT NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
 expires_at REAL NOT NULL
);
"""


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


def connect(settings):
    db = sqlite3.connect(str(settings.data_dir / "app.sqlite3"), timeout=15, factory=ClosingConnection)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA busy_timeout=15000")
    return db


def initialize(settings):
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    (settings.data_dir / "reports").mkdir(exist_ok=True)
    with connect(settings) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript(SCHEMA)


@contextmanager
def transaction(settings):
    db = connect(settings)
    try:
        db.execute("BEGIN IMMEDIATE")
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def enqueue(db, report_id, kind, payload=None):
    now = time.time()
    return db.execute("INSERT INTO jobs(report_id,kind,payload,available_at,created_at) VALUES(?,?,?,?,?)",
                      (report_id, kind, payload, now, now)).lastrowid
