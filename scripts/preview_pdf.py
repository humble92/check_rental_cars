"""Create an explicitly synthetic QA report using the supplied test photos."""
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import fitz
from app.config import Settings
from app.db import initialize, transaction, connect
from app.reports import generate_pdf, report_data
from app.storage import prepare_image, safe_path


def main():
    settings = Settings(ROOT / "tmp/pdf-preview")
    initialize(settings)
    now = time.time()
    report_id = "qa-preview"
    with transaction(settings) as db:
        db.execute("DELETE FROM reports WHERE id=?", (report_id,))
        db.execute("INSERT INTO reports(id,owner_hash,share_hash,status,vehicle,notes,created_at,updated_at,completed_at,expires_at,summary,model) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                   (report_id, "unused-owner", "unused-share", "ready", "QA sample - not an AI assessment", "Synthetic layout fixture. These observations are for rendering verification only, not findings about the supplied images.", now, now, now, now + 7 * 86400, "Two submitted photos. Synthetic observations for layout verification only.", "synthetic-test-fixture"))
        for index, path in enumerate(sorted((ROOT / "resources/test").glob("*.jpg"))):
            data = path.read_bytes()
            normalized, extension, sha = prepare_image(data)
            relative = "normalized/%d.jpg" % index
            target = safe_path(settings, report_id, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(normalized)
            analysis = {"vehicle_visible": True, "view_description": "Synthetic QA description. Inspect the source photograph independently.", "quality": "limited", "limitations": ["This fixture verifies layout, not analysis quality."], "findings": [{"part": "Sample panel", "kind": "sample observation", "description": "A placeholder observation used to verify photo evidence placement, readable labels, and consistent PDF/HTML content. This is not a conclusion about damage in this photograph.", "certainty": "uncertain", "box": None}]}
            db.execute("INSERT INTO photos VALUES(?,?,?,?,?,?,?,?,?,?)", (str(index), report_id, "extra", path.name, relative, relative, len(data), sha, now, json.dumps(analysis)))
    with connect(settings) as db:
        report = report_data(db, report_id)
    output = generate_pdf(settings, report)
    with fitz.open(output) as document:
        for index, page in enumerate(document):
            page.get_pixmap(matrix=fitz.Matrix(1.25, 1.25)).save(str(ROOT / "tmp/pdf-preview" / ("page-%d.png" % (index + 1))))
        print("Rendered %d pages to tmp/pdf-preview." % len(document))


if __name__ == "__main__":
    main()
