from email.message import EmailMessage
from email.utils import formatdate
import smtplib
import ssl

from .reports import utc
from .storage import report_dir


def send_report(settings, report, payload, job_id):
    if not settings.email_enabled:
        raise RuntimeError("Email delivery is not configured.")
    msg = EmailMessage()
    msg["Subject"] = "Your rental condition report"
    msg["From"] = settings.smtp_from
    msg["To"] = payload["email"]
    msg["Date"] = formatdate(localtime=False)
    msg["Message-ID"] = "<rental-%s-%s@%s>" % (report["id"], job_id, settings.smtp_from.split("@")[-1])
    msg.set_content("Your rental condition report is ready.\n\n" + report["summary"] + "\n\n" + payload["url"] +
                    "\n\nThis link expires " + utc(report["expires_at"]) + ". Anyone with the link can view the report.\n\n" +
                    "The attached PDF, if included, remains in your mailbox after the link expires.\n\n" + report["disclaimer"])
    pdf = report_dir(settings, report["id"]) / "artifacts/report.pdf"
    if pdf.stat().st_size <= 8 * 1024 * 1024:
        msg.add_attachment(pdf.read_bytes(), maintype="application", subtype="pdf", filename="rental-condition-report.pdf")
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as server:
        if settings.smtp_tls:
            server.starttls(context=ssl.create_default_context())
        if settings.smtp_user:
            server.login(settings.smtp_user, settings.smtp_password)
        refused = server.send_message(msg)
        if refused:
            raise RuntimeError("The email recipient was rejected.")
