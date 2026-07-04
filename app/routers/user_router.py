import uuid

from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models import User, UserRole, Server, VM, PortForward, VMStatus, ServerStatus, VMAssignment, VMType
from ..auth import get_current_user, hash_password, verify_password
from ..utils import set_flash, get_active_announcements
from ..services.ssh_client import SSHClient
from ..services.vm_manager import VMManager

router = APIRouter(prefix="", tags=["user"])
templates = Jinja2Templates(directory="app/templates")


async def _get_own_vm(db: AsyncSession, user: User, vm_id: int):
    if user.role == UserRole.SUBUSER:
        result = await db.execute(
            select(VM).join(VMAssignment).where(
                VM.id == vm_id, VMAssignment.subuser_id == user.id
            ))
        return result.scalar_one_or_none()
    result = await db.execute(select(VM).where(VM.id == vm_id, VM.owner_id == user.id))
    return result.scalar_one_or_none()


async def _get_visible_vms(db: AsyncSession, user: User):
    if user.role == UserRole.SUBUSER:
        result = await db.execute(
            select(VM).join(VMAssignment).where(VMAssignment.subuser_id == user.id)
            .order_by(VM.id.desc()))
        return result.scalars().all()
    result = await db.execute(
        select(VM).where(VM.owner_id == user.id).order_by(VM.id.desc()))
    return result.scalars().all()


def _err_text(result: dict) -> str:
    return (result.get("stderr") or result.get("stdout") or "").strip()[:200] or "远端命令执行失败"


async def _vm_action(request: Request, vm_id: int, db: AsyncSession,
                     action, success_status, success_msg: str, fail_action: str):
    user = await get_current_user(request, db)
    vm = await _get_own_vm(db, user, vm_id)
    if not vm:
        raise HTTPException(status_code=404, detail="虚拟机不存在")
    server = await db.get(Server, vm.server_id)
    if not server:
        msg, level = "所属服务器不存在", "danger"
    else:
        try:
            ssh = SSHClient(server.host, server.port, server.ssh_user, server.ssh_password)
            mgr = VMManager(ssh)
            result = await action(mgr, vm)
            await ssh.close()
            if result["code"] == 0:
                if success_status is not None:
                    vm.status = success_status
                    await db.commit()
                msg, level = success_msg, "success"
            else:
                msg, level = f"{fail_action}失败:{_err_text(result)}", "danger"
        except Exception as e:
            msg, level = f"{fail_action}失败:{e}", "danger"
    resp = RedirectResponse(url=f"/vms/{vm_id}", status_code=302)
    set_flash(resp, msg, level)
    return resp


def _vm_id(vm):
    return vm.name if vm.vm_type == VMType.INCUS else vm.uuid


# ─────────────────────── Dashboard ───────────────────────

@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_current_user(request, db)
    vms = await _get_visible_vms(db, user)
    running = sum(1 for v in vms if v.status == VMStatus.RUNNING)
    servers = (await db.execute(
        select(Server).where(
            Server.status != ServerStatus.OFFLINE,
            (Server.owner_id == None) | (Server.owner_id == user.id)
        )
    )).scalars().all()
    announcements = await get_active_announcements(db)
    subusers = []
    if user.role == UserRole.USER:
        subusers = (await db.execute(
            select(User).where(User.parent_id == user.id, User.role == UserRole.SUBUSER)
            .options(selectinload(User.vm_assignments))
        )).scalars().all()
    return templates.TemplateResponse("user/dashboard.html", {
        "request": request, "user": user, "vms": vms,
        "running": running, "total": len(vms), "servers": servers,
        "announcements": announcements, "subusers": subusers,
    })


# ─────────────────────── VM CRUD ───────────────────────

