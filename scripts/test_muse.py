"""Explicit paid comparison of the requested Meta models with local images."""
import argparse
import base64
import io
import json
import os
from pathlib import Path
import sys
import time

import httpx
from dotenv import load_dotenv
from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.ai import PROMPT
from app.models import PhotoAnalysis


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", nargs="+", default=["muse-spark-1.3-contributor", "muse-ember-1.0"])
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    key = os.getenv("MUSE_API_KEY")
    if not key:
        raise SystemExit("Missing MUSE_API_KEY.")
    images = sorted((ROOT / "resources/test").glob("*.jpg"))
    if not images:
        raise SystemExit("No test images found.")
    results = []
    for model in args.models:
        for path in images:
            with Image.open(path) as source:
                img = ImageOps.exif_transpose(source).convert("RGB")
                img.thumbnail((2048, 2048))
                buffer = io.BytesIO()
                img.save(buffer, format="JPEG", quality=90)
            content = [{"type": "text", "text": "Inspect the actual visible image. View label: Additional photo."},
                       {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode()}}]
            start = time.monotonic()
            record = {"model": model, "image": path.name}
            print("Testing %s / %s" % (model, path.name), flush=True)
            try:
                response = httpx.post("https://api.meta.ai/v1/chat/completions", headers={"Authorization": "Bearer " + key},
                    json={"model": model, "messages": [{"role": "system", "content": PROMPT}, {"role": "user", "content": content}], "max_completion_tokens": 3500, "reasoning_effort": "low"}, timeout=180)
                record["http_status"] = response.status_code
                if response.status_code != 200:
                    record["error"] = response.text.replace(key, "[REDACTED]")[:1200]
                else:
                    body = response.json()
                    choice = body["choices"][0]
                    answer = choice["message"].get("content") or ""
                    record.update({"answer": answer, "usage": body.get("usage"), "finish_reason": choice.get("finish_reason")})
                    text = answer.strip()
                    if text.startswith("```"):
                        text = text.split("\n", 1)[1].rsplit("```", 1)[0]
                    try:
                        record["analysis"] = PhotoAnalysis.model_validate(json.loads(text)).model_dump()
                        record["schema_valid"] = True
                    except Exception as exc:
                        record["schema_valid"] = False
                        record["schema_error"] = type(exc).__name__
            except httpx.RequestError as exc:
                record["error"] = "Connection failed: " + type(exc).__name__
            record["seconds"] = round(time.monotonic() - start, 2)
            results.append(record)
            print(json.dumps(record, ensure_ascii=True), flush=True)
            target = ROOT / "tmp/muse-smoke.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(results, indent=2), encoding="utf-8")
    return 0 if all(record.get("schema_valid") for record in results) else 1


if __name__ == "__main__":
    sys.exit(main())
