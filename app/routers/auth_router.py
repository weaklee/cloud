from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models import User
from ..auth import verify_password, create_token

router = APIRouter(tags=["auth"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if request.cookies.get("token"):
        return RedirectResponse(url="/dashboard")
    return templates.TemplateResponse("login.html", {"request": request, "error": ""})


@router.post("/login")
async def login(request: Request, username: str = Form(...), password: str = Form(...),
                db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()
    if not user or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(
            "login.html", {"request": request, "error": "用户名或密码错误"}, status_code=401)
    if not user.is_active:
        return templates.TemplateResponse(
            "login.html", {"request": request, "error": "该账号已被禁用,请联系管理员"}, status_code=403)
    token = create_token(user.id, user.role.value, user.username)
    resp = RedirectResponse(url="/dashboard", status_code=302)
    resp.set_cookie(key="token", value=token, httponly=True, max_age=86400, samesite="lax")
    return resp


@router.get("/logout")
async def logout():
    resp = RedirectResponse(url="/login")
    resp.delete_cookie("token")
    return resp