@router.get("/vms", response_class=HTMLResponse)
async def vm_list(request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_current_user(request, db)
    vms = await _get_visible_vms(db, user)
    return templates.TemplateResponse("user/vms.html", {
        "request": request, "user": user, "vms": vms})


@router.get("/vms/create", response_class=HTMLResponse)
async def vm_create_page(request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_current_user(request, db)
    if user.role == UserRole.SUBUSER:
        raise HTTPException(status_code=403, detail="子账号无权创建虚拟机")
    servers = (await db.execute(
        select(Server).where(
            (Server.owner_id == None) | (Server.owner_id == user.id)
        ).order_by(Server.id)
    )).scalars().all()
    server_details = {}
    for s in servers:
        try:
            ssh = SSHClient(s.host, s.port, s.ssh_user, s.ssh_password)
            mgr = VMManager(ssh)
            info = await mgr.get_host_info()
            await ssh.close()
            server_details[s.id] = info
        except Exception:
            server_details[s.id] = {}
    return templates.TemplateResponse("user/vm_create.html", {
        "request": request, "user": user, "servers": servers,
        "server_details": server_details,
    })


@router.get("/api/server/{server_id}/ipv6")
async def get_server_ipv6(request: Request, server_id: int, db: AsyncSession = Depends(get_db)):
    from fastapi.responses import JSONResponse
    user = await get_current_user(request, db)
    if user.role == UserRole.SUBUSER:
        return JSONResponse({"error": "无权访问"}, status_code=403)
    server = await db.get(Server, server_id)
    if not server:
        return JSONResponse({"error": "服务器不存在"}, status_code=404)
    try:
        ssh = SSHClient(server.host, server.port, server.ssh_user, server.ssh_password)
        mgr = VMManager(ssh)
        addrs = await mgr.get_ipv6_addresses()
        await ssh.close()
        return JSONResponse({"addresses": addrs})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/vms/create")
async def vm_create(request: Request, name: str = Form(...), server_id: int = Form(...),
                    cpus: int = Form(1), memory_mb: int = Form(512), disk_gb: int = Form(5),
                    os_image: str = Form("debian-12"), vm_type: str = Form("cloud-hypervisor"),
                    ipv6: str = Form(""), db: AsyncSession = Depends(get_db)):
    user = await get_current_user(request, db)
    if user.role == UserRole.SUBUSER:
        raise HTTPException(status_code=403, detail="子账号无权创建虚拟机")
    server = await db.get(Server, server_id)
    if not server:
        resp = RedirectResponse(url="/vms/create", status_code=302)
        set_flash(resp, "所选服务器不存在", "danger")
        return resp
    vm_uuid = str(uuid.uuid4())[:8]
    try:
        ssh = SSHClient(server.host, server.port, server.ssh_user, server.ssh_password)
        mgr = VMManager(ssh)
        if vm_type == "incus":
            result = await mgr.create_incus(name, cpus, memory_mb, disk_gb, os_image,
                                            ipv6=ipv6 if ipv6 else None)
        else:
            result = await mgr.create_vm(vm_uuid, name, cpus, memory_mb, disk_gb, os_image)
        await ssh.close()
        if result["code"] != 0:
            resp = RedirectResponse(url="/vms/create", status_code=302)
            set_flash(resp, f"创建失败:{_err_text(result)}", "danger")
            return resp
    except Exception as e:
        resp = RedirectResponse(url="/vms/create", status_code=302)
        set_flash(resp, f"创建失败:{e}", "danger")
        return resp
    vm = VM(
        name=name, uuid=vm_uuid, owner_id=user.id, server_id=server_id,
        vm_type=VMType(vm_type) if vm_type in ["cloud-hypervisor", "incus"] else VMType.CLOUD_HYPERVISOR,
        cpus=cpus, memory_mb=memory_mb, disk_gb=disk_gb, os_image=os_image,
        ipv6=ipv6 if ipv6 else None,
        status=VMStatus.STOPPED, config={},
    )
    db.add(vm)
    await db.commit()
    resp = RedirectResponse(url="/vms", status_code=302)
    set_flash(resp, "虚拟机创建成功", "success")
    return resp


# ─────────────────────── VM Detail & Actions ───────────────────────

@router.get("/vms/{vm_id}", response_class=HTMLResponse)
async def vm_detail(request: Request, vm_id: int, db: AsyncSession = Depends(get_db)):
    user = await get_current_user(request, db)
    vm = await _get_own_vm(db, user, vm_id)
    if not vm:
        raise HTTPException(status_code=404, detail="虚拟机不存在")
    port_forwards = (await db.execute(
        select(PortForward).where(PortForward.vm_id == vm.id).order_by(PortForward.id)
    )).scalars().all()
    server = await db.get(Server, vm.server_id)
    return templates.TemplateResponse("user/vm_detail.html", {
        "request": request, "user": user, "vm": vm,
        "port_forwards": port_forwards, "server": server,
    })


@router.post("/vms/{vm_id}/start")
async def vm_start(request: Request, vm_id: int, db: AsyncSession = Depends(get_db)):
    return await _vm_action(request, vm_id, db,
                            lambda mgr, vm: mgr.start_vm(_vm_id(vm), vm.vm_type.value),
                            VMStatus.RUNNING, "虚拟机已启动", "启动")


@router.post("/vms/{vm_id}/stop")
async def vm_stop(request: Request, vm_id: int, db: AsyncSession = Depends(get_db)):
    return await _vm_action(request, vm_id, db,
                            lambda mgr, vm: mgr.stop_vm(_vm_id(vm), vm.vm_type.value),
                            VMStatus.STOPPED, "虚拟机已停止", "停止")


@router.post("/vms/{vm_id}/restart")
async def vm_restart(request: Request, vm_id: int, db: AsyncSession = Depends(get_db)):
    return await _vm_action(request, vm_id, db,
                            lambda mgr, vm: mgr.restart_vm(_vm_id(vm), vm.vm_type.value),
                            VMStatus.RUNNING, "虚拟机已重启", "重启")


@router.post("/vms/{vm_id}/delete")
async def vm_delete(request: Request, vm_id: int, db: AsyncSession = Depends(get_db)):
    user = await get_current_user(request, db)
    if user.role == UserRole.SUBUSER:
        raise HTTPException(status_code=403, detail="子账号无权删除虚拟机")
    vm = await _get_own_vm(db, user, vm_id)
    if not vm:
        raise HTTPException(status_code=404, detail="虚拟机不存在")
    server = await db.get(Server, vm.server_id)
    msg, level = "虚拟机已删除", "success"
    if server:
        try:
            ssh = SSHClient(server.host, server.port, server.ssh_user, server.ssh_password)
            mgr = VMManager(ssh)
            await mgr.destroy_vm(_vm_id(vm), vm.vm_type.value)
            await ssh.close()
        except Exception as e:
            msg, level = f"远端清理失败,本地记录已删除:{e}", "warning"
    await db.delete(vm)
    await db.commit()
    resp = RedirectResponse(url="/vms", status_code=302)
    set_flash(resp, msg, level)
    return resp


@router.post("/vms/{vm_id}/reset-password")
async def vm_reset_password(request: Request, vm_id: int, password: str = Form(...),
                            db: AsyncSession = Depends(get_db)):
    user = await get_current_user(request, db)
    if user.role == UserRole.SUBUSER:
        raise HTTPException(status_code=403, detail="子账号无权重置密码")
    vm = await _get_own_vm(db, user, vm_id)
    if not vm:
        raise HTTPException(status_code=404, detail="虚拟机不存在")
    server = await db.get(Server, vm.server_id)
    if not server:
        msg, level = "所属服务器不存在", "danger"
    else:
        try:
            ssh = SSHClient(server.host, server.port, server.ssh_user, server.ssh_password)
            mgr = VMManager(ssh)
            result = await mgr.reset_password(_vm_id(vm), password, vm.vm_type.value)
            await ssh.close()
            if result["code"] == 0:
                vm.password = password
                await db.commit()
                msg, level = "密码已重置", "success"
            else:
                msg, level = f"重置失败:{_err_text(result)}", "danger"
        except Exception as e:
            msg, level = f"重置失败:{e}", "danger"
    resp = RedirectResponse(url=f"/vms/{vm_id}", status_code=302)
    set_flash(resp, msg, level)
    return resp


@router.post("/vms/{vm_id}/port-forward")
async def add_port_forward(request: Request, vm_id: int,
                           host_port: int = Form(...), guest_port: int = Form(...),
                           protocol: str = Form("tcp"), db: AsyncSession = Depends(get_db)):
    user = await get_current_user(request, db)
    if user.role == UserRole.SUBUSER:
        raise HTTPException(status_code=403, detail="子账号无权管理端口转发")
    vm = await _get_own_vm(db, user, vm_id)
    if not vm:
        raise HTTPException(status_code=404, detail="虚拟机不存在")
    if not (1 <= host_port <= 65535) or not (1 <= guest_port <= 65535):
        resp = RedirectResponse(url=f"/vms/{vm_id}", status_code=302)
        set_flash(resp, "端口范围为 1-65535", "danger")
        return resp
    pf = PortForward(vm_id=vm.id, host_port=host_port, guest_port=guest_port, protocol=protocol)
    db.add(pf)
    await db.commit()
    resp = RedirectResponse(url=f"/vms/{vm_id}", status_code=302)
    set_flash(resp, "端口转发规则已添加", "success")
    return resp


@router.post("/vms/{vm_id}/port-forward/{pf_id}/delete")
async def del_port_forward(request: Request, vm_id: int, pf_id: int,
                           db: AsyncSession = Depends(get_db)):
    user = await get_current_user(request, db)
    if user.role == UserRole.SUBUSER:
        raise HTTPException(status_code=403, detail="子账号无权管理端口转发")
    vm = await _get_own_vm(db, user, vm_id)
    if not vm:
        raise HTTPException(status_code=404, detail="虚拟机不存在")
    pf = await db.get(PortForward, pf_id)
    if pf and pf.vm_id == vm.id:
        await db.delete(pf)
        await db.commit()
    resp = RedirectResponse(url=f"/vms/{vm_id}", status_code=302)
    set_flash(resp, "端口转发规则已删除", "success")
    return resp


# ─────────────────────── Subuser Management (parent) ───────────────────────

@router.get("/subusers", response_class=HTMLResponse)
async def subuser_list(request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_current_user(request, db)
    if user.role != UserRole.USER:
        raise HTTPException(status_code=403, detail="无权访问")
    subusers = (await db.execute(
        select(User).where(User.parent_id == user.id, User.role == UserRole.SUBUSER)
        .options(selectinload(User.vm_assignments).selectinload(VMAssignment.vm))
        .order_by(User.id)
    )).scalars().all()
    return templates.TemplateResponse("user/subusers.html", {
        "request": request, "user": user, "subusers": subusers})


@router.post("/subusers/create")
async def subuser_create(request: Request, username: str = Form(...),
                         password: str = Form(...), db: AsyncSession = Depends(get_db)):
    user = await get_current_user(request, db)
    if user.role != UserRole.USER:
        raise HTTPException(status_code=403, detail="无权操作")
    existing = await db.execute(select(User).where(User.username == username))
    if existing.scalar_one_or_none():
        resp = RedirectResponse(url="/subusers", status_code=302)
        set_flash(resp, "用户名已存在", "danger")
        return resp
    if len(password) < 6:
        resp = RedirectResponse(url="/subusers", status_code=302)
        set_flash(resp, "密码至少需要 6 位", "danger")
        return resp
    subuser = User(username=username, password_hash=hash_password(password),
                   role=UserRole.SUBUSER, parent_id=user.id)
    db.add(subuser)
    await db.commit()
    resp = RedirectResponse(url="/subusers", status_code=302)
    set_flash(resp, "子账号创建成功", "success")
    return resp


@router.post("/subusers/{subuser_id}/toggle")
async def subuser_toggle(request: Request, subuser_id: int, db: AsyncSession = Depends(get_db)):
    user = await get_current_user(request, db)
    if user.role != UserRole.USER:
        raise HTTPException(status_code=403, detail="无权操作")
    subuser = (await db.execute(
        select(User).where(User.id == subuser_id, User.parent_id == user.id)
    )).scalar_one_or_none()
    if subuser:
        subuser.is_active = not subuser.is_active
        await db.commit()
    resp = RedirectResponse(url="/subusers", status_code=302)
    set_flash(resp, "子账号状态已更新", "success")
    return resp


@router.post("/subusers/{subuser_id}/reset-password")
async def subuser_reset_password(request: Request, subuser_id: int,
                                 password: str = Form(...), db: AsyncSession = Depends(get_db)):
    user = await get_current_user(request, db)
    if user.role != UserRole.USER:
        raise HTTPException(status_code=403, detail="无权操作")
    if len(password) < 6:
        resp = RedirectResponse(url="/subusers", status_code=302)
        set_flash(resp, "密码至少需要 6 位", "danger")
        return resp
    subuser = (await db.execute(
        select(User).where(User.id == subuser_id, User.parent_id == user.id)
    )).scalar_one_or_none()
    if subuser:
        subuser.password_hash = hash_password(password)
        await db.commit()
    resp = RedirectResponse(url="/subusers", status_code=302)
    set_flash(resp, "密码已重置", "success")
    return resp


@router.post("/subusers/{subuser_id}/delete")
async def subuser_delete(request: Request, subuser_id: int, db: AsyncSession = Depends(get_db)):
    user = await get_current_user(request, db)
    if user.role != UserRole.USER:
        raise HTTPException(status_code=403, detail="无权操作")
    subuser = (await db.execute(
        select(User).where(User.id == subuser_id, User.parent_id == user.id)
    )).scalar_one_or_none()
    if subuser:
        await db.delete(subuser)
        await db.commit()
    resp = RedirectResponse(url="/subusers", status_code=302)
    set_flash(resp, "子账号已删除", "success")
    return resp


# ─────────────────────── VM Assignment Management ───────────────────────

@router.get("/subusers/{subuser_id}/assign", response_class=HTMLResponse)
async def subuser_assign_page(request: Request, subuser_id: int, db: AsyncSession = Depends(get_db)):
    user = await get_current_user(request, db)
    if user.role != UserRole.USER:
        raise HTTPException(status_code=403, detail="无权访问")
    subuser = (await db.execute(
        select(User).where(User.id == subuser_id, User.parent_id == user.id)
    )).scalar_one_or_none()
    if not subuser:
        raise HTTPException(status_code=404, detail="子账号不存在")
    my_vms = (await db.execute(
        select(VM).where(VM.owner_id == user.id).order_by(VM.id.desc())
    )).scalars().all()
    assigned_ids = {a.vm_id for a in
                    (await db.execute(
                        select(VMAssignment).where(VMAssignment.subuser_id == subuser_id)
                    )).scalars().all()}
    return templates.TemplateResponse("user/subuser_assign.html", {
        "request": request, "user": user, "subuser": subuser,
        "my_vms": my_vms, "assigned_ids": assigned_ids})


@router.post("/subusers/{subuser_id}/assign/{vm_id}")
async def subuser_assign_vm(request: Request, subuser_id: int, vm_id: int,
                            db: AsyncSession = Depends(get_db)):
    user = await get_current_user(request, db)
    if user.role != UserRole.USER:
        raise HTTPException(status_code=403, detail="无权操作")
    subuser = (await db.execute(
        select(User).where(User.id == subuser_id, User.parent_id == user.id)
    )).scalar_one_or_none()
    if not subuser:
        raise HTTPException(status_code=404, detail="子账号不存在")
    vm = (await db.execute(
        select(VM).where(VM.id == vm_id, VM.owner_id == user.id)
    )).scalar_one_or_none()
    if not vm:
        raise HTTPException(status_code=404, detail="虚拟机不存在")
    existing = (await db.execute(
        select(VMAssignment).where(VMAssignment.vm_id == vm_id, VMAssignment.subuser_id == subuser_id)
    )).scalar_one_or_none()
    if not existing:
        db.add(VMAssignment(vm_id=vm_id, subuser_id=subuser_id))
        await db.commit()
    resp = RedirectResponse(url=f"/subusers/{subuser_id}/assign", status_code=302)
    set_flash(resp, "已分配虚拟机", "success")
    return resp


@router.post("/subusers/{subuser_id}/unassign/{vm_id}")
async def subuser_unassign_vm(request: Request, subuser_id: int, vm_id: int,
                              db: AsyncSession = Depends(get_db)):
    user = await get_current_user(request, db)
    if user.role != UserRole.USER:
        raise HTTPException(status_code=403, detail="无权操作")
    subuser = (await db.execute(
        select(User).where(User.id == subuser_id, User.parent_id == user.id)
    )).scalar_one_or_none()
    if not subuser:
        raise HTTPException(status_code=404, detail="子账号不存在")
    assignment = (await db.execute(
        select(VMAssignment).where(VMAssignment.vm_id == vm_id, VMAssignment.subuser_id == subuser_id)
    )).scalar_one_or_none()
    if assignment:
        await db.delete(assignment)
        await db.commit()
    resp = RedirectResponse(url=f"/subusers/{subuser_id}/assign", status_code=302)
    set_flash(resp, "已取消分配", "success")
    return resp


# ─────────────────────── User Server Management ───────────────────────

@router.get("/servers", response_class=HTMLResponse)
async def user_servers(request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_current_user(request, db)
    if user.role != UserRole.USER:
        raise HTTPException(status_code=403, detail="无权访问")
    servers = (await db.execute(
        select(Server).where(Server.owner_id == user.id).order_by(Server.id.desc())
    )).scalars().all()
    return templates.TemplateResponse("user/servers.html", {
        "request": request, "user": user, "servers": servers})


@router.post("/servers/create")
async def user_create_server(request: Request, name: str = Form(...), host: str = Form(...),
                             port: int = Form(22), ssh_user: str = Form("root"),
                             ssh_password: str = Form(...), db: AsyncSession = Depends(get_db)):
    user = await get_current_user(request, db)
    if user.role != UserRole.USER:
        raise HTTPException(status_code=403, detail="无权操作")
    server = Server(name=name, host=host, port=port, ssh_user=ssh_user, ssh_password=ssh_password,
                    owner_id=user.id, status=ServerStatus.UNKNOWN)
    db.add(server)
    await db.commit()
    resp = RedirectResponse(url="/servers", status_code=302)
    set_flash(resp, "服务器已添加,请点击检测验证连接", "success")
    return resp


@router.post("/servers/{server_id}/delete")
async def user_delete_server(request: Request, server_id: int, db: AsyncSession = Depends(get_db)):
    user = await get_current_user(request, db)
    if user.role != UserRole.USER:
        raise HTTPException(status_code=403, detail="无权操作")
    server = (await db.execute(
        select(Server).where(Server.id == server_id, Server.owner_id == user.id)
    )).scalar_one_or_none()
    if server:
        vm_count = (await db.execute(
            select(VM).where(VM.server_id == server.id)
        )).scalars().all()
        if vm_count:
            resp = RedirectResponse(url="/servers", status_code=302)
            set_flash(resp, "服务器下还有虚拟机,请先删除所有虚拟机", "danger")
            return resp
        await db.delete(server)
        await db.commit()
    resp = RedirectResponse(url="/servers", status_code=302)
    set_flash(resp, "服务器已删除", "success")
    return resp


@router.post("/servers/{server_id}/check")
async def user_check_server(request: Request, server_id: int, db: AsyncSession = Depends(get_db)):
    user = await get_current_user(request, db)
    if user.role != UserRole.USER:
        raise HTTPException(status_code=403, detail="无权操作")
    server = (await db.execute(
        select(Server).where(Server.id == server_id, Server.owner_id == user.id)
    )).scalar_one_or_none()
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
    resp = RedirectResponse(url="/servers", status_code=302)
    set_flash(resp, msg, level)
    return resp


# ─────────────────────── Profile ───────────────────────

@router.get("/profile", response_class=HTMLResponse)
async def profile(request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_current_user(request, db)
    vm_count = await _get_visible_vms(db, user)
    return templates.TemplateResponse("user/profile.html", {
        "request": request, "user": user, "vm_count": len(vm_count),
    })


@router.post("/profile/password")
async def change_password(request: Request, old_password: str = Form(...),
                          new_password: str = Form(...), db: AsyncSession = Depends(get_db)):
    user = await get_current_user(request, db)
    if not verify_password(old_password, user.password_hash):
        resp = RedirectResponse(url="/profile", status_code=302)
        set_flash(resp, "原密码错误", "danger")
        return resp
    if len(new_password) < 6:
        resp = RedirectResponse(url="/profile", status_code=302)
        set_flash(resp, "新密码至少需要 6 位", "danger")
        return resp
    user.password_hash = hash_password(new_password)
    await db.commit()
    resp = RedirectResponse(url="/profile", status_code=302)
    set_flash(resp, "密码修改成功", "success")
    return resp
