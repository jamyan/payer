from datetime import date
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID

from fastapi import Depends, FastAPI, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app import schemas, services
from app.auth import get_owner
from app.browser_auth import router as browser_auth_router
from app.config import get_settings
from app.db import get_db
from app.models import Audit, Notification, Occurrence, Payment, Recurring, Tariff

runtime_settings = get_settings()
STATIC = Path(__file__).parent / "static"
app = FastAPI(
    title="Платёжник",
    version="1.4.0",
    description="Учёт оплат в RUB. Это журнал фактов оплаты, деньги API не переводит.",
    docs_url=None if runtime_settings.app_env == "production" else "/docs",
    redoc_url=None,
    openapi_url=None if runtime_settings.app_env == "production" else "/openapi.json",
)
if runtime_settings.app_env == "production":
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=[urlparse(runtime_settings.app_url).hostname, "127.0.0.1", "localhost"],
    )


@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Cache-Control"] = "no-store"
    if request.url.path == "/" or request.url.path.startswith("/assets/"):
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' https://telegram.org; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; font-src 'self'; base-uri 'none'; "
            "object-src 'none'; form-action 'self'; frame-ancestors https://*.telegram.org https://web.telegram.org https://*.t.me"
        )
    return response


@app.get("/", include_in_schema=False)
def home():
    return FileResponse(STATIC / "index.html")


@app.get("/client-config", include_in_schema=False)
def client_config():
    settings = get_settings()
    return {"auth_mode": settings.auth_mode, "version": "1.4.0", "timezone": "Asia/Irkutsk"}


app.mount("/assets", StaticFiles(directory=STATIC), name="assets")
app.include_router(browser_auth_router)


@app.get("/health")
def health(db: Session = Depends(get_db, scope="function")):
    db.execute(text("SELECT 1"))
    return {"status": "ok", "version": "1.4.0"}


@app.get("/settings")
def settings(owner: int = Depends(get_owner)):
    return {
        "owner_id": owner,
        "currency": "RUB",
        "timezone": "Asia/Irkutsk",
        "notification_days": [3, 1, 0],
        "notification_time": "11:00",
    }


@app.post("/recurring", status_code=201, response_model=schemas.RecurringOut)
def create_recurring(
    data: schemas.RecurringCreate,
    db: Session = Depends(get_db, scope="function"),
    owner: int = Depends(get_owner),
):
    return services.create_recurring(db, owner, data)


@app.get("/recurring", response_model=list[schemas.RecurringOut])
def list_recurring(
    db: Session = Depends(get_db, scope="function"),
    owner: int = Depends(get_owner),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    include_deleted: bool = False,
):
    query = select(Recurring).where(Recurring.owner_id == owner)
    if not include_deleted:
        query = query.where(Recurring.deleted_at.is_(None))
    return list(db.scalars(query.order_by(Recurring.created_at, Recurring.id).limit(limit).offset(offset)))


@app.patch("/recurring/{ident}", response_model=schemas.RecurringOut)
def edit_recurring(
    ident: UUID,
    data: schemas.RecurringPatch,
    db: Session = Depends(get_db, scope="function"),
    owner: int = Depends(get_owner),
):
    return services.edit_recurring(db, owner, ident, data)


@app.delete("/recurring/{ident}")
def delete_recurring(
    ident: UUID, db: Session = Depends(get_db, scope="function"), owner: int = Depends(get_owner)
):
    services.delete_recurring(db, owner, ident)
    return {"status": "deleted", "history_preserved": True}


@app.get("/recurring/{ident}/tariffs", response_model=list[schemas.TariffOut])
def list_tariffs(
    ident: UUID, db: Session = Depends(get_db, scope="function"), owner: int = Depends(get_owner)
):
    services.recurring(db, owner, ident)
    return list(
        db.scalars(select(Tariff).where(Tariff.recurring_id == ident).order_by(Tariff.effective_from))
    )


@app.post("/recurring/{ident}/tariffs", status_code=201)
def add_tariff(
    ident: UUID,
    data: schemas.TariffCreate,
    db: Session = Depends(get_db, scope="function"),
    owner: int = Depends(get_owner),
):
    services.change_tariff(db, owner, ident, data)
    return {"status": "created"}


