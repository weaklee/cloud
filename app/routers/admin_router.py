from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import selectinload, joinedload
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models import (User, UserRole, Server, ServerStatus, VM, SiteSetting,
                      Announcement, OperationLog, VMAssignment)
from ..auth import hash_password, require_admin
from ..utils import set_flash
from ..services.ssh_client import SSHClient
from ..services.vm_manager import VMManager

router = APIRouter(prefix="/admin", tags=["admin"])
templates = Jinja2Templates(directory="app/templates")


@router.get("", response_class=HTMLResponse)
@router.get("/dashboard", response_class=HTMLResponse)
async def admin_dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    admin = await require_admin(request, db)
    users = (await db.execute(select(User))).scalars().all()
    vms = (await db.execute(select(VM))).scalars().all()
    servers = (await db.execute(select(Server))).scalars().all()
    logs = (await db.execute(
        select(OperationLog).order_by(OperationLog.id.desc()).limit(8)
    )).scalars().all()
    subuser_count = sum(1 for u in users if u.role == UserRole.SUBUSER)
    return templates.TemplateResponse("admin/dashboard.html", {
        "request": request, "user": admin,
        "user_count": len(users), "vm_count": len(vms), "server_count": len(servers),
        "running_vms": sum(1 for v in vms if v.status.value == "running"),
        "logs": logs, "subuser_count": subuser_count,
    })


@router.get("/users", response_class=HTMLResponse)
async def admin_users(request: Request, db: AsyncSession = Depends(get_db)):
    admin = await require_admin(request, db)
    users = (await db.execute(select(User).options(selectinload(User.vms)).order_by(User.id))).scalars().all()
    return templates.TemplateResponse("admin/users.html", {
        "request": request, "user": admin, "users": users})


@router.post("/users/create")
async def admin_create_user(request: Request, username: str = Form(...), password: str = Form(...),
                            role: str = Form("user"), db: AsyncSession = Depends(get_db)):
    await require_admin(request, db)
    existing = await db.execute(select(User).where(User.username == username))
    if existing.scalar_one_or_none():
        resp = RedirectResponse(url="/admin/users", status_code=302)
        set_flash(resp, "用户名已存在", "danger")
        return resp
    if len(password) < 6:
        resp = RedirectResponse(url="/admin/users", status_code=302)
        set_flash(resp, "密码至少需要 6 位", "danger")
        return resp
    user = User(username=username, password_hash=hash_password(password),
                role=UserRole.ADMIN if role == "admin" else UserRole.USER)
    db.add(user)
    await db.commit()
    resp = RedirectResponse(url="/admin/users", status_code=302)
    set_flash(resp, "用户创建成功", "success")
    return resp


@router.post("/users/{user_id}/toggle")
async def admin_toggle_user(request: Request, user_id: int, db: AsyncSession = Depends(get_db)):
    await require_admin(request, db)
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user:
        user.is_active = not user.is_active
        await db.commit()
    resp = RedirectResponse(url="/admin/users", status_code=302)
    set_flash(resp, "用户状态已更新", "success")
    return resp


@router.post("/users/{user_id}/reset-password")
async def admin_reset_password(request: Request, user_id: int, password: str = Form(...),
                               db: AsyncSession = Depends(get_db)):
    await require_admin(request, db)
    if len(password) < 6:
        resp = RedirectResponse(url="/admin/users", status_code=302)
        set_flash(resp, "密码至少需要 6 位", "danger")
        return resp
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user:
        user.password_hash = hash_password(password)
        await db.commit()
    resp = RedirectResponse(url="/admin/users", status_code=302)
    set_flash(resp, "密码已重置", "success")
    return resp


@router.post("/users/{user_id}/delete")
async def admin_delete_user(request: Request, user_id: int, db: AsyncSession = Depends(get_db)):
    admin = await require_admin(request, db)
    if admin.id == user_id:
        resp = RedirectResponse(url="/admin/users", status_code=302)
        set_flash(resp, "不能删除当前登录的管理员账号", "danger")
        return resp
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user:
        await db.delete(user)
        await db.commit()
    resp = RedirectResponse(url="/admin/users", status_code=302)
    set_flash(resp, "用户已删除", "success")
    return resp


@router.get("/servers", response_class=HTMLResponse)
async def admin_servers(request: Request, db: AsyncSession = Depends(get_db)):
    admin = await require_admin(request, db)
    servers = (await db.execute(
        select(Server).options(selectinload(Server.vms), selectinload(Server.owner)).order_by(Server.id)
    )).scalars().all()
    return templates.TemplateResponse("admin/servers.html", {
        "request": request, "user": admin, "servers": servers})


