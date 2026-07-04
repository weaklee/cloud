import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.getenv("DATA_DIR", os.path.join(BASE_DIR, "data"))

DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite+aiosqlite:///{DATA_DIR}/cloud-panel.db")
SECRET_KEY = os.getenv("SECRET_KEY", "change-this-to-a-random-secret-key")
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_HOURS = 24
BIND_HOST = os.getenv("BIND_HOST", "0.0.0.0")
BIND_PORT = int(os.getenv("BIND_PORT", "3000"))