@app.post("/recurring/{ident}/stop")
def stop(
    ident: UUID,
    data: schemas.StopRequest,
    db: Session = Depends(get_db, scope="function"),
    owner: int = Depends(get_owner),
):
    services.stop_recurring(db, owner, ident, data.cancel_future_unpaid)
    return {"status": "stopped"}


@app.post("/occurrences/generate")
def generate(db: Session = Depends(get_db, scope="function"), owner: int = Depends(get_owner)):
    return {"created": services.generate(db, owner, services.today())}


@app.get("/occurrences")
def list_occurrences(
    start: date | None = None,
    end: date | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db, scope="function"),
    owner: int = Depends(get_owner),
):
    query = select(Occurrence).join(Recurring).where(Recurring.owner_id == owner)
    if start:
        query = query.where(Occurrence.due_date >= start)
    if end:
        query = query.where(Occurrence.due_date <= end)
    rows = db.scalars(query.order_by(Occurrence.due_date, Occurrence.id).limit(limit).offset(offset))
    return [services.view_occurrence(db, row) for row in rows]


@app.get("/occurrences/{ident}")
def get_occurrence(
    ident: UUID, db: Session = Depends(get_db, scope="function"), owner: int = Depends(get_owner)
):
    return services.view_occurrence(db, services.occurrence(db, owner, ident))


@app.patch("/occurrences/{ident}")
def patch(
    ident: UUID,
    data: schemas.OccurrencePatch,
    db: Session = Depends(get_db, scope="function"),
    owner: int = Depends(get_owner),
):
    return services.view_occurrence(db, services.patch_occurrence(db, owner, ident, data))


@app.post("/occurrences/{ident}/payments", response_model=schemas.PaymentOut)
def pay(
    ident: UUID,
    data: schemas.PaymentCreate,
    db: Session = Depends(get_db, scope="function"),
    owner: int = Depends(get_owner),
):
    return services.view_payment(db, services.register_payment(db, owner, ident, data))


@app.get("/occurrences/{ident}/payments", response_model=list[schemas.PaymentOut])
def payments(ident: UUID, db: Session = Depends(get_db, scope="function"), owner: int = Depends(get_owner)):
    services.occurrence(db, owner, ident)
    return [
        services.view_payment(db, p)
        for p in db.scalars(
            select(Payment).where(Payment.occurrence_id == ident).order_by(Payment.created_at, Payment.id)
        )
    ]


@app.get("/occurrences/{ident}/notifications", response_model=list[schemas.NotificationOut])
def notifications(
    ident: UUID, db: Session = Depends(get_db, scope="function"), owner: int = Depends(get_owner)
):
    services.occurrence(db, owner, ident)
    return list(
        db.scalars(
            select(Notification)
            .where(Notification.occurrence_id == ident)
            .order_by(Notification.scheduled_at)
        )
    )


@app.get("/audit", response_model=list[schemas.AuditOut])
def audit(
    entity_id: UUID | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db, scope="function"),
    owner: int = Depends(get_owner),
):
    query = select(Audit).where(Audit.owner_id == owner)
    if entity_id:
        query = query.where(Audit.entity_id == entity_id)
    return list(db.scalars(query.order_by(Audit.created_at, Audit.id).limit(limit).offset(offset)))


@app.post("/payments/{ident}/corrections", response_model=schemas.PaymentOut)
def correct_payment(
    ident: UUID,
    data: schemas.PaymentCorrectionCreate,
    db: Session = Depends(get_db, scope="function"),
    owner: int = Depends(get_owner),
):
    return services.view_payment(db, services.correct_payment(db, owner, ident, data))


@app.post("/recurring/{ident}/restore", response_model=schemas.RecurringOut)
def restore_recurring(
    ident: UUID,
    data: schemas.RestoreRequest,
    db: Session = Depends(get_db, scope="function"),
    owner: int = Depends(get_owner),
):
    return services.restore_recurring(db, owner, ident, data.first_date)


@app.get("/summary")
def monthly_summary(
    month: date | None = Query(default=None, ge=date(2000, 1, 1), le=date(2200, 12, 31)),
    db: Session = Depends(get_db, scope="function"),
    owner: int = Depends(get_owner),
):
    return services.monthly_summary(db, owner, month)
