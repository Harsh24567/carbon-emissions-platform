import os
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import extract, func, select
from sqlalchemy.orm import Session

from . import analytics, schemas, services
from .db import Base, SessionLocal, engine, get_db
from .models import AuditLog, BusinessMetric, EmissionFactor, EmissionRecord
from .seed import seed_demo_data

STATIC = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    Base.metadata.create_all(engine)
    if os.getenv("SEED_DEMO_DATA", "1") != "0":
        with SessionLocal() as db:
            seed_demo_data(db)
    yield


app = FastAPI(title="Carbon Emissions Reporting Platform", version="1.0.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.exception_handler(services.DomainError)
async def domain_error_handler(_request: Request, exc: services.DomainError):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


def record_out(r: EmissionRecord) -> schemas.RecordOut:
    f = r.factor
    return schemas.RecordOut(
        id=r.id, activity_date=r.activity_date, scope=r.scope, activity_key=f.activity_key, activity_name=f.name,
        quantity=r.quantity, unit=r.unit, factor_id=f.id, factor_version=f.version, factor_value=f.co2e_per_unit,
        factor_source=f.source, calculated_kgco2e=r.calculated_kgco2e, override_kgco2e=r.override_kgco2e,
        effective_kgco2e=r.effective_kgco2e, is_overridden=r.is_overridden, location=r.location, notes=r.notes,
    )


def _period(start: date, end: date):
    if start > end:
        raise services.DomainError(422, "start must not be after end")
    return start, end


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/meta")
def meta(db: Session = Depends(get_db)):
    years = [int(y) for y in db.scalars(select(extract("year", EmissionRecord.activity_date)).distinct().order_by(extract("year", EmissionRecord.activity_date)))]
    metrics = db.execute(select(BusinessMetric.metric_name, BusinessMetric.unit, BusinessMetric.aggregation).distinct()
                         .order_by(BusinessMetric.aggregation.desc(), BusinessMetric.metric_name)).all()
    activities = db.execute(select(EmissionFactor.activity_key, EmissionFactor.name, EmissionFactor.scope, EmissionFactor.unit).distinct()
                            .order_by(EmissionFactor.scope, EmissionFactor.name)).all()
    return {
        "years": years, "default_year": years[-1] if years else date.today().year,
        "metrics": [{"metric_name": m, "unit": u, "aggregation": a} for m, u, a in metrics],
        "activities": [{"activity_key": k, "name": n, "scope": s, "unit": u} for k, n, s, u in activities],
    }


@app.get("/api/factors", response_model=list[schemas.FactorOut])
def list_factors(scope: int | None = Query(None, ge=1, le=2), active_on: date | None = None, activity_key: str | None = None,
                 db: Session = Depends(get_db)):
    stmt = select(EmissionFactor).order_by(EmissionFactor.activity_key, EmissionFactor.valid_from)
    if scope:
        stmt = stmt.where(EmissionFactor.scope == scope)
    if activity_key:
        stmt = stmt.where(EmissionFactor.activity_key == activity_key)
    if active_on:
        stmt = stmt.where(EmissionFactor.valid_from <= active_on).where((EmissionFactor.valid_to.is_(None)) | (EmissionFactor.valid_to >= active_on))
    return list(db.scalars(stmt))


@app.post("/api/factors", response_model=schemas.FactorOut, status_code=201)
def add_factor(payload: schemas.FactorIn, db: Session = Depends(get_db)):
    return services.create_factor(db, payload)


@app.get("/api/factors/resolve", response_model=schemas.FactorOut)
def resolve(activity_key: str, on: date, db: Session = Depends(get_db)):
    factor = services.resolve_factor(db, activity_key, on)
    if factor is None:
        raise services.DomainError(404, f"No factor for '{activity_key}' valid on {on}")
    return factor


@app.post("/api/records/scope1", response_model=schemas.RecordOut, status_code=201)
def create_scope1(payload: schemas.RecordIn, db: Session = Depends(get_db)):
    return record_out(services.create_record(db, 1, payload))


@app.post("/api/records/scope2", response_model=schemas.RecordOut, status_code=201)
def create_scope2(payload: schemas.RecordIn, db: Session = Depends(get_db)):
    return record_out(services.create_record(db, 2, payload))


@app.get("/api/records", response_model=list[schemas.RecordOut])
def list_records(scope: int | None = Query(None, ge=1, le=2), start: date | None = None, end: date | None = None,
                 limit: int = Query(25, ge=1, le=500), offset: int = Query(0, ge=0), db: Session = Depends(get_db)):
    stmt = select(EmissionRecord).order_by(EmissionRecord.activity_date.desc(), EmissionRecord.id.desc()).limit(limit).offset(offset)
    if scope:
        stmt = stmt.where(EmissionRecord.scope == scope)
    if start:
        stmt = stmt.where(EmissionRecord.activity_date >= start)
    if end:
        stmt = stmt.where(EmissionRecord.activity_date <= end)
    return [record_out(r) for r in db.scalars(stmt).unique()]


@app.get("/api/records/{record_id}", response_model=schemas.RecordOut)
def get_record(record_id: int, db: Session = Depends(get_db)):
    r = db.get(EmissionRecord, record_id)
    if r is None:
        raise services.DomainError(404, f"Record {record_id} not found")
    return record_out(r)


@app.patch("/api/records/{record_id}/override", response_model=schemas.RecordOut)
def override(record_id: int, payload: schemas.OverrideIn, db: Session = Depends(get_db)):
    return record_out(services.apply_override(db, record_id, payload.override_kgco2e, payload.reason, payload.changed_by))


@app.post("/api/records/{record_id}/override/revert", response_model=schemas.RecordOut)
def revert(record_id: int, payload: schemas.RevertIn, db: Session = Depends(get_db)):
    return record_out(services.revert_override(db, record_id, payload.reason, payload.changed_by))


@app.get("/api/records/{record_id}/audit", response_model=list[schemas.AuditOut])
def record_audit(record_id: int, db: Session = Depends(get_db)):
    if db.get(EmissionRecord, record_id) is None:
        raise services.DomainError(404, f"Record {record_id} not found")
    return list(db.scalars(select(AuditLog).where(AuditLog.record_id == record_id).order_by(AuditLog.id)))


@app.get("/api/audit-log", response_model=list[schemas.AuditOut])
def audit_log(limit: int = Query(50, ge=1, le=500), db: Session = Depends(get_db)):
    return list(db.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(limit)))


