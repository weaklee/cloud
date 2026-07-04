#!/usr/bin/env python3
"""Create initial admin user for cloud panel."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from app.database import init_db, async_session
from app.models import User, UserRole
from app.auth import hash_password
from sqlalchemy import select


async def seed():
    await init_db()
    async with async_session() as session:
        result = await session.execute(select(User).where(User.role == UserRole.ADMIN))
        admin = result.scalar_one_or_none()
        if admin:
            print(f"Admin user already exists: {admin.username}")
        else:
            username = os.getenv("ADMIN_USERNAME", "admin")
            password = os.getenv("ADMIN_PASSWORD", "admin123")
            user = User(username=username, password_hash=hash_password(password), role=UserRole.ADMIN)
            session.add(user)
            await session.commit()
            print(f"Admin user created: {username} / {password}")
            print("CHANGE THE DEFAULT PASSWORD IMMEDIATELY!")


if __name__ == "__main__":
    asyncio.run(seed())
