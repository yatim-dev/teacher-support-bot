from datetime import datetime, timedelta, timezone, date
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from . import db
from .models import ScheduleRule, Student, Lesson, LessonStatus

HORIZON_DAYS = 60


def local_date_range(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def to_utc(student_tz: str, d: date, t_local) -> datetime:
    local_dt = datetime(d.year, d.month, d.day, t_local.hour, t_local.minute, t_local.second, tzinfo=ZoneInfo(student_tz))
    return local_dt.astimezone(timezone.utc)


async def generate_lessons_job():
    async with db.SessionMaker() as session:
        rules = (await session.execute(
            select(ScheduleRule).where(ScheduleRule.active == True)
        )).scalars().all()

        if not rules:
            return

        student_ids = {r.student_id for r in rules}
        students = (await session.execute(select(Student).where(Student.id.in_(student_ids)))).scalars().all()
        tz_map = {s.id: s.timezone for s in students}

        now_utc = datetime.now(timezone.utc)
        start_day = now_utc.date()
        end_day = (now_utc + timedelta(days=HORIZON_DAYS)).date()

        rows = []
        for r in rules:
            st_tz = tz_map.get(r.student_id, "Europe/Moscow")

            rule_from = max(r.start_date, start_day)
            rule_to = min(r.end_date, end_day) if r.end_date else end_day

            for d in local_date_range(rule_from, rule_to):
                if d.weekday() != r.weekday:
                    continue
                start_at = to_utc(st_tz, d, r.time_local)
                rows.append({
                    "student_id": r.student_id,
                    "start_at": start_at,
                    "duration_min": r.duration_min,
                    "status": LessonStatus.planned,
                    "source_rule_id": r.id
                })

        if not rows:
            return

        stmt = insert(Lesson).values(rows)
        stmt = stmt.on_conflict_do_nothing(index_elements=["student_id", "start_at"])
        await session.execute(stmt)
        await session.commit()
