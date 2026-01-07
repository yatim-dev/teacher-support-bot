from datetime import datetime, timedelta, timezone, time, date

import pytest
from sqlalchemy import select, func

from app.models import Student, ScheduleRule, Lesson, LessonStatus


def _freeze_datetime(monkeypatch, module, fixed: datetime):
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed if tz is not None else fixed.replace(tzinfo=None)

    monkeypatch.setattr(module, "datetime", FixedDateTime)


@pytest.mark.asyncio
async def test_generate_lessons_job_creates_for_all_active_rules(monkeypatch, sessionmaker, session):
    import app.jobs_lessons as jobs  # <-- поправьте путь при необходимости

    monkeypatch.setattr(jobs.db, "SessionMaker", sessionmaker)
    fixed_now = datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc)  # Monday
    _freeze_datetime(monkeypatch, jobs, fixed_now)

    s1 = Student(full_name="S1", timezone="Europe/Moscow")
    s2 = Student(full_name="S2", timezone="Europe/Moscow")
    session.add_all([s1, s2])
    await session.flush()

    r1 = ScheduleRule(
        student_id=s1.id, weekday=fixed_now.date().weekday(),
        time_local=time(10, 0), duration_min=60,
        start_date=fixed_now.date(), end_date=fixed_now.date(),
        active=True
    )
    r2 = ScheduleRule(
        student_id=s2.id, weekday=fixed_now.date().weekday(),
        time_local=time(11, 0), duration_min=45,
        start_date=fixed_now.date(), end_date=fixed_now.date(),
        active=True
    )
    session.add_all([r1, r2])
    await session.commit()

    await jobs.generate_lessons_job()

    async with sessionmaker() as s2db:
        cnt = (await s2db.execute(select(func.count()).select_from(Lesson))).scalar_one()
        assert cnt == 2


@pytest.mark.asyncio
async def test_generate_lessons_job_ignores_inactive_rules(monkeypatch, sessionmaker, session):
    import app.jobs_lessons as jobs  # <-- поправьте путь

    monkeypatch.setattr(jobs.db, "SessionMaker", sessionmaker)
    fixed_now = datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc)
    _freeze_datetime(monkeypatch, jobs, fixed_now)

    st = Student(full_name="S", timezone="Europe/Moscow")
    session.add(st)
    await session.flush()

    rule = ScheduleRule(
        student_id=st.id, weekday=fixed_now.date().weekday(),
        time_local=time(10, 0), duration_min=60,
        start_date=fixed_now.date(), end_date=fixed_now.date(),
        active=False
    )
    session.add(rule)
    await session.commit()

    await jobs.generate_lessons_job()

    async with sessionmaker() as s2db:
        cnt = (await s2db.execute(select(func.count()).select_from(Lesson))).scalar_one()
        assert cnt == 0


@pytest.mark.asyncio
async def test_generate_lessons_job_is_idempotent(monkeypatch, sessionmaker, session):
    import app.jobs_lessons as jobs  # <-- поправьте путь

    monkeypatch.setattr(jobs.db, "SessionMaker", sessionmaker)
    fixed_now = datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc)
    _freeze_datetime(monkeypatch, jobs, fixed_now)

    st = Student(full_name="S", timezone="Europe/Moscow")
    session.add(st)
    await session.flush()

    rule = ScheduleRule(
        student_id=st.id, weekday=fixed_now.date().weekday(),
        time_local=time(10, 0), duration_min=60,
        start_date=fixed_now.date(), end_date=fixed_now.date(),
        active=True
    )
    session.add(rule)
    await session.commit()

    await jobs.generate_lessons_job()
    await jobs.generate_lessons_job()

    async with sessionmaker() as s2db:
        cnt = (await s2db.execute(select(func.count()).select_from(Lesson).where(Lesson.student_id == st.id))).scalar_one()
        assert cnt == 1


@pytest.mark.asyncio
async def test_generate_lessons_job_respects_end_date(monkeypatch, sessionmaker, session):
    import app.jobs_lessons as jobs  # <-- поправьте путь

    monkeypatch.setattr(jobs.db, "SessionMaker", sessionmaker)
    fixed_now = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)  # Thursday
    _freeze_datetime(monkeypatch, jobs, fixed_now)

    st = Student(full_name="S", timezone="Europe/Moscow")
    session.add(st)
    await session.flush()

    # Правило активно, но полностью "в прошлом" относительно start_day = now.date()
    rule = ScheduleRule(
        student_id=st.id,
        weekday=fixed_now.date().weekday(),
        time_local=time(10, 0),
        duration_min=60,
        start_date=date(2025, 12, 1),
        end_date=date(2025, 12, 31),
        active=True
    )
    session.add(rule)
    await session.commit()

    await jobs.generate_lessons_job()

    async with sessionmaker() as s2db:
        cnt = (await s2db.execute(select(func.count()).select_from(Lesson).where(Lesson.student_id == st.id))).scalar_one()
        assert cnt == 0


@pytest.mark.asyncio
async def test_generate_lessons_job_respects_horizon(monkeypatch, sessionmaker, session):
    import app.jobs_lessons as jobs  # <-- поправьте путь

    monkeypatch.setattr(jobs.db, "SessionMaker", sessionmaker)
    fixed_now = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    _freeze_datetime(monkeypatch, jobs, fixed_now)

    st = Student(full_name="S", timezone="Europe/Moscow")
    session.add(st)
    await session.flush()

    # Внутри job HORIZON_DAYS=60. Сделаем правило, стартующее позже горизонта -> уроков быть не должно
    rule = ScheduleRule(
        student_id=st.id,
        weekday=(fixed_now.date() + timedelta(days=70)).weekday(),
        time_local=time(10, 0),
        duration_min=60,
        start_date=fixed_now.date() + timedelta(days=70),
        end_date=None,
        active=True
    )
    session.add(rule)
    await session.commit()

    await jobs.generate_lessons_job()

    async with sessionmaker() as s2db:
        cnt = (await s2db.execute(select(func.count()).select_from(Lesson).where(Lesson.student_id == st.id))).scalar_one()
        assert cnt == 0


@pytest.mark.asyncio
async def test_generate_lessons_job_conflict_two_rules_same_student_same_time_creates_one(monkeypatch, sessionmaker, session):
    import app.jobs_lessons as jobs  # <-- поправьте путь

    monkeypatch.setattr(jobs.db, "SessionMaker", sessionmaker)
    fixed_now = datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc)  # Monday
    _freeze_datetime(monkeypatch, jobs, fixed_now)

    st = Student(full_name="S", timezone="Europe/Moscow")
    session.add(st)
    await session.flush()

    d = fixed_now.date()
    wd = d.weekday()

    r1 = ScheduleRule(
        student_id=st.id, weekday=wd,
        time_local=time(10, 0), duration_min=60,
        start_date=d, end_date=d,
        active=True
    )
    r2 = ScheduleRule(
        student_id=st.id, weekday=wd,
        time_local=time(10, 0), duration_min=45,
        start_date=d, end_date=d,
        active=True
    )
    session.add_all([r1, r2])
    await session.commit()

    await jobs.generate_lessons_job()

    async with sessionmaker() as s2db:
        cnt = (await s2db.execute(select(func.count()).select_from(Lesson).where(Lesson.student_id == st.id))).scalar_one()
        assert cnt == 1
        lesson = (await s2db.execute(select(Lesson).where(Lesson.student_id == st.id))).scalar_one()
        assert lesson.status == LessonStatus.planned
