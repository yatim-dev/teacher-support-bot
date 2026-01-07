from datetime import date, datetime, time, timedelta, timezone

import pytest
from sqlalchemy import select, func

from app.models import Student, ScheduleRule, Lesson
from app.services.schedule import generate_lessons_for_student


@pytest.mark.asyncio
async def test_generate_lessons_ignores_inactive_rules(session):
    now_utc = datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc)  # Monday

    st = Student(full_name="A", timezone="Europe/Moscow")
    session.add(st)
    await session.flush()

    rule = ScheduleRule(
        student_id=st.id,
        weekday=now_utc.date().weekday(),
        time_local=time(10, 0),
        duration_min=60,
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 31),
        active=False,  # ключевое
    )
    session.add(rule)
    await session.commit()

    n = await generate_lessons_for_student(session, st.id, now_utc=now_utc, horizon_days=7)
    await session.commit()

    assert n == 0
    cnt = (await session.execute(
        select(func.count()).select_from(Lesson).where(Lesson.student_id == st.id)
    )).scalar_one()
    assert cnt == 0


@pytest.mark.asyncio
async def test_generate_lessons_returns_0_when_rule_start_after_horizon(session):
    now_utc = datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc)  # Monday
    horizon_days = 7

    st = Student(full_name="A", timezone="Europe/Moscow")
    session.add(st)
    await session.flush()

    rule = ScheduleRule(
        student_id=st.id,
        weekday=now_utc.date().weekday(),
        time_local=time(10, 0),
        duration_min=60,
        start_date=now_utc.date() + timedelta(days=horizon_days + 1),  # старт позже горизонта
        end_date=None,
        active=True,
    )
    session.add(rule)
    await session.commit()

    n = await generate_lessons_for_student(session, st.id, now_utc=now_utc, horizon_days=horizon_days)
    await session.commit()

    assert n == 0
    cnt = (await session.execute(
        select(func.count()).select_from(Lesson).where(Lesson.student_id == st.id)
    )).scalar_one()
    assert cnt == 0


@pytest.mark.asyncio
async def test_generate_lessons_returns_0_when_rule_ended_before_start_day(session):
    now_utc = datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc)  # Monday

    st = Student(full_name="A", timezone="Europe/Moscow")
    session.add(st)
    await session.flush()

    # правило закончилось до now_utc.date()
    rule = ScheduleRule(
        student_id=st.id,
        weekday=now_utc.date().weekday(),
        time_local=time(10, 0),
        duration_min=60,
        start_date=date(2025, 12, 1),
        end_date=date(2026, 1, 4),
        active=True,
    )
    session.add(rule)
    await session.commit()

    n = await generate_lessons_for_student(session, st.id, now_utc=now_utc, horizon_days=30)
    await session.commit()

    assert n == 0
    cnt = (await session.execute(
        select(func.count()).select_from(Lesson).where(Lesson.student_id == st.id)
    )).scalar_one()
    assert cnt == 0


@pytest.mark.asyncio
async def test_generate_lessons_conflict_two_rules_same_time_creates_one_lesson(session):
    """
    Два активных правила генерят один и тот же start_at (student_id, start_at одинаковые).
    Благодаря ON CONFLICT DO NOTHING в БД должен остаться ровно один Lesson.
    """
    now_utc = datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc)  # Monday
    d = now_utc.date()
    wd = d.weekday()

    st = Student(full_name="A", timezone="Europe/Moscow")
    session.add(st)
    await session.flush()

    r1 = ScheduleRule(
        student_id=st.id,
        weekday=wd,
        time_local=time(10, 0),
        duration_min=60,
        start_date=d,
        end_date=d,
        active=True,
    )
    r2 = ScheduleRule(
        student_id=st.id,
        weekday=wd,
        time_local=time(10, 0),  # то же время => тот же start_at
        duration_min=45,
        start_date=d,
        end_date=d,
        active=True,
    )
    session.add_all([r1, r2])
    await session.commit()

    # horizon_days=0 => только один день (d)
    _ = await generate_lessons_for_student(session, st.id, now_utc=now_utc, horizon_days=0)
    await session.commit()

    cnt = (await session.execute(
        select(func.count()).select_from(Lesson).where(Lesson.student_id == st.id)
    )).scalar_one()
    assert cnt == 1
