import hashlib
import io
from pathlib import Path
import shutil
import warnings

from PIL import Image, ImageOps, ImageDraw, ImageFont
from pillow_heif import register_heif_opener

register_heif_opener()
Image.MAX_IMAGE_PIXELS = 40_000_000


def report_dir(settings, report_id):
    root = (settings.data_dir / "reports").resolve()
    candidate = root / report_id
    path = candidate.resolve()
    if path.parent != root or not report_id or candidate.is_symlink():
        raise ValueError("Invalid storage path")
    return path


def safe_path(settings, report_id, relative):
    root = report_dir(settings, report_id)
    path = (root / relative).resolve()
    if root not in path.parents:
        raise ValueError("Invalid file path")
    return path


def prepare_image(data):
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(data)) as source:
            fmt = source.format
            if fmt not in {"JPEG", "PNG", "WEBP", "HEIF"}:
                raise ValueError("Use JPEG, PNG, WebP, or HEIC images.")
            source.load()
            image = ImageOps.exif_transpose(source).convert("RGB")
            if min(image.size) < 64:
                raise ValueError("Photo must be at least 64 pixels on each side.")
            image.thumbnail((2048, 2048))
            buffer = io.BytesIO()
            image.save(buffer, "JPEG", quality=90)
    extension = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp", "HEIF": ".heic"}[fmt]
    return buffer.getvalue(), extension, hashlib.sha256(data).hexdigest()


def ensure_capacity(settings):
    if shutil.disk_usage(settings.data_dir).free < settings.min_free_bytes + settings.max_photo_bytes * 2:
        raise ValueError("Storage is temporarily full. Please try again later.")


def annotated_image(settings, report_id, photo):
    path = safe_path(settings, report_id, photo["normalized"])
    with Image.open(path) as source:
        img = source.convert("RGB")
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default(size=max(24, img.width // 45))
    for index, finding in enumerate(photo["analysis"]["findings"], 1):
        if finding.get("box"):
            x, y, w, h = finding["box"]
            left, top, right, bottom = x * img.width, y * img.height, (x + w) * img.width, (y + h) * img.height
            draw.rectangle((left, top, right, bottom), outline="#e79b25", width=max(3, img.width // 300))
            label = str(index)
            bounds = draw.textbbox((0, 0), label, font=font)
            label_width = bounds[2] - bounds[0] + 16
            label_height = bounds[3] - bounds[1] + 16
            label_left = min(left, img.width - label_width)
            label_top = max(0, top - label_height)
            draw.rectangle((label_left, label_top, label_left + label_width, label_top + label_height), fill="#e79b25")
            draw.text((label_left + 8, label_top + 8 - bounds[1]), label, font=font, fill="#243c34")
    return img
