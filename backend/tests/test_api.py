from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import schemas, services
from app.models import Audit, Occurrence, Payment, Recurring

pytestmark = pytest.mark.integration


def create(client, **overrides):
    body = dict(
        title="Квартплата",
        anchor_date=services.today().isoformat(),
        frequency="months",
        interval=1,
        amount="6500.00",
        variable=True,
    )
    body.update(overrides)
    response = client.post("/recurring", json=body)
    assert response.status_code == 201, response.text
    ident = response.json()["id"]
    rows = client.get("/occurrences").json()
    occ = next(o for o in rows if o["recurring_id"] == ident)
    return ident, occ["id"]


def pay(client, occ, amount, key=None, **kwargs):
    return client.post(
        f"/occurrences/{occ}/payments",
        json={"amount": amount, "idempotency_key": key or str(uuid4()), **kwargs},
    )


def test_full_and_partial_payment(client):
    _, occ = create(client)
    assert pay(client, occ, "2000").status_code == 200
    row = client.get(f"/occurrences/{occ}").json()
    assert row["status"] == "partial" and Decimal(row["remaining_amount"]) == 4500
    assert pay(client, occ, "4500").status_code == 200
    row = client.get(f"/occurrences/{occ}").json()
    assert row["status"] == "paid" and Decimal(row["remaining_amount"]) == 0
    assert pay(client, occ, "1").status_code == 409


def test_variable_before_and_during_payment(client):
    _, occ = create(client)
    assert client.patch(f"/occurrences/{occ}", json={"expected_amount": "7000"}).status_code == 200
    assert pay(client, occ, "2000").status_code == 200
    assert pay(client, occ, "5200", revised_expected_amount="7200").status_code == 200
    row = client.get(f"/occurrences/{occ}").json()
    assert Decimal(row["expected_amount"]) == 7200 and row["status"] == "paid"
    assert len(client.get("/audit", params={"entity_id": occ}).json()) == 4


def test_failed_revision_rolls_back_everything(client):
    _, occ = create(client)
    assert pay(client, occ, "7201", revised_expected_amount="7200").status_code == 409
    row = client.get(f"/occurrences/{occ}").json()
    assert Decimal(row["expected_amount"]) == 6500 and row["status"] == "pending"
    assert client.get(f"/occurrences/{occ}/payments").json() == []
    assert client.get("/audit", params={"entity_id": occ}).json() == []


def test_below_paid_and_partial_cancel_forbidden(client):
    _, occ = create(client)
    pay(client, occ, "2000")
    assert client.patch(f"/occurrences/{occ}", json={"expected_amount": "1999"}).status_code == 409
    assert client.patch(f"/occurrences/{occ}", json={"status": "cancelled"}).status_code == 409
    assert client.patch(f"/occurrences/{occ}", json={"expected_amount": "2000"}).json()["status"] == "paid"


def test_fixed_amount_cannot_be_revised(client):
    _, occ = create(client, variable=False)
    assert client.patch(f"/occurrences/{occ}", json={"expected_amount": "7000"}).status_code == 409
    assert pay(client, occ, "7000", revised_expected_amount="7000").status_code == 409


@pytest.mark.parametrize("amount", ["0", "-1", "1.001", "NaN", "Infinity", "1000000000000"])
def test_invalid_money(client, amount):
    _, occ = create(client)
    assert pay(client, occ, amount).status_code == 422


def test_idempotency_and_conflicts(client):
    _, occ = create(client)
    one = pay(client, occ, "6500", key="same-key")
    two = pay(client, occ, "6500.00", key="same-key")
    assert one.status_code == two.status_code == 200
    assert one.json()["id"] == two.json()["id"]
    assert len(client.get(f"/occurrences/{occ}/payments").json()) == 1
    assert pay(client, occ, "1", key="same-key").status_code == 409
    _, other = create(client)
    assert pay(client, other, "6500", key="same-key").status_code == 409


def test_missing_occurrence_has_no_advance_balance(client):
    assert pay(client, str(uuid4()), "100").status_code == 404


