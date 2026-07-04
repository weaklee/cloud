import uuid
from datetime import datetime, timezone

from sqlalchemy import String, Integer, Boolean, Text, DateTime, ForeignKey, JSON, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
import enum

from .database import Base


def _utcnow():
    return datetime.now(timezone.utc)


def _uuid():
    return str(uuid.uuid4())


class UserRole(str, enum.Enum):
    ADMIN = "admin"
    USER = "user"
    SUBUSER = "subuser"


class VMStatus(str, enum.Enum):
    STOPPED = "stopped"
    RUNNING = "running"
    ERROR = "error"


class VMType(str, enum.Enum):
    CLOUD_HYPERVISOR = "cloud-hypervisor"
    INCUS = "incus"


class ServerStatus(str, enum.Enum):
    ONLINE = "online"
    OFFLINE = "offline"
    UNKNOWN = "unknown"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    role: Mapped[UserRole] = mapped_column(SAEnum(UserRole), default=UserRole.USER)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    parent_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)

    vms = relationship("VM", back_populates="owner")
    servers = relationship("Server", back_populates="owner")
    parent = relationship("User", remote_side="User.id", back_populates="subusers")
    subusers = relationship("User", back_populates="parent")
    vm_assignments = relationship("VMAssignment", back_populates="subuser", cascade="all, delete-orphan")


class Server(Base):
    __tablename__ = "servers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    host: Mapped[str] = mapped_column(String(256), nullable=False)
    port: Mapped[int] = mapped_column(Integer, default=22)
    ssh_user: Mapped[str] = mapped_column(String(64), default="root")
    ssh_password: Mapped[str] = mapped_column(Text, default="")
    owner_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    status: Mapped[ServerStatus] = mapped_column(SAEnum(ServerStatus), default=ServerStatus.UNKNOWN)
    cpu_cores: Mapped[int] = mapped_column(Integer, default=0)
    memory_mb: Mapped[int] = mapped_column(Integer, default=0)
    disk_gb: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)

    owner = relationship("User", back_populates="servers")
    vms = relationship("VM", back_populates="server")


class VM(Base):
    __tablename__ = "vms"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    uuid: Mapped[str] = mapped_column(String(36), default=_uuid, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    owner_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    server_id: Mapped[int] = mapped_column(Integer, ForeignKey("servers.id"), nullable=False)
    vm_type: Mapped[VMType] = mapped_column(SAEnum(VMType), default=VMType.CLOUD_HYPERVISOR)
    status: Mapped[VMStatus] = mapped_column(SAEnum(VMStatus), default=VMStatus.STOPPED)
    cpus: Mapped[int] = mapped_column(Integer, default=1)
    memory_mb: Mapped[int] = mapped_column(Integer, default=512)
    disk_gb: Mapped[int] = mapped_column(Integer, default=5)
    os_image: Mapped[str] = mapped_column(String(128), default="debian-12")
    vnc_port: Mapped[int] = mapped_column(Integer, nullable=True)
    ssh_port: Mapped[int] = mapped_column(Integer, nullable=True)
    internal_ip: Mapped[str] = mapped_column(String(64), nullable=True)
    ipv6: Mapped[str] = mapped_column(String(64), nullable=True)
    password: Mapped[str] = mapped_column(String(256), nullable=True)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)

    owner = relationship("User", back_populates="vms")
    server = relationship("Server", back_populates="vms")
    port_forwards = relationship("PortForward", back_populates="vm", cascade="all, delete-orphan")


class PortForward(Base):
    __tablename__ = "port_forwards"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    vm_id: Mapped[int] = mapped_column(Integer, ForeignKey("vms.id"), nullable=False)
    host_port: Mapped[int] = mapped_column(Integer, nullable=False)
    guest_port: Mapped[int] = mapped_column(Integer, nullable=False)
    protocol: Mapped[str] = mapped_column(String(8), default="tcp")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    vm = relationship("VM", back_populates="port_forwards")


class SiteSetting(Base):
    __tablename__ = "site_settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")


class Announcement(Base):
    __tablename__ = "announcements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    content: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class OperationLog(Base):
    __tablename__ = "operation_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    username: Mapped[str] = mapped_column(String(64), default="")
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    target: Mapped[str] = mapped_column(String(256), default="")
    detail: Mapped[str] = mapped_column(Text, default="")
    ip_address: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class VMAssignment(Base):
    __tablename__ = "vm_assignments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    vm_id: Mapped[int] = mapped_column(Integer, ForeignKey("vms.id"), nullable=False)
    subuser_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    vm = relationship("VM")
    subuser = relationship("User", back_populates="vm_assignments")
