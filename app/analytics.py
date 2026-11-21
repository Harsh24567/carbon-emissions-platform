from datetime import date

from sqlalchemy import extract, func, select
from sqlalchemy.orm import Session

from .models import BusinessMetric, EmissionFactor, EmissionRecord
from .services import DomainError

MONTH_ABBR = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
EFFECTIVE = func.coalesce(EmissionRecord.override_kgco2e, EmissionRecord.calculated_kgco2e)


def _scope_totals(rows) -> dict:
    out = {"scope1": 0.0, "scope2": 0.0}
    for scope, total in rows:
        out[f"scope{scope}"] = float(total or 0)
    out["total"] = out["scope1"] + out["scope2"]
    return out


def yoy(db: Session, year: int, like_for_like: bool = True) -> dict:
    latest = db.scalar(select(func.max(extract("month", EmissionRecord.activity_date))).where(extract("year", EmissionRecord.activity_date) == year))
    through = int(latest) if latest else 12

    def totals(y: int, max_month: int | None = None) -> dict:
        stmt = select(EmissionRecord.scope, func.sum(EFFECTIVE)).where(extract("year", EmissionRecord.activity_date) == y)
        if max_month:
            stmt = stmt.where(extract("month", EmissionRecord.activity_date) <= max_month)
        return _scope_totals(db.execute(stmt.group_by(EmissionRecord.scope)).all())

    limit = through if like_for_like and through < 12 else None
    current, previous = totals(year), totals(year - 1, limit)
    change = {k: (None if previous[k] == 0 else round((current[k] - previous[k]) / previous[k] * 100, 2)) for k in current}
    return {"unit": "kgCO2e", "current_year": year, "previous_year": year - 1, "current": current, "previous": previous,
            "change_pct": change, "comparison_months": f"Jan-{MONTH_ABBR[through]}" if limit else "full year",
            "like_for_like": limit is not None}


def hotspots(db: Session, start: date, end: date) -> dict:
    rows = db.execute(
        select(EmissionFactor.activity_key, EmissionFactor.name, EmissionFactor.scope, func.sum(EFFECTIVE).label("total"))
        .join(EmissionFactor, EmissionRecord.factor_id == EmissionFactor.id)
        .where(EmissionRecord.activity_date >= start, EmissionRecord.activity_date <= end)
        .group_by(EmissionFactor.activity_key, EmissionFactor.name, EmissionFactor.scope)
        .order_by(func.sum(EFFECTIVE).desc())
    ).all()
    grand = sum(float(r.total) for r in rows)
    sources = [{"activity_key": r.activity_key, "source": r.name, "scope": r.scope, "kgco2e": float(r.total),
                "share_pct": round(float(r.total) / grand * 100, 2) if grand else 0.0} for r in rows]
    return {"unit": "kgCO2e", "start": start, "end": end, "total_kgco2e": grand, "sources": sources,
            "largest": sources[0]["source"] if sources else None}


def monthly(db: Session, year: int) -> dict:
    rows = db.execute(select(extract("month", EmissionRecord.activity_date), EmissionRecord.scope, func.sum(EFFECTIVE))
                      .where(extract("year", EmissionRecord.activity_date) == year)
                      .group_by(extract("month", EmissionRecord.activity_date), EmissionRecord.scope)).all()
    months = {m: {"scope1": 0.0, "scope2": 0.0} for m in range(1, 13)}
    for m, scope, total in rows:
        months[int(m)][f"scope{scope}"] = float(total)
    series = [{"month": m, "scope1": v["scope1"], "scope2": v["scope2"], "total": v["scope1"] + v["scope2"]} for m, v in months.items()]
    return {"unit": "kgCO2e", "year": year, "months": series}


def intensity(db: Session, start: date, end: date, metric_name: str, aggregation: str | None) -> dict:
    total = db.scalar(select(func.sum(EFFECTIVE)).where(EmissionRecord.activity_date >= start, EmissionRecord.activity_date <= end)) or 0.0
    metrics = db.scalars(select(BusinessMetric).where(BusinessMetric.metric_name == metric_name, BusinessMetric.date >= start,
                                                      BusinessMetric.date <= end).order_by(BusinessMetric.date)).all()
    if not metrics:
        raise DomainError(404, f"No '{metric_name}' values between {start} and {end}")
    agg = aggregation or metrics[-1].aggregation
    values = [m.value for m in metrics]
    metric_value = sum(values) if agg == "sum" else sum(values) / len(values)
    if metric_value <= 0:
        raise DomainError(422, f"'{metric_name}' totals zero for the period, intensity is undefined")
    unit = metrics[-1].unit or "unit"
    return {"start": start, "end": end, "metric_name": metric_name, "aggregation": agg, "metric_value": metric_value,
            "metric_unit": unit, "total_kgco2e": float(total), "intensity": float(total) / metric_value,
            "intensity_unit": f"kgCO2e per {unit}", "periods_counted": len(metrics)}
