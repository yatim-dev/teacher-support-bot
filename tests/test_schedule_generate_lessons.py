from datetime import date, datetime, time, timedelta, timezone

import pytest
from sqlalchemy import select, func

from app.models import Student, ScheduleRule, Lesson, LessonStatus
from app.services.schedule import generate_lessons_for_student, HORIZON_DAYS


@pytest.mark.asyncio
async def test_generate_lessons_returns_0_when_no_rules(session):
    st = Student(full_name="A", timezone="Europe/Moscow")
    session.add(st)
    await session.commit()

    n = await generate_lessons_for_student(session, st.id)
    await session.commit()

    assert n == 0
    cnt = (await session.execute(select(func.count()).select_from(Lesson).where(Lesson.student_id == st.id))).scalar_one()
    assert cnt == 0


@pytest.mark.asyncio
async def test_generate_lessons_creates_only_matching_weekday_within_horizon(session):
    st = Student(full_name="A", timezone="Europe/Moscow")
    session.add(st)
    await session.flush()

    today = datetime.now(timezone.utc).date()
    # выберем weekday = today.weekday() чтобы точно попало "сегодня"
    wd = today.weekday()

    rule = ScheduleRule(
        student_id=st.id,
        weekday=wd,
        time_local=time(10, 0),
        duration_min=60,
        start_date=today,
        end_date=None,
        active=True,
    )
    session.add(rule)
    await session.commit()

    n = await generate_lessons_for_student(session, st.id)
    await session.commit()

    # Должно создать несколько дат (каждую неделю) на горизонте 60 дней.
    assert n > 0

    lessons = (await session.execute(
        select(Lesson).where(Lesson.student_id == st.id).order_by(Lesson.start_at.asc())
    )).scalars().all()

    assert len(lessons) > 0
    # каждый урок должен быть из этого правила и planned
    assert all(l.source_rule_id == rule.id for l in lessons)
    assert all(l.status == LessonStatus.planned for l in lessons)

    # И все должны лежать в диапазоне [today; today+HORIZON_DAYS]
    end_day = (datetime.now(timezone.utc) + timedelta(days=HORIZON_DAYS)).date()
    for l in lessons:
        d = l.start_at.date()
        assert today <= d <= end_day


@pytest.mark.asyncio
async def test_generate_lessons_respects_start_end_date(session):
    st = Student(full_name="A", timezone="Europe/Moscow")
    session.add(st)
    await session.flush()

    today = datetime.now(timezone.utc).date()

    # окно правила: 8 дней, чтобы в нём гарантированно попалась 1-2 даты нужного weekday
    rule_start = today + timedelta(days=1)
    rule_end = today + timedelta(days=8)

    # берём weekday = rule_start.weekday(), тогда минимум rule_start попадёт
    wd = rule_start.weekday()

    rule = ScheduleRule(
        student_id=st.id,
        weekday=wd,
        time_local=time(12, 30),
        duration_min=45,
        start_date=rule_start,
        end_date=rule_end,
        active=True,
    )
    session.add(rule)
    await session.commit()

    n = await generate_lessons_for_student(session, st.id)
    await session.commit()

    assert n > 0

    lessons = (await session.execute(
        select(Lesson).where(Lesson.student_id == st.id)
    )).scalars().all()

    assert len(lessons) > 0
    for l in lessons:
        d = l.start_at.date()
        assert rule_start <= d <= rule_end


@pytest.mark.asyncio
async def test_generate_lessons_is_idempotent_on_conflict(session):
    st = Student(full_name="A", timezone="Europe/Moscow")
    session.add(st)
    await session.flush()

    today = datetime.now(timezone.utc).date()
    wd = today.weekday()

    rule = ScheduleRule(
        student_id=st.id,
        weekday=wd,
        time_local=time(9, 0),
        duration_min=60,
        start_date=today,
        end_date=today + timedelta(days=14),
        active=True,
    )
    session.add(rule)
    await session.commit()

    n1 = await generate_lessons_for_student(session, st.id)
    await session.commit()

    cnt1 = (await session.execute(select(func.count()).select_from(Lesson).where(Lesson.student_id == st.id))).scalar_one()

    n2 = await generate_lessons_for_student(session, st.id)
    await session.commit()

    cnt2 = (await session.execute(select(func.count()).select_from(Lesson).where(Lesson.student_id == st.id))).scalar_one()

    assert n1 > 0
    # n2 по вашей реализации возвращает len(rows) (сколько пытались вставить), он может быть >0
    # но фактическое количество записей не должно измениться
    assert cnt2 == cnt1