@router.post("/servers/create")
async def admin_create_server(request: Request, name: str = Form(...), host: str = Form(...),
                              port: int = Form(22), ssh_user: str = Form("root"),
                              ssh_password: str = Form(...), db: AsyncSession = Depends(get_db)):
    await require_admin(request, db)
    server = Server(name=name, host=host, port=port, ssh_user=ssh_user, ssh_password=ssh_password,
                    status=ServerStatus.UNKNOWN)
    db.add(server)
    await db.commit()
    resp = RedirectResponse(url="/admin/servers", status_code=302)
    set_flash(resp, "服务器已添加,请点击检测验证连接", "success")
    return resp


@router.post("/servers/{server_id}/delete")
async def admin_delete_server(request: Request, server_id: int, db: AsyncSession = Depends(get_db)):
    await require_admin(request, db)
    server = (await db.execute(select(Server).where(Server.id == server_id))).scalar_one_or_none()
    if server:
        await db.delete(server)
        await db.commit()
    resp = RedirectResponse(url="/admin/servers", status_code=302)
    set_flash(resp, "服务器已删除", "success")
    return resp


@router.post("/servers/{server_id}/check")
async def admin_check_server(request: Request, server_id: int, db: AsyncSession = Depends(get_db)):
    await require_admin(request, db)
    server = (await db.execute(select(Server).where(Server.id == server_id))).scalar_one_or_none()
    if not server:
        raise HTTPException(status_code=404, detail="服务器不存在")
    msg, level = "服务器连接正常", "success"
    try:
        ssh = SSHClient(server.host, server.port, server.ssh_user, server.ssh_password)
        mgr = VMManager(ssh)
        ok = await mgr.check_connection()
        if ok:
            info = await mgr.get_host_info()
            server.status = ServerStatus.ONLINE
            try:
                server.cpu_cores = int(info.get("cpu_cores") or 0)
                server.memory_mb = int(info.get("memory_mb") or 0)
                server.disk_gb = int(info.get("disk_gb") or 0)
            except (ValueError, TypeError):
                pass
        else:
            server.status = ServerStatus.OFFLINE
            msg, level = "无法连接到服务器,请检查网络与密钥", "danger"
    except Exception as e:
        server.status = ServerStatus.OFFLINE
        msg, level = f"连接失败:{e}", "danger"
    await db.commit()
    resp = RedirectResponse(url="/admin/servers", status_code=302)
    set_flash(resp, msg, level)
    return resp


@router.get("/vms", response_class=HTMLResponse)
async def admin_vms(request: Request, db: AsyncSession = Depends(get_db)):
    admin = await require_admin(request, db)
    vms = (await db.execute(select(VM).options(joinedload(VM.owner), joinedload(VM.server)).order_by(VM.id.desc()))).scalars().all()
    return templates.TemplateResponse("admin/vms.html", {
        "request": request, "user": admin, "vms": vms})


@router.get("/settings", response_class=HTMLResponse)
async def admin_settings(request: Request, db: AsyncSession = Depends(get_db)):
    admin = await require_admin(request, db)
    settings = {row.key: row.value for row in
                (await db.execute(select(SiteSetting))).scalars().all()}
    return templates.TemplateResponse("admin/settings.html", {
        "request": request, "user": admin, "settings": settings})


@router.post("/settings")
async def admin_update_settings(request: Request, db: AsyncSession = Depends(get_db)):
    await require_admin(request, db)
    form = await request.form()
    for key, value in form.items():
        setting = (await db.execute(select(SiteSetting).where(SiteSetting.key == key))).scalar_one_or_none()
        if setting:
            setting.value = value
        else:
            db.add(SiteSetting(key=key, value=value))
    await db.commit()
    resp = RedirectResponse(url="/admin/settings", status_code=302)
    set_flash(resp, "设置已保存", "success")
    return resp


@router.get("/announcements", response_class=HTMLResponse)
async def admin_announcements(request: Request, db: AsyncSession = Depends(get_db)):
    admin = await require_admin(request, db)
    announcements = (await db.execute(
        select(Announcement).order_by(Announcement.id.desc())
    )).scalars().all()
    return templates.TemplateResponse("admin/announcements.html", {
        "request": request, "user": admin, "announcements": announcements})


@router.post("/announcements/create")
async def admin_create_announcement(request: Request, title: str = Form(...), content: str = Form(""),
                                    db: AsyncSession = Depends(get_db)):
    await require_admin(request, db)
    db.add(Announcement(title=title, content=content))
    await db.commit()
    resp = RedirectResponse(url="/admin/announcements", status_code=302)
    set_flash(resp, "公告已发布", "success")
    return resp


@router.post("/announcements/{ann_id}/toggle")
async def admin_toggle_announcement(request: Request, ann_id: int, db: AsyncSession = Depends(get_db)):
    await require_admin(request, db)
    ann = (await db.execute(select(Announcement).where(Announcement.id == ann_id))).scalar_one_or_none()
    if ann:
        ann.is_active = not ann.is_active
        await db.commit()
    resp = RedirectResponse(url="/admin/announcements", status_code=302)
    set_flash(resp, "公告状态已更新", "success")
    return resp


