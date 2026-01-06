from datetime import datetime, timedelta, timezone, date
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from ..models import ScheduleRule, Student, Lesson, LessonStatus


HORIZON_DAYS = 60

def _date_range(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)

def _to_utc(student_tz: str, d: date, t_local) -> datetime:
    local_dt = datetime(d.year, d.month, d.day, t_local.hour, t_local.minute, t_local.second, tzinfo=ZoneInfo(student_tz))
    return local_dt.astimezone(timezone.utc)

async def generate_lessons_for_student(session, student_id: int):
    # студент и его TZ
    st = (await session.execute(select(Student).where(Student.id == student_id))).scalar_one()

    rules = (await session.execute(
        select(ScheduleRule).where(ScheduleRule.student_id == student_id, ScheduleRule.active == True)
    )).scalars().all()

    if not rules:
        return 0

    now_utc = datetime.now(timezone.utc)
    start_day = now_utc.date()
    end_day = (now_utc + timedelta(days=HORIZON_DAYS)).date()

    rows = []
    for r in rules:
        rule_from = max(r.start_date, start_day)
        rule_to = min(r.end_date, end_day) if r.end_date else end_day

        for d in _date_range(rule_from, rule_to):
            if d.weekday() != r.weekday:
                continue
            start_at = _to_utc(st.timezone, d, r.time_local)
            rows.append({
                "student_id": student_id,
                "start_at": start_at,
                "duration_min": r.duration_min,
                "status": LessonStatus.planned,
                "source_rule_id": r.id
            })

    if not rows:
        return 0

    stmt = insert(Lesson).values(rows)
    stmt = stmt.on_conflict_do_nothing(index_elements=["student_id", "start_at"])
    result = await session.execute(stmt)
    # result.rowcount в asyncpg может быть None; поэтому возвращаем len(rows) как "пытались вставить"
    return len(rows)
