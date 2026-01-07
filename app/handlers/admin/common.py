from datetime import datetime, timezone, date, time as dtime
from zoneinfo import ZoneInfo
from sqlalchemy import select

from ...models import User, Role


async def get_user(session, tg_id: int) -> User | None:
    return (await session.execute(select(User).where(User.tg_id == tg_id))).scalar_one_or_none()


def ensure_teacher(user: User | None):
    if not user or user.role != Role.teacher:
        raise PermissionError("Teacher only")


def local_to_utc(student_tz: str, d: date, t: dtime) -> datetime:
    local_dt = datetime(d.year, d.month, d.day, t.hour, t.minute, 0, tzinfo=ZoneInfo(student_tz))
    return local_dt.astimezone(timezone.utc)
