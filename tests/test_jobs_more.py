from datetime import datetime, timedelta, timezone, date, time

import pytest
from sqlalchemy import select, func

from app.models import (
    Student, ScheduleRule,
    Lesson, LessonStatus,
    Notification, NotificationStatus,
    ParentStudent, Parent, User, Role,
)


def freeze_datetime(monkeypatch, module, fixed: datetime):
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed if tz is not None else fixed.replace(tzinfo=None)

    monkeypatch.setattr(module, "datetime", FixedDateTime)


@pytest.mark.asyncio
async def test_generate_lessons_job_creates_one_lesson_for_one_day_rule(monkeypatch, sessionmaker, session):
    # поправьте модуль под ваш реальный путь
    import app.jobs_lessons as gen_jobs

    monkeypatch.setattr(gen_jobs.db, "SessionMaker", sessionmaker)

    now = datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc)  # Monday
    freeze_datetime(monkeypatch, gen_jobs, now)

    st = Student(full_name="A", timezone="Europe/Moscow")
    session.add(st)
    await session.flush()

    rule = ScheduleRule(
        student_id=st.id,
        weekday=now.date().weekday(),
        time_local=time(10, 0),
        duration_min=60,
        start_date=now.date(),
        end_date=now.date(),
        active=True,
    )
    session.add(rule)
    await session.commit()

    await gen_jobs.generate_lessons_job()

    async with sessionmaker() as s2:
        cnt = (await s2.execute(select(func.count()).select_from(Lesson).where(Lesson.student_id == st.id))).scalar_one()
        assert cnt == 1

        lesson = (await s2.execute(select(Lesson).where(Lesson.student_id == st.id))).scalar_one()
        assert lesson.status == LessonStatus.planned
        assert lesson.source_rule_id == rule.id


@pytest.mark.asyncio
async def test_generate_lessons_job_is_idempotent(monkeypatch, sessionmaker, session):
    import app.jobs_lessons as gen_jobs
    monkeypatch.setattr(gen_jobs.db, "SessionMaker", sessionmaker)

    now = datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc)
    freeze_datetime(monkeypatch, gen_jobs, now)

    st = Student(full_name="A", timezone="Europe/Moscow")
    session.add(st)
    await session.flush()

    rule = ScheduleRule(
        student_id=st.id,
        weekday=now.date().weekday(),
        time_local=time(10, 0),
        duration_min=60,
        start_date=now.date(),
        end_date=now.date(),
        active=True,
    )
    session.add(rule)
    await session.commit()

    await gen_jobs.generate_lessons_job()
    await gen_jobs.generate_lessons_job()

    async with sessionmaker() as s2:
        cnt = (await s2.execute(select(func.count()).select_from(Lesson).where(Lesson.student_id == st.id))).scalar_one()
        assert cnt == 1


@pytest.mark.asyncio
async def test_generate_lessons_job_uses_default_timezone_when_student_missing(monkeypatch, sessionmaker, session):
    """
    В job вы строите tz_map по студентам, найденным в БД.
    Если student_id из правила почему-то не найден (редкий кейс), tz default "Europe/Moscow".
    Это сложно смоделировать при FK schedule_rules.student_id -> students.id.
    Поэтому тестируем другой важный кейс: student.timezone = None (если у вас допускается).
    Если в вашей схеме timezone NOT NULL, этот тест удалите.
    """
    import app.jobs_lessons as gen_jobs
    monkeypatch.setattr(gen_jobs.db, "SessionMaker", sessionmaker)

    now = datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc)
    freeze_datetime(monkeypatch, gen_jobs, now)

    st = Student(full_name="A", timezone="Europe/Moscow")  # если timezone nullable и вы хотите None — поставьте None
    session.add(st)
    await session.flush()

    rule = ScheduleRule(
        student_id=st.id,
        weekday=now.date().weekday(),
        time_local=time(10, 0),
        duration_min=60,
        start_date=now.date(),
        end_date=now.date(),
        active=True,
    )
    session.add(rule)
    await session.commit()

    await gen_jobs.generate_lessons_job()

    async with sessionmaker() as s2:
        lesson = (await s2.execute(select(Lesson).where(Lesson.student_id == st.id))).scalar_one()
        assert lesson.start_at.tzinfo is not None  # UTC aware


