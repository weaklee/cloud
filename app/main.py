import os
from contextlib import asynccontextmanager

import re
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .config import DATA_DIR, SECRET_KEY
from .database import init_db, get_db, async_session
from .logger import setup_logging
from .routers import auth_router, user_router, admin_router
from .models import OperationLog
from .auth import decode_token


@asynccontextmanager
async def lifespan(app: FastAPI):
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(os.path.join(DATA_DIR, "logs"), exist_ok=True)
    setup_logging()
    await init_db()
    yield


app = FastAPI(title="cloud Panel", lifespan=lifespan)
templates = Jinja2Templates(directory="app/templates")

static_dir = os.path.join(os.path.dirname(__file__), "..", "static")
os.makedirs(static_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")

app.include_router(auth_router.router)
app.include_router(user_router.router)
app.include_router(admin_router.router)


@app.middleware("http")
async def log_operations(request: Request, call_next):
    response = await call_next(request)
    if request.method == "POST" and response.status_code < 400:
        path = request.url.path
        if any(p in path for p in ["login", "logout", "static"]):
            return response
        username = ""
        try:
            token = request.cookies.get("token")
            if token:
                payload = decode_token(token)
                username = payload.get("username") or payload.get("sub", "")
        except Exception:
            pass
        if username:
            log_entry = OperationLog(
                username=username,
                action=request.method,
                target=path,
                detail=str(dict(request.query_params))[:200],
                ip_address=request.client.host if request.client else "",
            )
            async with async_session() as session:
                session.add(log_entry)
                await session.commit()
    return response


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    if request.cookies.get("token"):
        return RedirectResponse(url="/dashboard")
    return RedirectResponse(url="/login")


@app.exception_handler(404)
async def not_found(request: Request, exc):
    if request.url.path.startswith("/admin"):
        return RedirectResponse(url="/admin/dashboard")
    return RedirectResponse(url="/dashboard")
