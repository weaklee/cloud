#!/usr/bin/env python3
import uvicorn
from app.config import BIND_HOST, BIND_PORT

if __name__ == "__main__":
    uvicorn.run("app.main:app", host=BIND_HOST, port=BIND_PORT, reload=False)
