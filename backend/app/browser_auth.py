"""Одноразовый код из Telegram и непрозрачная HttpOnly-сессия браузера."""

import base64
import hashlib
import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import BrowserSession, LoginCode, LoginGuard, utcnow
from app.schemas import BrowserLogin
from app.services import lock_owner

router = APIRouter(prefix="/auth", tags=["browser login"])


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def cookie_name(settings):
    return "__Host-pt_session" if settings.app_env == "production" else "pt_session"


def check_browser_request(request, settings):
    if (
        request.headers.get("origin") != settings.app_url.rstrip("/")
        or request.headers.get("x-platezhnik-client") != "web"
    ):
        raise HTTPException(403, "Запрос должен прийти с сайта Платёжника")


def issue_code(db, owner):
    lock_owner(db, owner)
    code = base64.b32encode(secrets.token_bytes(8)).decode().rstrip("=")[:12]
    row = db.get(LoginCode, owner)
    if row is None:
        row = LoginCode(owner_id=owner)
        db.add(row)
    row.code_hash, row.expires_at = digest(code), utcnow() + timedelta(minutes=5)
    guard = db.get(LoginGuard, owner)
    if guard:
        guard.failures, guard.window_start = 0, utcnow()
    return "-".join(code[i : i + 4] for i in range(0, 12, 4))


def revoke_sessions(db, owner):
    lock_owner(db, owner)
    db.execute(delete(BrowserSession).where(BrowserSession.owner_id == owner))
    db.execute(delete(LoginCode).where(LoginCode.owner_id == owner))


def session_owner(db, request, settings):
    token = request.cookies.get(cookie_name(settings), "")
    row = db.get(BrowserSession, digest(token)) if token else None
    if row is None or row.expires_at <= utcnow() or row.owner_id != settings.owner_telegram_id:
        raise HTTPException(401, "Войди по одноразовому коду из бота")
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        check_browser_request(request, settings)
    return row.owner_id


@router.post("/login")
def login(data: BrowserLogin, request: Request, db: Session = Depends(get_db, scope="function")):
    settings = get_settings()
    check_browser_request(request, settings)
    owner, now = settings.owner_telegram_id, utcnow()
    lock_owner(db, owner)
    guard = db.get(LoginGuard, owner)
    if not guard:
        guard = LoginGuard(owner_id=owner, failures=0, window_start=now)
        db.add(guard)
    if guard.window_start + timedelta(minutes=1) <= now:
        guard.failures, guard.window_start = 0, now
    if guard.failures >= 10:
        return JSONResponse(
            {"detail": "Слишком много попыток. Подожди минуту или запроси новый /login в боте."},
            status_code=429,
        )
    normalized = data.code.upper().replace("-", "").replace(" ", "").strip()
    code = db.get(LoginCode, owner)
    if (
        code is None
        or code.expires_at <= now
        or not secrets.compare_digest(code.code_hash, digest(normalized))
    ):
        guard.failures += 1
        # A normal response commits the failed-attempt counter; HTTPException would roll it back.
        return JSONResponse(
            {"detail": "Код неверный или истёк. Запроси /login в своём боте."}, status_code=401
        )
    db.delete(code)
    guard.failures = 0
    db.execute(delete(BrowserSession).where(BrowserSession.expires_at <= now))
    token = secrets.token_urlsafe(32)
    db.add(BrowserSession(token_hash=digest(token), owner_id=owner, expires_at=now + timedelta(days=7)))
    response = JSONResponse({"status": "ok", "expires_in_days": 7})
    response.set_cookie(
        cookie_name(settings),
        token,
        max_age=7 * 86400,
        httponly=True,
        secure=settings.app_env == "production",
        samesite="strict",
        path="/",
    )
    return response


@router.post("/logout")
def logout(request: Request, db: Session = Depends(get_db, scope="function")):
    settings = get_settings()
    check_browser_request(request, settings)
    token = request.cookies.get(cookie_name(settings), "")
    if token:
        db.execute(delete(BrowserSession).where(BrowserSession.token_hash == digest(token)))
    response = JSONResponse({"status": "ok"})
    response.delete_cookie(
        cookie_name(settings),
        path="/",
        secure=settings.app_env == "production",
        httponly=True,
        samesite="strict",
    )
    return response
