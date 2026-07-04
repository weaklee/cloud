from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=4, max_length=128)


class UserCreate(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=6, max_length=128)
    role: str = "user"


class ServerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    host: str = Field(min_length=1, max_length=256)
    port: int = 22
    ssh_user: str = "root"
    ssh_password: str = Field(min_length=1)


class VMCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    server_id: int
    cpus: int = Field(default=1, ge=1, le=32)
    memory_mb: int = Field(default=512, ge=128, le=262144)
    disk_gb: int = Field(default=5, ge=1, le=2000)
    os_image: str = "debian-12"