def test_generation_is_idempotent_and_yearly_visible(client, engine):
    ident, _ = create(
        client, frequency="years", anchor_date=(services.today() + timedelta(days=200)).isoformat()
    )
    rows = [o for o in client.get("/occurrences").json() if o["recurring_id"] == ident]
    assert len(rows) == 1
    assert client.post("/occurrences/generate").json() == {"created": 0}
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(Occurrence)) == 1


def test_tariff_updates_only_untouched_future_occurrences(client):
    ident, occ = create(client)
    pay(client, occ, "1000")
    future = [o for o in client.get("/occurrences").json() if o["id"] != occ]
    client.patch(f"/occurrences/{future[0]['id']}", json={"expected_amount": "6600"})
    effective = (services.today() + timedelta(days=1)).isoformat()
    response = client.post(
        f"/recurring/{ident}/tariffs", json={"amount": "7500", "effective_from": effective}
    )
    assert response.status_code == 201, response.text
    rows = client.get("/occurrences").json()
    assert Decimal(next(o for o in rows if o["id"] == occ)["expected_amount"]) == 6500
    assert Decimal(next(o for o in rows if o["id"] == future[0]["id"])["expected_amount"]) == 6600
    assert all(Decimal(o["expected_amount"]) == 7500 for o in rows if o["id"] not in (occ, future[0]["id"]))
    assert (
        client.post(
            f"/recurring/{ident}/tariffs", json={"amount": "7600", "effective_from": effective}
        ).status_code
        == 409
    )


def test_stop_preserves_partial_and_history(client):
    ident, occ = create(client)
    pay(client, occ, "1000")
    assert client.post(f"/recurring/{ident}/stop", json={}).status_code == 200
    rows = client.get("/occurrences").json()
    assert next(o for o in rows if o["id"] == occ)["status"] == "partial"
    assert all(o["status"] == "cancelled" for o in rows if o["id"] != occ)
    assert pay(client, occ, "5500").status_code == 200
    assert client.post("/occurrences/generate").json()["created"] == 0


def test_foreign_owner_and_auth(client, engine):
    ident, occ = create(client)
    with Session(engine) as db, db.begin():
        db.get(Recurring, UUID(ident)).owner_id = 2
    assert client.get(f"/occurrences/{occ}").status_code == 404
    assert pay(client, occ, "1").status_code == 404
    assert client.get(f"/recurring/{ident}/tariffs").status_code == 404
    assert client.get("/recurring").json() == []
    assert client.get("/settings", headers={"Authorization": "Bearer wrong"}).status_code == 401


@pytest.mark.parametrize("same_key", [False, True])
def test_concurrent_payments_on_real_postgres(client, engine, same_key):
    _, occ = create(client, amount="1000")
    barrier = Barrier(2)

    def attempt(key):
        barrier.wait(timeout=10)
        try:
            with Session(engine, expire_on_commit=False) as db, db.begin():
                p = services.register_payment(
                    db, 1, UUID(occ), schemas.PaymentCreate(amount="700", idempotency_key=key)
                )
                return str(p.id)
        except HTTPException as exc:
            return exc.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, ["key-a", "key-a" if same_key else "key-b"]))
    if same_key:
        assert results[0] == results[1] and isinstance(results[0], str)
    else:
        assert sum(r == 409 for r in results) == 1
    with Session(engine) as db:
        assert db.scalar(select(func.sum(Payment.amount))) == 700
        assert db.scalar(select(func.count()).select_from(Payment)) == 1


def test_transaction_rollback_after_insert(client, engine):
    _, occ = create(client)
    with pytest.raises(RuntimeError):
        with Session(engine) as db, db.begin():
            services.register_payment(
                db,
                1,
                UUID(occ),
                schemas.PaymentCreate(
                    amount="7000", revised_expected_amount="7000", idempotency_key="rollback"
                ),
            )
            raise RuntimeError("Simulated failure before commit")
    with Session(engine) as db:
        row = db.get(Occurrence, UUID(occ))
        assert row.expected_amount == 6500 and row.status == "pending"
        assert db.scalar(select(func.count()).select_from(Payment)) == 0
        assert db.scalar(select(func.count()).select_from(Audit).where(Audit.entity_id == row.id)) == 0
