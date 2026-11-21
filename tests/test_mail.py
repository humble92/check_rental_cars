import time
import pytest
from app.config import Settings
from app.mail import send_report


@pytest.mark.parametrize("pdf_size, advertised_size, attached", [
    (9, None, True),
    (15 * 1024**2, "0", True),
    (18 * 1024**2, "50000000", False),  # Base64 line breaks push it over 25 MB.
    (30 * 1024**2, None, False),
    (1024, "1500", False),  # SMTP server publishes a lower limit.
])
def test_smtp_tls_attachment_and_stable_message_id(tmp_path, monkeypatch, pdf_size, advertised_size, attached):
    settings = Settings(tmp_path)
    settings.smtp_host, settings.smtp_from = "smtp.example.com", "reports@example.com"
    settings.smtp_user, settings.smtp_password = "test-user", "test-password"
    target = tmp_path / "reports/sample/artifacts"
    target.mkdir(parents=True)
    pdf_bytes = b"%PDF-test" + b" " * (pdf_size - 9)
    (target / "report.pdf").write_bytes(pdf_bytes)
    events = []

    class SMTP:
        def __init__(self, *args, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def starttls(self, **kwargs): events.append("tls")
        def login(self, *args): events.append("login")
        def ehlo_or_helo_if_needed(self):
            self.esmtp_features = {} if advertised_size is None else {"size": advertised_size}
        def send_message(self, message): events.append(message); return {}

    monkeypatch.setattr("app.mail.smtplib.SMTP", SMTP)
    report = {"id": "sample", "summary": "Two observations.", "expires_at": time.time() + 86400, "disclaimer": "Review observations."}
    send_report(settings, report, {"email": "renter@example.com", "url": "https://example.com/r/test"}, 42)
    assert events[:2] == ["tls", "login"]
    message = events[2]
    assert message["To"] == "renter@example.com"
    assert message["Message-ID"] == "<rental-sample-42@example.com>"
    body = message.get_body(preferencelist=("plain",)).get_content()
    assert "https://example.com/r/test" in body
    assert "The links expire" in body
    limit = min(25_000_000, int(advertised_size or 0) or 25_000_000)
    assert len(message.as_bytes()) <= limit
    attachments = list(message.iter_attachments())
    if attached:
        assert len(attachments) == 1
        assert attachments[0].get_filename() == "rental-condition-report.pdf"
        assert attachments[0].get_payload(decode=True) == pdf_bytes
    else:
        assert not attachments
        assert "The PDF exceeds the email size limit" in body
        assert "https://example.com/r/test/download/pdf" in body
