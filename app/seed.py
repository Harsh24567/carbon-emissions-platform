import math
import random
from calendar import monthrange
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import EmissionFactor
from .schemas import FactorIn, MetricIn, RecordIn
from .services import apply_override, create_factor, create_record, upsert_metric

SAMPLE_NOTE = "Sample value for demonstration; replace with the official factor for the reporting year"
GHG_SHEET = "GHG_Sheet_.xlsx (Scope 2 sheet); sample value"

FACTORS = [
    ("diesel", "Diesel (stationary and mobile)", 1, "litre", SAMPLE_NOTE, [(2.65, date(2022, 1, 1), date(2023, 12, 31)), (2.68, date(2024, 1, 1), None)]),
    ("natural_gas", "Natural gas", 1, "m3", SAMPLE_NOTE, [(1.93, date(2022, 1, 1), date(2023, 12, 31)), (2.02, date(2024, 1, 1), None)]),
    ("coal_bituminous", "Bituminous coal", 1, "kg", SAMPLE_NOTE, [(2.42, date(2022, 1, 1), date(2024, 12, 31)), (2.45, date(2025, 1, 1), None)]),
    ("lpg", "LPG", 1, "kg", SAMPLE_NOTE, [(2.95, date(2022, 1, 1), None)]),
    ("grid_electricity", "Grid electricity", 2, "kWh", SAMPLE_NOTE,
     [(0.82, date(2022, 1, 1), date(2023, 12, 31)), (0.79, date(2024, 1, 1), date(2025, 12, 31)), (0.76, date(2026, 1, 1), None)]),
    ("captive_solar_grid_blend", "Captive solar and grid blend", 2, "kWh", GHG_SHEET, [(0.60, date(2022, 1, 1), None)]),
    ("imported_steam", "Imported steam", 2, "GJ", SAMPLE_NOTE, [(70.0, date(2022, 1, 1), None)]),
]

MONTHLY_BASE = {
    "diesel": 40000, "natural_gas": 120000, "coal_bituminous": 800000, "lpg": 6000,
    "grid_electricity": 2500000, "captive_solar_grid_blend": 600000, "imported_steam": 4000,
}


def seed_factors(db: Session) -> None:
    for key, name, scope, unit, source, versions in FACTORS:
        for value, start, end in versions:
            create_factor(db, FactorIn(activity_key=key, name=name, scope=scope, unit=unit, co2e_per_unit=value,
                                       source=source, valid_from=start, valid_to=end))


def seed_activity(db: Session, rng: random.Random) -> None:
    today = date.today()
    for year in (2023, 2024, 2025, 2026):
        for month in range(1, 13):
            when = date(year, month, 15)
            if when > today:
                continue
            drift = 1 - 0.025 * (year - 2023)
            season = 1 + 0.07 * math.sin((month - 3) / 12 * 2 * math.pi)
            for key, base in MONTHLY_BASE.items():
                scope = 1 if key in ("diesel", "natural_gas", "coal_bituminous", "lpg") else 2
                qty = round(base * drift * season * rng.uniform(0.94, 1.06), 2)
                create_record(db, scope, RecordIn(activity_key=key, quantity=qty, activity_date=when,
                                                  location="Central Steel Plant"))
            last = date(year, month, monthrange(year, month)[1])
            if last <= today:
                steel = round(4400 * (1 + 0.02 * (year - 2023)) * rng.uniform(0.93, 1.07))
                upsert_metric(db, MetricIn(date=last, metric_name="Tons of Steel Produced", value=steel, unit="tonne", aggregation="sum"))
                staff = round(1150 + 18 * (year - 2023) + month + rng.uniform(-6, 6))
                upsert_metric(db, MetricIn(date=last, metric_name="Employees", value=staff, unit="employee", aggregation="avg"))


def seed_override_example(db: Session) -> None:
    from .models import EmissionRecord

    record = db.scalars(select(EmissionRecord).where(EmissionRecord.activity_date == date(2025, 6, 15), EmissionRecord.scope == 2)
                        .order_by(EmissionRecord.id)).first()
    if record:
        apply_override(db, record.id, round(record.calculated_kgco2e * 0.95, 4),
                       "Replaced estimate with metered utility invoice value", "demo.seed")


def seed_demo_data(db: Session) -> bool:
    if db.scalar(select(func.count()).select_from(EmissionFactor)):
        return False
    seed_factors(db)
    seed_activity(db, random.Random(7))
    seed_override_example(db)
    return True
