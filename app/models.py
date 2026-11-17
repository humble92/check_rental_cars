from typing import List, Literal, Optional
from pydantic import BaseModel, Field, model_validator


class Finding(BaseModel):
    part: str = Field(min_length=1, max_length=160)
    kind: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=1200)
    certainty: Literal["visible", "uncertain"]
    box: Optional[List[float]] = None

    @model_validator(mode="after")
    def validate_box(self):
        if self.box is not None:
            if len(self.box) != 4 or not all(0 <= v <= 1 for v in self.box):
                raise ValueError("Invalid normalized bounding box")
            x, y, w, h = self.box
            if w <= 0 or h <= 0 or x + w > 1.001 or y + h > 1.001:
                raise ValueError("Bounding box exceeds image")
        return self


class PhotoAnalysis(BaseModel):
    vehicle_visible: bool
    view_description: str = Field(max_length=1500)
    quality: Literal["good", "limited", "unusable"]
    limitations: List[str] = Field(default_factory=list, max_length=12)
    findings: List[Finding] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_vehicle_evidence(self):
        if not self.vehicle_visible and self.findings:
            raise ValueError("Vehicle findings require visible vehicle evidence")
        return self


class ReportInput(BaseModel):
    vehicle: str = Field(default="", max_length=120)
    plate: str = Field(default="", max_length=40)
    company: str = Field(default="", max_length=120)
    location: str = Field(default="", max_length=200)
    notes: str = Field(default="", max_length=3000)


class EmailInput(BaseModel):
    email: str = Field(max_length=254)
    share_token: str = Field(min_length=32, max_length=100)
