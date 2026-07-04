from sqlalchemy import text, inspect
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase

from .config import DATABASE_URL

engine = create_async_engine(DATABASE_URL, echo=False)
async_session = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db():
    async with async_session() as session:
        yield session


def _migrate(conn):
    from .models import Server, User, VMAssignment  # noqa: F401  确保表已注册
    inspector = inspect(conn)

    # --- servers: ssh_key -> ssh_password ---
    if "servers" in inspector.get_table_names():
        cols = {c["name"] for c in inspector.get_columns("servers")}
        if "ssh_password" not in cols:
            conn.execute(text("ALTER TABLE servers ADD COLUMN ssh_password TEXT"))
        if "ssh_key" in cols:
            try:
                conn.execute(text("ALTER TABLE servers DROP COLUMN ssh_key"))
            except Exception:
                conn.execute(text("ALTER TABLE servers RENAME TO _servers_old"))
                Base.metadata.create_all(conn, tables=[Base.metadata.tables["servers"]])
                conn.execute(text(
                    "INSERT INTO servers (id,name,host,port,ssh_user,ssh_password,status,"
                    "cpu_cores,memory_mb,disk_gb,created_at,updated_at) "
                    "SELECT id,name,host,port,ssh_user,ssh_password,status,cpu_cores,memory_mb,"
                    "disk_gb,created_at,updated_at FROM _servers_old"
                ))
                conn.execute(text("DROP TABLE _servers_old"))

    # --- users: add parent_id column ---
    if "users" in inspector.get_table_names():
        cols = {c["name"] for c in inspector.get_columns("users")}
        if "parent_id" not in cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN parent_id INTEGER"))

    # --- servers: add owner_id column ---
    if "servers" in inspector.get_table_names():
        cols = {c["name"] for c in inspector.get_columns("servers")}
        if "owner_id" not in cols:
            conn.execute(text("ALTER TABLE servers ADD COLUMN owner_id INTEGER"))

    # --- vms: add vm_type and ipv6 columns ---
    if "vms" in inspector.get_table_names():
        cols = {c["name"] for c in inspector.get_columns("vms")}
        if "vm_type" not in cols:
            conn.execute(text("ALTER TABLE vms ADD COLUMN vm_type VARCHAR(32) DEFAULT 'cloud-hypervisor'"))
        if "ipv6" not in cols:
            conn.execute(text("ALTER TABLE vms ADD COLUMN ipv6 VARCHAR(64)"))


async def init_db():
    from .models import User, Server, VM, PortForward, SiteSetting, Announcement, OperationLog, VMAssignment
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_migrate)
