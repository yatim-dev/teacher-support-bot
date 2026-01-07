from datetime import date, datetime, time, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models import Student, ScheduleRule, Lesson, LessonStatus
from app.services.schedule import generate_lessons_for_student


@pytest.mark.asyncio
async def test_generate_lessons_exact_utc_for_moscow(session):
    # Фиксируем "сегодня" внутри генератора
    now_utc = datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc)  # 2026-01-05 = понедельник

    st = Student(full_name="A", timezone="Europe/Moscow")  # UTC+3 стабильно
    session.add(st)
    await session.flush()

    rule = ScheduleRule(
        student_id=st.id,
        weekday=now_utc.date().weekday(),     # понедельник
        time_local=time(10, 0),              # 10:00 по Москве
        duration_min=60,
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 31),
        active=True,
    )
    session.add(rule)
    await session.commit()

    n = await generate_lessons_for_student(session, st.id, now_utc=now_utc, horizon_days=0)
    await session.commit()

    assert n == 1

    lessons = (await session.execute(
        select(Lesson).where(Lesson.student_id == st.id)
    )).scalars().all()

    assert len(lessons) == 1
    lesson = lessons[0]

    expected_start_at = datetime(2026, 1, 5, 7, 0, tzinfo=timezone.utc)  # 10:00 MSK = 07:00 UTC
    assert lesson.start_at == expected_start_at
    assert lesson.duration_min == 60
    assert lesson.status == LessonStatus.planned
    assert lesson.source_rule_id == rule.id
