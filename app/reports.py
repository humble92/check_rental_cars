from datetime import datetime, timezone
import io
import json
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image, PageBreak, KeepTogether

from .config import VIEW_LABELS, VIEWS
from .storage import report_dir, annotated_image

DISCLAIMER = "AI-assisted observations from submitted photographs, not proof of when damage occurred. Reflections, dirt, lighting, and unseen areas may hide or resemble damage. Review every observation before sharing. No detected damage does not establish a damage-free vehicle."


def utc(value):
    return datetime.fromtimestamp(value, timezone.utc).strftime("%b %d, %Y at %H:%M UTC") if value else "Not yet available"


def report_data(db, report_id):
    row = db.execute("SELECT * FROM reports WHERE id=?", (report_id,)).fetchone()
    report = dict(row)
    for secret in ("owner_hash", "share_hash"):
        report.pop(secret, None)
    photos = []
    for row in db.execute("SELECT * FROM photos WHERE report_id=? ORDER BY uploaded_at,id", (report_id,)):
        photo = dict(row)
        photo["analysis"] = json.loads(photo["analysis"]) if photo["analysis"] else None
        photo["label"] = VIEW_LABELS[photo["view"]]
        photos.append(photo)
    report["photos"] = photos
    present = {photo["view"] for photo in photos}
    report["missing_views"] = [label for key, label in VIEWS if key not in present]
    report["finding_count"] = sum(len(p["analysis"]["findings"]) for p in photos if p["analysis"])
    report["disclaimer"] = DISCLAIMER
    return report


def generate_pdf(settings, report):
    destination = report_dir(settings, report["id"]) / "artifacts"
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / "report.pdf"
    temporary = destination / "report.pdf.tmp"
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="CaptionSmall", parent=styles["BodyText"], fontSize=8, leading=11, textColor=colors.HexColor("#52636b")))
    styles["Title"].textColor = colors.HexColor("#143d35")
    styles["BodyText"].spaceAfter = 8
    story = []

    def para(text, style="BodyText"):
        return Paragraph(escape(str(text)).replace("\n", "<br/>"), styles[style])

    story.extend([para("DEPARTURE / VEHICLE CONDITION", "CaptionSmall"), Spacer(1, 12), para("Rental condition report", "Title"),
                  para(report["summary"]), para("Report ID: " + report["id"], "CaptionSmall")])
    for label, value in [("Vehicle", report["vehicle"]), ("License plate", report["plate"]), ("Rental company", report["company"]), ("Pickup location", report["location"]), ("Generated", utc(report["completed_at"])), ("Link expires", utc(report["expires_at"]))]:
        story.append(para(label + ": " + (value or "Not provided")))
    story.extend([para("Your notes", "Heading2"), para(report["notes"] or "No notes provided."),
                  para("Coverage and limitations", "Heading2"),
                  para("Recommended views not submitted: " + (", ".join(report["missing_views"]) or "None")),
                  para(DISCLAIMER), para("Analysis model: " + report["model"], "CaptionSmall"),
                  para("Observations in different photos may show the same mark. The total is a count of observations, not unique damage.", "CaptionSmall")])
    for index, photo in enumerate(report["photos"], 1):
        story.extend([PageBreak(), para("Photo %02d / %s" % (index, photo["label"]), "Heading1"),
                      para("Uploaded " + utc(photo["uploaded_at"]) + " - server receipt time, not verified capture time.", "CaptionSmall")])
        img = annotated_image(settings, report["id"], photo)
        buffer = io.BytesIO()
        img.save(buffer, "JPEG", quality=90)
        buffer.seek(0)
        ratio = min(6.5 * inch / img.width, 3.8 * inch / img.height)
        story.extend([Image(buffer, width=img.width * ratio, height=img.height * ratio), Spacer(1, 10)])
        analysis = photo["analysis"]
        story.extend([para(analysis["view_description"]), para("Image quality: " + analysis["quality"])])
        for limitation in analysis["limitations"]:
            story.append(para("Limitation: " + limitation))
        if not analysis["findings"]:
            story.append(para("No visible damage identified in this photo. This is not a guarantee that no damage exists."))
        for number, finding in enumerate(analysis["findings"], 1):
            story.append(KeepTogether([para("%d. %s - %s (%s)" % (number, finding["part"], finding["kind"], finding["certainty"]), "Heading3"), para(finding["description"])]))
        story.append(para("Original SHA-256: " + photo["sha256"], "CaptionSmall"))

    def footer(canvas, doc):
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#52636b"))
        canvas.drawString(42, 26, "DEPARTURE / AI-assisted vehicle condition record")
        canvas.drawRightString(570, 26, "Page %d" % doc.page)

    SimpleDocTemplate(str(temporary), pagesize=(612, 792), leftMargin=42, rightMargin=42, topMargin=40, bottomMargin=45,
                      title="Rental condition report", author="Departure").build(story, onFirstPage=footer, onLaterPages=footer)
    temporary.replace(target)
    return target
