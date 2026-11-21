import os

os.environ["DATABASE_URL"] = "sqlite://"
os.environ["SEED_DEMO_DATA"] = "0"

import pytest
from fastapi.testclient import TestClient

from app.db import Base, engine
from app.main import app


@pytest.fixture()
def client():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with TestClient(app) as c:
        yield c


def add_factor(client, **overrides):
    body = {"activity_key": "diesel", "name": "Diesel", "scope": 1, "unit": "litre", "co2e_per_unit": 2.0,
            "source": "test", "valid_from": "2022-01-01", "valid_to": "2023-12-31"}
    body.update(overrides)
    return client.post("/api/factors", json=body)


@pytest.fixture()
def factors(client):
    assert add_factor(client).status_code == 201
    assert add_factor(client, co2e_per_unit=3.0, valid_from="2024-01-01", valid_to=None).status_code == 201
    assert add_factor(client, activity_key="grid_electricity", name="Grid electricity", scope=2, unit="kWh",
                      co2e_per_unit=0.8, valid_from="2022-01-01", valid_to=None).status_code == 201
    return client
