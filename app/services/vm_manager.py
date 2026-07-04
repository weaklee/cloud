import json
import shlex
import traceback

from ..logger import get_logger
from .ssh_client import SSHClient

log = get_logger("vm_manager")


class VMManager:
    def __init__(self, ssh: SSHClient):
        self.ssh = ssh
        self._tag = f"{ssh.username}@{ssh.host}:{ssh.port}"
        log.debug("VMManager created for %s", self._tag)

    # ─────────────────── Cloud-Hypervisor (cloud-ctl) ───────────────────

    async def create_vm(self, vm_id, name, cpus, memory_mb, disk_gb, os_image) -> dict:
        cmd = (
            f"cloud-ctl create --id {shlex.quote(str(vm_id))} --name {shlex.quote(str(name))} "
            f"--cpus {int(cpus)} --memory {int(memory_mb)} --disk {int(disk_gb)} "
            f"--image {shlex.quote(str(os_image))} --json 2>/dev/null"
        )
        log.info("[%s] 创建 VM(cloud-hypervisor) id=%s name=%s cpus=%d mem=%d disk=%d image=%s",
                 self._tag, vm_id, name, cpus, memory_mb, disk_gb, os_image)
        code, out, err = await self.ssh.exec(cmd)
        log.info("[%s] 创建 VM 结果 code=%d", self._tag, code)
        if code != 0:
            log.warning("[%s] 创建 VM 失败\nstdout: %s\nstderr: %s",
                        self._tag, out[:500], err[:500])
        return {"code": code, "stdout": out, "stderr": err}

    async def start_vm(self, vm_id, vm_type="cloud-hypervisor") -> dict:
        if vm_type == "incus":
            return await self._incus_start(vm_id)
        log.info("[%s] 启动 VM  %s", self._tag, vm_id)
        code, out, err = await self.ssh.exec(f"cloud-ctl start {shlex.quote(str(vm_id))} 2>/dev/null")
        if code != 0:
            log.warning("[%s] 启动 VM %s 失败\nstdout: %s\nstderr: %s",
                        self._tag, vm_id, out[:500], err[:500])
        return {"code": code, "stdout": out, "stderr": err}

    async def stop_vm(self, vm_id, vm_type="cloud-hypervisor") -> dict:
        if vm_type == "incus":
            return await self._incus_stop(vm_id)
        log.info("[%s] 停止 VM  %s", self._tag, vm_id)
        code, out, err = await self.ssh.exec(f"cloud-ctl stop {shlex.quote(str(vm_id))} 2>/dev/null")
        if code != 0:
            log.warning("[%s] 停止 VM %s 失败\nstdout: %s\nstderr: %s",
                        self._tag, vm_id, out[:500], err[:500])
        return {"code": code, "stdout": out, "stderr": err}

    async def restart_vm(self, vm_id, vm_type="cloud-hypervisor") -> dict:
        if vm_type == "incus":
            return await self._incus_restart(vm_id)
        log.info("[%s] 重启 VM  %s", self._tag, vm_id)
        code, out, err = await self.ssh.exec(f"cloud-ctl restart {shlex.quote(str(vm_id))} 2>/dev/null")
        if code != 0:
            log.warning("[%s] 重启 VM %s 失败\nstdout: %s\nstderr: %s",
                        self._tag, vm_id, out[:500], err[:500])
        return {"code": code, "stdout": out, "stderr": err}

    async def destroy_vm(self, vm_id, vm_type="cloud-hypervisor") -> dict:
        if vm_type == "incus":
            return await self._incus_destroy(vm_id)
        log.info("[%s] 销毁 VM  %s", self._tag, vm_id)
        code, out, err = await self.ssh.exec(f"cloud-ctl destroy {shlex.quote(str(vm_id))} 2>/dev/null")
        if code != 0:
            log.warning("[%s] 销毁 VM %s 失败\nstdout: %s\nstderr: %s",
                        self._tag, vm_id, out[:500], err[:500])
        return {"code": code, "stdout": out, "stderr": err}

    async def reset_password(self, vm_id, new_password, vm_type="cloud-hypervisor") -> dict:
        if vm_type == "incus":
            return await self._incus_reset_password(vm_id, new_password)
        log.info("[%s] 重置 VM %s 密码", self._tag, vm_id)
        code, out, err = await self.ssh.exec(
            f"cloud-ctl reset-password {shlex.quote(str(vm_id))} {shlex.quote(str(new_password))} 2>/dev/null")
        if code != 0:
            log.warning("[%s] 重置 VM %s 密码失败\nstdout: %s\nstderr: %s",
                        self._tag, vm_id, out[:500], err[:500])
        return {"code": code, "stdout": out, "stderr": err}

    async def reinstall_vm(self, vm_id, os_image="", vm_type="cloud-hypervisor") -> dict:
        if vm_type == "incus":
            return {"code": 1, "stdout": "", "stderr": "Incus 不支持重装,请删除后重建"}
        img = f"--image {shlex.quote(str(os_image))}" if os_image else ""
        log.info("[%s] 重装 VM  %s image=%s", self._tag, vm_id, os_image or "(default)")
        code, out, err = await self.ssh.exec(f"cloud-ctl reinstall {shlex.quote(str(vm_id))} {img} 2>/dev/null")
        if code != 0:
            log.warning("[%s] 重装 VM %s 失败\nstdout: %s\nstderr: %s",
                        self._tag, vm_id, out[:500], err[:500])
        return {"code": code, "stdout": out, "stderr": err}

    async def list_images(self) -> list:
        log.info("[%s] 列出系统镜像", self._tag)
        code, out, err = await self.ssh.exec("cloud-ctl image list --json 2>/dev/null || echo '[]'")
        try:
            data = json.loads(out)
            log.info("[%s] 获取镜像列表成功, 共 %d 个", self._tag, len(data) if isinstance(data, list) else 0)
            return data
        except json.JSONDecodeError as e:
            log.warning("[%s] 解析镜像列表失败: %s", self._tag, e)
            log.debug("原始输出:\n%s", out[:1000])
            return []

    # ─────────────────── Incus Methods ───────────────────

    async def create_incus(self, name, cpus, memory_mb, disk_gb, os_image,
                           ipv4=None, ipv6=None, bridge="incusbr0") -> dict:
        log.info("[%s] 创建 Incus 容器 name=%s cpus=%d mem=%dMB disk=%dGB image=%s",
                 self._tag, name, cpus, memory_mb, disk_gb, os_image)
        limits = f"limits.cpu={cpus},limits.memory={memory_mb}MB"
        cmd = (
            f"incus init {shlex.quote(os_image)} {shlex.quote(name)} "
            f"--profile default "
            f"-c {shlex.quote(limits)} "
            f"-c security.privileged=true "
            f"-c boot.autostart=true "
            f"--disk root,size={disk_gb}GB "
            f"--network {shlex.quote(bridge)} "
            f"2>/dev/null"
        )
        if ipv4:
            cmd += f" -d root ipv4.address={shlex.quote(ipv4)}"
        if ipv6:
            cmd += f" -d root ipv6.address={shlex.quote(ipv6)}"
        code, out, err = await self.ssh.exec(cmd)
        log.info("[%s] 创建 Incus 结果 code=%d", self._tag, code)
        if code != 0:
            log.warning("[%s] 创建 Incus 失败\nstdout: %s\nstderr: %s",
                        self._tag, out[:500], err[:500])
        return {"code": code, "stdout": out, "stderr": err}

    async def _incus_start(self, name) -> dict:
        log.info("[%s] 启动 Incus  %s", self._tag, name)
        code, out, err = await self.ssh.exec(f"incus start {shlex.quote(name)} 2>/dev/null")
        if code != 0:
            log.warning("[%s] 启动 Incus %s 失败\nstdout: %s\nstderr: %s",
                        self._tag, name, out[:500], err[:500])
        return {"code": code, "stdout": out, "stderr": err}

    async def _incus_stop(self, name) -> dict:
        log.info("[%s] 停止 Incus  %s", self._tag, name)
        code, out, err = await self.ssh.exec(f"incus stop {shlex.quote(name)} 2>/dev/null")
        if code != 0:
            log.warning("[%s] 停止 Incus %s 失败\nstdout: %s\nstderr: %s",
                        self._tag, name, out[:500], err[:500])
        return {"code": code, "stdout": out, "stderr": err}

    async def _incus_restart(self, name) -> dict:
        log.info("[%s] 重启 Incus  %s", self._tag, name)
        code, out, err = await self.ssh.exec(f"incus restart {shlex.quote(name)} 2>/dev/null")
        if code != 0:
            log.warning("[%s] 重启 Incus %s 失败\nstdout: %s\nstderr: %s",
                        self._tag, name, out[:500], err[:500])
        return {"code": code, "stdout": out, "stderr": err}

    async def _incus_destroy(self, name) -> dict:
        log.info("[%s] 销毁 Incus  %s", self._tag, name)
        code, out, err = await self.ssh.exec(f"incus delete {shlex.quote(name)} --force 2>/dev/null")
        if code != 0:
            log.warning("[%s] 销毁 Incus %s 失败\nstdout: %s\nstderr: %s",
                        self._tag, name, out[:500], err[:500])
        return {"code": code, "stdout": out, "stderr": err}

    async def _incus_reset_password(self, name, new_password) -> dict:
        log.info("[%s] 重置 Incus %s 密码", self._tag, name)
        escaped_pw = shlex.quote(new_password)
        cmd = f"incus exec {shlex.quote(name)} -- bash -c \"echo 'root:{escaped_pw}' | chpasswd\" 2>/dev/null"
        code, out, err = await self.ssh.exec(cmd)
        if code != 0:
            log.warning("[%s] 重置 Incus %s 密码失败\nstdout: %s\nstderr: %s",
                        self._tag, name, out[:500], err[:500])
        return {"code": code, "stdout": out, "stderr": err}

    async def get_incus_info(self, name) -> dict:
        log.info("[%s] 获取 Incus 信息  %s", self._tag, name)
        code, out, err = await self.ssh.exec(f"incus info {shlex.quote(name)} --format json 2>/dev/null")
        if code != 0:
            log.warning("[%s] 获取 Incus 信息失败: %s", self._tag, err[:300])
            return {}
        try:
            return json.loads(out)
        except json.JSONDecodeError:
            return {}

    async def get_incus_ipv4(self, name) -> str:
        info = await self.get_incus_info(name)
        try:
            for iface, cfg in info.get("state", {}).get("network", {}).get("addresses", []):
                if cfg.get("family") == "inet" and not cfg.get("scope", "").startswith("link"):
                    return cfg.get("address", "")
        except Exception:
            pass
        return ""

    async def list_incus_images(self) -> list:
        log.info("[%s] 列出 Incus 镜像", self._tag)
        code, out, err = await self.ssh.exec("incus image list --format json 2>/dev/null || echo '[]'")
        try:
            data = json.loads(out)
            return [img.get("aliases", [{}])[0].get("name", img.get("fingerprint", ""))
                    for img in data if img.get("aliases")]
        except Exception:
            return []

    # ─────────────────── IPv6 Detection ───────────────────

    async def get_ipv6_addresses(self) -> list:
        log.info("[%s] 获取宿主机 IPv6 地址", self._tag)
        code, out, err = await self.ssh.exec(
            "ip -6 addr show scope global | grep 'inet6' | awk '{print $2}' | cut -d'/' -f1 | sort -u"
        )
        if code != 0:
            log.warning("[%s] 获取 IPv6 失败: %s", self._tag, err[:300])
            return []
        addrs = [a.strip() for a in out.strip().splitlines() if a.strip()]
        log.info("[%s] 获取到 %d 个 IPv6 地址", self._tag, len(addrs))
        return addrs

    async def get_ipv6_subnets(self) -> list:
        log.info("[%s] 获取宿主机 IPv6 子网", self._tag)
        code, out, err = await self.ssh.exec(
            "ip -6 addr show scope global | grep 'inet6' | awk '{print $2}'"
        )
        if code != 0:
            return []
        subnets = [s.strip() for s in out.strip().splitlines() if s.strip()]
        log.info("[%s] 获取到 %d 个 IPv6 子网", self._tag, len(subnets))
        return subnets

    async def generate_ipv6_from_subnet(self, subnet: str, name: str) -> str:
        """从子网生成一个基于容器名的 IPv6 地址"""
        import hashlib
        base = subnet.split("/")[0]
        host_hash = hashlib.md5(name.encode()).hexdigest()[:8]
        parts = base.split(":")
        if len(parts) >= 4:
            parts[-1] = host_hash[:4]
            parts[-2] = host_hash[4:8]
        return ":".join(parts)

    # ─────────────────── Common ───────────────────

    async def get_host_info(self) -> dict:
        log.info("[%s] 获取宿主机信息", self._tag)
        cmds = {
            "cpu_cores": "nproc 2>/dev/null || echo 0",
            "memory_mb": "free -m 2>/dev/null | awk '/^Mem:/{print $2}' || echo 0",
            "disk_gb": "df -BG / 2>/dev/null | awk 'NR==2{print $2}' | tr -d 'G' || echo 0",
            "has_incus": "which incus >/dev/null 2>&1 && echo yes || echo no",
            "has_cloud_ctl": "which cloud-ctl >/dev/null 2>&1 && echo yes || echo no",
        }
        info = {}
        for key, cmd in cmds.items():
            _, out, _ = await self.ssh.exec(cmd)
            info[key] = out.strip()
        log.info("[%s] 宿主机信息: cpu=%s mem=%sMB disk=%sGB incus=%s cloud-ctl=%s",
                 self._tag, info.get("cpu_cores"), info.get("memory_mb"),
                 info.get("disk_gb"), info.get("has_incus"), info.get("has_cloud_ctl"))
        return info

    async def check_connection(self) -> bool:
        log.info("[%s] 检查 SSH 连接", self._tag)
        try:
            await self.ssh.connect()
            code, out, err = await self.ssh.exec("echo ok")
            ok = code == 0
            log.info("[%s] 连接检查结果: %s", self._tag, "成功" if ok else "失败")
            if not ok:
                log.warning("[%s] 连接检查失败 code=%d stderr=%s", self._tag, code, err[:500])
            return ok
        except Exception as e:
            log.error("[%s] 连接检查异常: %s", self._tag, e)
            log.debug("异常详细:\n%s", traceback.format_exc())
            return False
        finally:
            await self.ssh.close()
