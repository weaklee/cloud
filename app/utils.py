from urllib.parse import quote

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import SiteSetting, Announcement


def set_flash(response, message: str, level: str = "info"):
    """附加一次性提示消息(cookie),由前端 toast 展示后清除。level: success/info/warning/danger"""
    response.set_cookie("flash_msg", quote(message), max_age=10, samesite="lax")
    response.set_cookie("flash_level", level, max_age=10, samesite="lax")
    return response


async def get_site_settings(db: AsyncSession) -> dict:
    result = await db.execute(select(SiteSetting))
    return {row.key: row.value for row in result.scalars().all()}


async def get_active_announcements(db: AsyncSession):
    result = await db.execute(
        select(Announcement).where(Announcement.is_active == True).order_by(Announcement.id.desc())
    )
    return result.scalars().all()
