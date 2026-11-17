import json
import pytest
from pydantic import ValidationError
from app.models import PhotoAnalysis
from app.ai import analyze_photo
from app.config import Settings


def test_schema_rejects_invalid_evidence_coordinates():
    with pytest.raises(ValidationError):
        PhotoAnalysis.model_validate({"vehicle_visible": True, "view_description": "test", "quality": "good", "findings": [{"part": "door", "kind": "scratch", "description": "mark", "certainty": "visible", "box": [0.9, 0.9, 0.5, 0.5]}]})


def test_provider_uses_image_and_validates_response(tmp_path, monkeypatch):
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"sample-image")
    settings = Settings(tmp_path)
    settings.muse_key, settings.muse_model = "secret", "vision-model"
    captured = {}

    class Response:
        status_code = 200
        def json(self):
            return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps({"vehicle_visible": False, "view_description": "No vehicle visible.", "quality": "good", "findings": []})}}]}

    class Client:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def post(self, url, **kwargs): captured.update(kwargs); return Response()

    monkeypatch.setattr("app.ai.httpx.Client", Client)
    result = analyze_photo(settings, photo, "Front")
    assert not result["vehicle_visible"]
    assert captured["json"]["model"] == "vision-model"
    assert captured["json"]["messages"][1]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
