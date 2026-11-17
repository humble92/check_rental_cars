"""Explicit paid vision smoke test. Never prints API keys or request headers."""
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


def main():
    load_dotenv(ROOT / ".env")
    key, model = os.getenv("BASETEN_API_KEY"), os.getenv("BASETEN_MODEL")
    if not key or not model:
        raise SystemExit("Missing BASETEN_API_KEY or BASETEN_MODEL.")
    images = sorted((ROOT / "resources/test").glob("*.jpg"))
    if not images:
        raise SystemExit("No test images found.")
    results = []
    for path in images:
        with Image.open(path) as source:
            img = ImageOps.exif_transpose(source).convert("RGB")
            img.thumbnail((1536, 1536))
            buffer = io.BytesIO()
            img.save(buffer, format="JPEG", quality=85)
        content = [
            {"type": "text", "text": "Describe what is actually visible in this photograph in English. Identify whether a vehicle is visible, its visible parts, and any visible damage or uncertainty. Do not invent damage. Be specific to the image. Use at most 150 words."},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode()}},
        ]
        start = time.monotonic()
        try:
            response = httpx.post(
                "https://inference.baseten.co/v1/chat/completions",
                headers={"Authorization": "Bearer " + key},
                json={"model": model, "messages": [{"role": "user", "content": content}], "max_tokens": 1000},
                timeout=180,
            )
        except httpx.RequestError as exc:
            print("Connection failed: " + type(exc).__name__)
            return 2
        if response.status_code != 200:
            print("Baseten returned HTTP " + str(response.status_code))
            # Provider text is sanitized before display; do not dump request objects.
            print(response.text.replace(key, "[REDACTED]")[:1000])
            return 1
        body = response.json()
        answer = body.get("choices", [{}])[0].get("message", {}).get("content", "")
        record = {"image": path.name, "http_status": response.status_code, "seconds": round(time.monotonic() - start, 2), "answer": answer, "usage": body.get("usage")}
        results.append(record)
        print(json.dumps(record, ensure_ascii=True))
        if not answer:
            print("The provider returned no visible answer.")
            return 1
    target = ROOT / "tmp/baseten-smoke.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(results, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