@router.post("/announcements/{ann_id}/delete")
async def admin_delete_announcement(request: Request, ann_id: int, db: AsyncSession = Depends(get_db)):
    await require_admin(request, db)
    ann = (await db.execute(select(Announcement).where(Announcement.id == ann_id))).scalar_one_or_none()
    if ann:
        await db.delete(ann)
        await db.commit()
    resp = RedirectResponse(url="/admin/announcements", status_code=302)
    set_flash(resp, "公告已删除", "success")
    return resp


@router.get("/logs", response_class=HTMLResponse)
async def admin_logs(request: Request, db: AsyncSession = Depends(get_db)):
    admin = await require_admin(request, db)
    logs = (await db.execute(
        select(OperationLog).order_by(OperationLog.id.desc()).limit(200)
    )).scalars().all()
    return templates.TemplateResponse("admin/logs.html", {
        "request": request, "user": admin, "logs": logs})


@router.get("/subusers", response_class=HTMLResponse)
async def admin_subusers(request: Request, db: AsyncSession = Depends(get_db)):
    admin = await require_admin(request, db)
    subusers = (await db.execute(
        select(User).where(User.role == UserRole.SUBUSER)
        .options(selectinload(User.parent), selectinload(User.vm_assignments).selectinload(VMAssignment.vm))
        .order_by(User.id)
    )).scalars().all()
    return templates.TemplateResponse("admin/subusers.html", {
        "request": request, "user": admin, "subusers": subusers})


@router.post("/subusers/create")
async def admin_create_subuser(request: Request, username: str = Form(...),
                               password: str = Form(...), parent_id: int = Form(...),
                               db: AsyncSession = Depends(get_db)):
    await require_admin(request, db)
    existing = await db.execute(select(User).where(User.username == username))
    if existing.scalar_one_or_none():
        resp = RedirectResponse(url="/admin/subusers", status_code=302)
        set_flash(resp, "用户名已存在", "danger")
        return resp
    if len(password) < 6:
        resp = RedirectResponse(url="/admin/subusers", status_code=302)
        set_flash(resp, "密码至少需要 6 位", "danger")
        return resp
    parent = await db.get(User, parent_id)
    if not parent or parent.role == UserRole.SUBUSER:
        resp = RedirectResponse(url="/admin/subusers", status_code=302)
        set_flash(resp, "父账号不存在或无效", "danger")
        return resp
    subuser = User(username=username, password_hash=hash_password(password),
                   role=UserRole.SUBUSER, parent_id=parent_id)
    db.add(subuser)
    await db.commit()
    resp = RedirectResponse(url="/admin/subusers", status_code=302)
    set_flash(resp, "子账号创建成功", "success")
    return resp


@router.post("/subusers/{subuser_id}/toggle")
async def admin_toggle_subuser(request: Request, subuser_id: int, db: AsyncSession = Depends(get_db)):
    await require_admin(request, db)
    subuser = (await db.execute(select(User).where(User.id == subuser_id))).scalar_one_or_none()
    if subuser and subuser.role == UserRole.SUBUSER:
        subuser.is_active = not subuser.is_active
        await db.commit()
    resp = RedirectResponse(url="/admin/subusers", status_code=302)
    set_flash(resp, "子账号状态已更新", "success")
    return resp


@router.post("/subusers/{subuser_id}/reset-password")
async def admin_reset_subuser_password(request: Request, subuser_id: int,
                                       password: str = Form(...), db: AsyncSession = Depends(get_db)):
    await require_admin(request, db)
    if len(password) < 6:
        resp = RedirectResponse(url="/admin/subusers", status_code=302)
        set_flash(resp, "密码至少需要 6 位", "danger")
        return resp
    subuser = (await db.execute(select(User).where(User.id == subuser_id))).scalar_one_or_none()
    if subuser and subuser.role == UserRole.SUBUSER:
        subuser.password_hash = hash_password(password)
        await db.commit()
    resp = RedirectResponse(url="/admin/subusers", status_code=302)
    set_flash(resp, "密码已重置", "success")
    return resp


@router.post("/subusers/{subuser_id}/delete")
async def admin_delete_subuser(request: Request, subuser_id: int, db: AsyncSession = Depends(get_db)):
    await require_admin(request, db)
    subuser = (await db.execute(select(User).where(User.id == subuser_id))).scalar_one_or_none()
    if subuser and subuser.role == UserRole.SUBUSER:
        await db.delete(subuser)
        await db.commit()
    resp = RedirectResponse(url="/admin/subusers", status_code=302)
    set_flash(resp, "子账号已删除", "success")
    return resp
