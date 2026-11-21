from datetime import date, datetime

from sqlalchemy import CheckConstraint, Date, DateTime, Float, ForeignKey, Index, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


class EmissionFactor(Base):
    __tablename__ = "EmissionFactors"
    __table_args__ = (
        UniqueConstraint("activity_key", "valid_from", name="uq_factor_key_start"),
        CheckConstraint("scope IN (1, 2)", name="ck_factor_scope"),
        CheckConstraint("valid_to IS NULL OR valid_to >= valid_from", name="ck_factor_dates"),
        CheckConstraint("co2e_per_unit >= 0", name="ck_factor_value"),
        Index("ix_factor_lookup", "activity_key", "valid_from", "valid_to"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    activity_key: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(120))
    scope: Mapped[int] = mapped_column(Integer)
    unit: Mapped[str] = mapped_column(String(24))
    co2e_per_unit: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(255))
    valid_from: Mapped[date] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class EmissionRecord(Base):
    __tablename__ = "EmissionRecords"
    __table_args__ = (
        CheckConstraint("scope IN (1, 2)", name="ck_record_scope"),
        CheckConstraint("quantity > 0", name="ck_record_quantity"),
        Index("ix_record_date_scope", "activity_date", "scope"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    activity_date: Mapped[date] = mapped_column(Date)
    scope: Mapped[int] = mapped_column(Integer)
    factor_id: Mapped[int] = mapped_column(ForeignKey("EmissionFactors.id"))
    quantity: Mapped[float] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(24))
    calculated_kgco2e: Mapped[float] = mapped_column(Float)
    override_kgco2e: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_overridden: Mapped[bool] = mapped_column(default=False)
    location: Mapped[str | None] = mapped_column(String(120), nullable=True)
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    factor: Mapped[EmissionFactor] = relationship(lazy="joined")

    @property
    def effective_kgco2e(self) -> float:
        return self.override_kgco2e if self.override_kgco2e is not None else self.calculated_kgco2e


class AuditLog(Base):
    __tablename__ = "AuditLog"
    __table_args__ = (Index("ix_audit_record", "record_id", "changed_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    record_id: Mapped[int] = mapped_column(ForeignKey("EmissionRecords.id"))
    action: Mapped[str] = mapped_column(String(32))
    field_name: Mapped[str] = mapped_column(String(64), default="emission_kgco2e")
    old_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    new_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    reason: Mapped[str] = mapped_column(String(500))
    changed_by: Mapped[str] = mapped_column(String(120))
    changed_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class BusinessMetric(Base):
    __tablename__ = "BusinessMetrics"
    __table_args__ = (
        UniqueConstraint("date", "metric_name", name="uq_metric_date_name"),
        CheckConstraint("aggregation IN ('sum', 'avg')", name="ck_metric_aggregation"),
        Index("ix_metric_name_date", "metric_name", "date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    date: Mapped[date] = mapped_column(Date)
    metric_name: Mapped[str] = mapped_column(String(120))
    value: Mapped[float] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(32), default="")
    aggregation: Mapped[str] = mapped_column(String(8), default="sum")
