import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


class Settings:
    def __init__(self, data_dir=None):
        self.data_dir = Path(data_dir or os.getenv("DATA_DIR", ROOT / "data")).resolve()
        self.base_url = os.getenv("PUBLIC_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
        self.muse_key = os.getenv("MUSE_API_KEY", "")
        self.muse_model = os.getenv("MUSE_MODEL", "muse-spark-1.3-contributor")
        self.muse_url = "https://api.meta.ai/v1/chat/completions"
        self.max_photos = 30
        self.max_photo_bytes = 15 * 1024 * 1024
        self.max_report_bytes = 200 * 1024 * 1024
        self.min_free_bytes = int(os.getenv("MIN_FREE_DISK_MB", "512")) * 1024 * 1024
        self.smtp_host = os.getenv("SMTP_HOST", "")
        self.smtp_port = int(os.getenv("SMTP_PORT", "587"))
        self.smtp_user = os.getenv("SMTP_USER", "")
        self.smtp_password = os.getenv("SMTP_PASSWORD", "")
        self.smtp_from = os.getenv("SMTP_FROM", "")
        self.smtp_tls = os.getenv("SMTP_STARTTLS", "true").lower() == "true"
        self.lease_seconds = 900

    @property
    def email_enabled(self):
        return bool(self.smtp_host and self.smtp_from)


VIEWS = [
    ("front", "Front"), ("front_left", "Front-left diagonal"),
    ("front_right", "Front-right diagonal"), ("rear", "Rear"),
    ("rear_left", "Rear-left diagonal"), ("rear_right", "Rear-right diagonal"),
    ("left_full", "Left side - full"), ("left_front", "Left side - front seat"),
    ("left_rear", "Left side - rear seat"), ("right_full", "Right side - full"),
    ("right_front", "Right side - front seat"), ("right_rear", "Right side - rear seat"),
]
VIEW_LABELS = dict(VIEWS + [("extra", "Additional photo")])
