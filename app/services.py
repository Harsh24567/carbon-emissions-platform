from datetime import date, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .models import AuditLog, BusinessMetric, EmissionFactor, EmissionRecord
from .schemas import FactorIn, MetricIn, RecordIn


class DomainError(Exception):
    def __init__(self, status_code: int, detail: str):
        self.status_code, self.detail = status_code, detail
        super().__init__(detail)


def resolve_factor(db: Session, activity_key: str, on_date: date) -> EmissionFactor | None:
    stmt = (
        select(EmissionFactor)
        .where(EmissionFactor.activity_key == activity_key, EmissionFactor.valid_from <= on_date,
               or_(EmissionFactor.valid_to.is_(None), EmissionFactor.valid_to >= on_date))
        .order_by(EmissionFactor.valid_from.desc())
        .limit(1)
    )
    return db.scalars(stmt).first()


def create_record(db: Session, scope: int, payload: RecordIn) -> EmissionRecord:
    known = db.scalar(select(func.count()).select_from(EmissionFactor).where(EmissionFactor.activity_key == payload.activity_key))
    if not known:
        raise DomainError(422, f"Unknown activity '{payload.activity_key}'")
    factor = resolve_factor(db, payload.activity_key, payload.activity_date)
    if factor is None:
        raise DomainError(422, f"No emission factor for '{payload.activity_key}' is valid on {payload.activity_date}")
    if factor.scope != scope:
        raise DomainError(422, f"'{payload.activity_key}' is a Scope {factor.scope} activity, not Scope {scope}")
    if payload.unit and payload.unit != factor.unit:
        raise DomainError(422, f"Unit mismatch: '{payload.activity_key}' is measured in {factor.unit}, got {payload.unit}")
    record = EmissionRecord(
        activity_date=payload.activity_date, scope=scope, factor_id=factor.id, quantity=payload.quantity,
        unit=factor.unit, calculated_kgco2e=round(payload.quantity * factor.co2e_per_unit, 4),
        location=payload.location, notes=payload.notes,
    )
    db.add(record)
    db.commit()
    return record


def apply_override(db: Session, record_id: int, value: float, reason: str, changed_by: str) -> EmissionRecord:
    record = db.get(EmissionRecord, record_id)
    if record is None:
        raise DomainError(404, f"Record {record_id} not found")
    previous = record.effective_kgco2e
    record.override_kgco2e = value
    record.is_overridden = True
    db.add(AuditLog(record_id=record.id, action="override", old_value=previous, new_value=value, reason=reason, changed_by=changed_by))
    db.commit()
    return record


def revert_override(db: Session, record_id: int, reason: str, changed_by: str) -> EmissionRecord:
    record = db.get(EmissionRecord, record_id)
    if record is None:
        raise DomainError(404, f"Record {record_id} not found")
    if not record.is_overridden:
        raise DomainError(409, "Record has no override to revert")
    db.add(AuditLog(record_id=record.id, action="override_reverted", old_value=record.override_kgco2e,
                    new_value=record.calculated_kgco2e, reason=reason, changed_by=changed_by))
    record.override_kgco2e = None
    record.is_overridden = False
    db.commit()
    return record


def create_factor(db: Session, payload: FactorIn) -> EmissionFactor:
    if payload.valid_to is not None and payload.valid_to < payload.valid_from:
        raise DomainError(422, "valid_to must not be earlier than valid_from")
    existing = list(db.scalars(select(EmissionFactor).where(EmissionFactor.activity_key == payload.activity_key)
                               .order_by(EmissionFactor.valid_from)))
    if existing and (existing[0].scope != payload.scope or existing[0].unit != payload.unit):
        raise DomainError(409, f"'{payload.activity_key}' already exists as Scope {existing[0].scope} in {existing[0].unit}")
    if any(f.valid_from == payload.valid_from for f in existing):
        raise DomainError(409, "A version starting on that date already exists")
    open_ended = [f for f in existing if f.valid_to is None and f.valid_from < payload.valid_from]
    for f in open_ended:
        f.valid_to = payload.valid_from - timedelta(days=1)
    far = date.max
    new_end = payload.valid_to or far
    for f in existing:
        if f in open_ended:
            continue
        if payload.valid_from <= (f.valid_to or far) and f.valid_from <= new_end:
            db.rollback()
            raise DomainError(409, f"Validity window overlaps version {f.version} ({f.valid_from} to {f.valid_to or 'open'})")
    factor = EmissionFactor(**payload.model_dump(), version=(max((f.version for f in existing), default=0) + 1))
    db.add(factor)
    db.commit()
    return factor


def upsert_metric(db: Session, payload: MetricIn) -> tuple[BusinessMetric, bool]:
    row = db.scalar(select(BusinessMetric).where(BusinessMetric.date == payload.date, BusinessMetric.metric_name == payload.metric_name))
    created = row is None
    if created:
        row = BusinessMetric(**payload.model_dump())
        db.add(row)
    else:
        row.value, row.unit, row.aggregation = payload.value, payload.unit, payload.aggregation
    db.commit()
    return row, created
