import asyncio
import traceback

import paramiko

from ..logger import get_logger

log = get_logger("ssh_client")


class SSHClient:
    def __init__(self, host: str, port: int = 22, username: str = "root", password: str = ""):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.client = None
        log.debug("SSHClient created: %s@%s:%d", username, host, port)

    def _connect_sync(self):
        self.client = paramiko.SSHClient()
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self.client.connect(
            hostname=self.host, port=self.port, username=self.username,
            password=self.password, timeout=10, auth_timeout=10, banner_timeout=10,
            allow_agent=False, look_for_keys=False,
        )

    def _exec_sync(self, command: str, timeout: int = 60) -> tuple[int, str, str]:
        _, stdout, stderr = self.client.exec_command(command, timeout=timeout)
        exit_code = stdout.channel.recv_exit_status()
        out_text = stdout.read().decode(errors="replace")
        err_text = stderr.read().decode(errors="replace")
        return (exit_code, out_text, err_text)

    def _close_sync(self):
        self.client.close()
        self.client = None

    async def connect(self):
        if not self.password:
            log.error("SSH 密码为空 [%s@%s:%d]", self.username, self.host, self.port)
            raise ValueError("SSH 密码为空")
        log.info("SSH 连接中  %s@%s:%d", self.username, self.host, self.port)
        try:
            await asyncio.to_thread(self._connect_sync)
            log.info("SSH 连接成功  %s@%s:%d", self.username, self.host, self.port)
        except Exception as e:
            log.error("SSH 连接失败  %s@%s:%d — %s", self.username, self.host, self.port, e)
            log.debug("SSH 连接失败详细:\n%s", traceback.format_exc())
            raise

    async def exec(self, command: str, timeout: int = 60) -> tuple[int, str, str]:
        if not self.client:
            log.debug("SSH 尚未连接,自动连接 %s@%s:%d", self.username, self.host, self.port)
            await self.connect()
        log.info("SSH 执行命令 [%s@%s:%d] $ %s", self.username, self.host, self.port, command)
        try:
            exit_code, out_text, err_text = await asyncio.to_thread(
                self._exec_sync, command, timeout
            )
            log.info("SSH 命令退出码 %d [%s@%s:%d]", exit_code, self.username, self.host, self.port)
            if out_text:
                log.debug("SSH stdout (%d bytes):\n%s", len(out_text), out_text[:2000])
            if err_text:
                log.warning("SSH stderr (%d bytes):\n%s", len(err_text), err_text[:2000])
            if exit_code != 0:
                log.warning("SSH 命令返回非零 %d: %s", exit_code, command[:200])
            return (exit_code, out_text, err_text)
        except Exception as e:
            log.error("SSH 命令执行异常 [%s@%s:%d] $ %s — %s",
                       self.username, self.host, self.port, command, e)
            log.debug("SSH 命令异常详细:\n%s", traceback.format_exc())
            raise

    async def close(self):
        if self.client:
            log.debug("SSH 关闭连接 %s@%s:%d", self.username, self.host, self.port)
            await asyncio.to_thread(self._close_sync)

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *args):
        await self.close()
