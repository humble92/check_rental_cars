from email.message import EmailMessage
from email.policy import SMTP
from email.utils import formatdate
import smtplib
import ssl

from .reports import utc
from .storage import report_dir

MAX_EMAIL_BYTES = 25_000_000


def send_report(settings, report, payload, job_id):
    if not settings.email_enabled:
        raise RuntimeError("Email delivery is not configured.")
    msg = EmailMessage(policy=SMTP)
    msg["Subject"] = "Your rental condition report"
    msg["From"] = settings.smtp_from
    msg["To"] = payload["email"]
    msg["Date"] = formatdate(localtime=False)
    msg["Message-ID"] = "<rental-%s-%s@%s>" % (report["id"], job_id, settings.smtp_from.split("@")[-1])
    url = payload["url"]
    intro = (
        f"Your rental condition report is ready.\n\n{report['summary']}\n\n"
        f"View report: {url}\n\n"
        f"The links expire {utc(report['expires_at'])}. Anyone with a link can view the report.\n\n"
    )
    msg.set_content(intro + "The attached PDF remains in your mailbox after the links expire.\n\n" + report["disclaimer"])
    pdf = report_dir(settings, report["id"]) / "artifacts/report.pdf"
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as server:
        if settings.smtp_tls:
            server.starttls(context=ssl.create_default_context())
        if settings.smtp_user:
            server.login(settings.smtp_user, settings.smtp_password)
        server.ehlo_or_helo_if_needed()
        limit = MAX_EMAIL_BYTES
        size = server.esmtp_features.get("size", "").strip()
        if size.isascii() and size.isdecimal() and int(size) > 0:
            limit = min(limit, int(size))

        # Base64 alone needs at least four bytes for every three PDF bytes.
        # Skip reading PDFs that already exceed the budget before MIME headers and line breaks.
        if pdf.stat().st_size <= 3 * (limit // 4):
            msg.add_attachment(pdf.read_bytes(), maintype="application", subtype="pdf", filename="rental-condition-report.pdf")
        if not msg.is_multipart() or len(msg.as_bytes()) > limit:
            msg.clear_content()
            msg.set_content(
                intro + "The PDF exceeds the email size limit when attached. "
                f"Download it before the links expire:\n{url}/download/pdf\n\n" + report["disclaimer"]
            )
        refused = server.send_message(msg)
        if refused:
            raise RuntimeError("The email recipient was rejected.")
