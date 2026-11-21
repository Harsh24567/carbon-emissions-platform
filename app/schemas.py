from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class FactorIn(BaseModel):
    activity_key: str = Field(min_length=2, max_length=64, pattern=r"^[a-z0-9_]+$")
    name: str = Field(min_length=2, max_length=120)
    scope: int = Field(ge=1, le=2)
    unit: str = Field(min_length=1, max_length=24)
    co2e_per_unit: float = Field(ge=0, description="kgCO2e per one unit of activity")
    source: str = Field(min_length=2, max_length=255)
    valid_from: date
    valid_to: date | None = None


class FactorOut(FactorIn):
    model_config = ConfigDict(from_attributes=True)
    id: int
    version: int


class RecordIn(BaseModel):
    activity_key: str
    quantity: float = Field(gt=0)
    activity_date: date
    unit: str | None = None
    location: str | None = Field(default=None, max_length=120)
    notes: str | None = Field(default=None, max_length=500)

    @field_validator("activity_date")
    @classmethod
    def not_future(cls, v: date) -> date:
        if v > date.today():
            raise ValueError("activity_date cannot be in the future")
        return v


class RecordOut(BaseModel):
    id: int
    activity_date: date
    scope: int
    activity_key: str
    activity_name: str
    quantity: float
    unit: str
    factor_id: int
    factor_version: int
    factor_value: float
    factor_source: str
    calculated_kgco2e: float
    override_kgco2e: float | None
    effective_kgco2e: float
    is_overridden: bool
    location: str | None
    notes: str | None


class OverrideIn(BaseModel):
    override_kgco2e: float = Field(ge=0)
    reason: str = Field(min_length=5, max_length=500)
    changed_by: str = Field(min_length=2, max_length=120)


class RevertIn(BaseModel):
    reason: str = Field(min_length=5, max_length=500)
    changed_by: str = Field(min_length=2, max_length=120)


class AuditOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    record_id: int
    action: str
    field_name: str
    old_value: float | None
    new_value: float | None
    reason: str
    changed_by: str
    changed_at: datetime


class MetricIn(BaseModel):
    date: date
    metric_name: str = Field(min_length=2, max_length=120)
    value: float = Field(ge=0)
    unit: str = Field(default="", max_length=32)
    aggregation: str = Field(default="sum", pattern=r"^(sum|avg)$")


class MetricOut(MetricIn):
    model_config = ConfigDict(from_attributes=True)
    id: int
