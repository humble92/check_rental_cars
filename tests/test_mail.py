import time
from app.config import Settings
from app.mail import send_report


def test_smtp_tls_attachment_and_stable_message_id(tmp_path, monkeypatch):
    settings = Settings(tmp_path)
    settings.smtp_host, settings.smtp_from = "smtp.example.com", "reports@example.com"
    settings.smtp_user, settings.smtp_password = "test-user", "test-password"
    target = tmp_path / "reports/sample/artifacts"
    target.mkdir(parents=True)
    (target / "report.pdf").write_bytes(b"%PDF-test")
    events = []

    class SMTP:
        def __init__(self, *args, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def starttls(self, **kwargs): events.append("tls")
        def login(self, *args): events.append("login")
        def send_message(self, message): events.append(message); return {}

    monkeypatch.setattr("app.mail.smtplib.SMTP", SMTP)
    report = {"id": "sample", "summary": "Two observations.", "expires_at": time.time() + 86400, "disclaimer": "Review observations."}
    send_report(settings, report, {"email": "renter@example.com", "url": "https://example.com/r/test"}, 42)
    assert events[:2] == ["tls", "login"]
    message = events[2]
    assert message["To"] == "renter@example.com"
    assert message["Message-ID"] == "<rental-sample-42@example.com>"
    assert "https://example.com/r/test" in message.get_body(preferencelist=("plain",)).get_content()
    assert next(message.iter_attachments()).get_filename() == "rental-condition-report.pdf"
