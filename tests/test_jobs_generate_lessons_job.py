from datetime import datetime, date, time, timezone

import pytest
from sqlalchemy import select, func

from app.models import Student, ScheduleRule, Lesson, LessonStatus


def _freeze_datetime(monkeypatch, module, fixed: datetime):
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            # модуль вызывает datetime.now(timezone.utc)
            return fixed if tz is not None else fixed.replace(tzinfo=None)

    monkeypatch.setattr(module, "datetime", FixedDateTime)


@pytest.mark.asyncio
async def test_generate_lessons_job_creates_lessons_for_active_rules(monkeypatch, sessionmaker, session):
    # поправьте импорт под ваш реальный путь
    import app.jobs_lessons as jobs

    monkeypatch.setattr(jobs.db, "SessionMaker", sessionmaker)

    fixed_now = datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc)  # Monday
    _freeze_datetime(monkeypatch, jobs, fixed_now)

    st = Student(full_name="A", timezone="Europe/Moscow")
    session.add(st)
    await session.flush()

    rule = ScheduleRule(
        student_id=st.id,
        weekday=fixed_now.date().weekday(),
        time_local=time(10, 0),
        duration_min=60,
        start_date=fixed_now.date(),
        end_date=fixed_now.date(),  # 1 день => 1 урок
        active=True,
    )
    session.add(rule)
    await session.commit()

    await jobs.generate_lessons_job()

    cnt = (await session.execute(
        select(func.count()).select_from(Lesson).where(Lesson.student_id == st.id)
    )).scalar_one()
    assert cnt == 1

    lesson = (await session.execute(select(Lesson).where(Lesson.student_id == st.id))).scalar_one()
    assert lesson.status == LessonStatus.planned
    assert lesson.source_rule_id == rule.id


@pytest.mark.asyncio
async def test_generate_lessons_job_is_idempotent(monkeypatch, sessionmaker, session):
    import app.jobs_lessons as jobs

    monkeypatch.setattr(jobs.db, "SessionMaker", sessionmaker)

    fixed_now = datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc)
    _freeze_datetime(monkeypatch, jobs, fixed_now)

    st = Student(full_name="A", timezone="Europe/Moscow")
    session.add(st)
    await session.flush()

    rule = ScheduleRule(
        student_id=st.id,
        weekday=fixed_now.date().weekday(),
        time_local=time(10, 0),
        duration_min=60,
        start_date=fixed_now.date(),
        end_date=fixed_now.date(),
        active=True,
    )
    session.add(rule)
    await session.commit()

    await jobs.generate_lessons_job()
    await jobs.generate_lessons_job()  # второй раз

    cnt = (await session.execute(
        select(func.count()).select_from(Lesson).where(Lesson.student_id == st.id)
    )).scalar_one()
    assert cnt == 1


@pytest.mark.asyncio
async def test_generate_lessons_job_ignores_inactive_rules(monkeypatch, sessionmaker, session):
    import app.jobs_lessons as jobs

    monkeypatch.setattr(jobs.db, "SessionMaker", sessionmaker)

    fixed_now = datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc)
    _freeze_datetime(monkeypatch, jobs, fixed_now)

    st = Student(full_name="A", timezone="Europe/Moscow")
    session.add(st)
    await session.flush()

    rule = ScheduleRule(
        student_id=st.id,
        weekday=fixed_now.date().weekday(),
        time_local=time(10, 0),
        duration_min=60,
        start_date=fixed_now.date(),
        end_date=fixed_now.date(),
        active=False,  # ключевое
    )
    session.add(rule)
    await session.commit()

    await jobs.generate_lessons_job()

    cnt = (await session.execute(
        select(func.count()).select_from(Lesson).where(Lesson.student_id == st.id)
    )).scalar_one()
    assert cnt == 0