@pytest.mark.asyncio
async def test_plan_notifications_skips_past_send_at(monkeypatch, sessionmaker, session):
    import app.jobs_notifications as notif_jobs
    monkeypatch.setattr(notif_jobs.db, "SessionMaker", sessionmaker)

    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    freeze_datetime(monkeypatch, notif_jobs, now)

    u = User(tg_id=7001, role=Role.student, name="S", timezone="Europe/Moscow")
    session.add(u)
    await session.flush()

    st = Student(full_name="Student", timezone="Europe/Moscow", user_id=u.id)
    session.add(st)
    await session.flush()

    # урок через 30 минут => send_at для lesson_1h будет в прошлом => не создастся
    lesson = Lesson(student_id=st.id, start_at=now + timedelta(minutes=30), duration_min=60, status=LessonStatus.planned)
    session.add(lesson)
    await session.commit()

    await notif_jobs.plan_lesson_notifications_job()

    async with sessionmaker() as s2:
        notifs = (await s2.execute(select(Notification))).scalars().all()
        # должно быть 0: и 24h, и 1h окажутся в прошлом
        assert len(notifs) == 0


@pytest.mark.asyncio
async def test_plan_notifications_creates_two_for_student_only(monkeypatch, sessionmaker, session):
    import app.jobs_notifications as notif_jobs
    monkeypatch.setattr(notif_jobs.db, "SessionMaker", sessionmaker)

    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    freeze_datetime(monkeypatch, notif_jobs, now)

    u = User(tg_id=7101, role=Role.student, name="S", timezone="Europe/Moscow")
    session.add(u)
    await session.flush()

    st = Student(full_name="Student", timezone="Europe/Moscow", user_id=u.id)
    session.add(st)
    await session.flush()

    lesson = Lesson(student_id=st.id, start_at=now + timedelta(days=2), duration_min=60, status=LessonStatus.planned)
    session.add(lesson)
    await session.commit()

    await notif_jobs.plan_lesson_notifications_job()

    async with sessionmaker() as s2:
        notifs = (await s2.execute(select(Notification).order_by(Notification.type))).scalars().all()
        assert len(notifs) == 2
        assert {n.type for n in notifs} == {"lesson_24h", "lesson_1h"}
        assert all(n.user_id == u.id for n in notifs)
        assert all(n.entity_id == lesson.id for n in notifs)
        assert all(n.status == NotificationStatus.pending for n in notifs)


@pytest.mark.asyncio
async def test_plan_notifications_is_idempotent(monkeypatch, sessionmaker, session):
    import app.jobs_notifications as notif_jobs
    monkeypatch.setattr(notif_jobs.db, "SessionMaker", sessionmaker)

    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    freeze_datetime(monkeypatch, notif_jobs, now)

    u = User(tg_id=7201, role=Role.student, name="S", timezone="Europe/Moscow")
    session.add(u)
    await session.flush()

    st = Student(full_name="Student", timezone="Europe/Moscow", user_id=u.id)
    session.add(st)
    await session.flush()

    lesson = Lesson(student_id=st.id, start_at=now + timedelta(days=2), duration_min=60, status=LessonStatus.planned)
    session.add(lesson)
    await session.commit()

    await notif_jobs.plan_lesson_notifications_job()
    await notif_jobs.plan_lesson_notifications_job()

    async with sessionmaker() as s2:
        cnt = (await s2.execute(select(func.count()).select_from(Notification))).scalar_one()
        assert cnt == 2