@app.post("/api/metrics", response_model=schemas.MetricOut)
def add_metric(payload: schemas.MetricIn, db: Session = Depends(get_db)):
    row, created = services.upsert_metric(db, payload)
    return JSONResponse(status_code=201 if created else 200, content=schemas.MetricOut.model_validate(row).model_dump(mode="json"))


@app.get("/api/metrics", response_model=list[schemas.MetricOut])
def list_metrics(metric_name: str | None = None, start: date | None = None, end: date | None = None,
                 db: Session = Depends(get_db)):
    stmt = select(BusinessMetric).order_by(BusinessMetric.date.desc(), BusinessMetric.metric_name)
    if metric_name:
        stmt = stmt.where(BusinessMetric.metric_name == metric_name)
    if start:
        stmt = stmt.where(BusinessMetric.date >= start)
    if end:
        stmt = stmt.where(BusinessMetric.date <= end)
    return list(db.scalars(stmt.limit(500)))


@app.get("/api/analytics/yoy")
def analytics_yoy(year: int = Query(default_factory=lambda: date.today().year, ge=1990, le=2100), like_for_like: bool = True,
                  db: Session = Depends(get_db)):
    return analytics.yoy(db, year, like_for_like)


@app.get("/api/analytics/intensity")
def analytics_intensity(start: date, end: date, metric_name: str = "Tons of Steel Produced",
                        aggregation: str | None = Query(None, pattern=r"^(sum|avg)$"), db: Session = Depends(get_db)):
    s, e = _period(start, end)
    return analytics.intensity(db, s, e, metric_name, aggregation)


@app.get("/api/analytics/hotspots")
def analytics_hotspots(start: date, end: date, db: Session = Depends(get_db)):
    s, e = _period(start, end)
    return analytics.hotspots(db, s, e)


@app.get("/api/analytics/monthly")
def analytics_monthly(year: int = Query(default_factory=lambda: date.today().year, ge=1990, le=2100), db: Session = Depends(get_db)):
    return analytics.monthly(db, year)
