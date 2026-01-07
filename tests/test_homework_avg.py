from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models import Student, Lesson, Homework
from app.services.homework import homework_avg_last_n


@pytest.mark.asyncio
async def test_homework_avg_last_n_none_when_no_grades(session):
    st = Student(full_name="A", timezone="Europe/Moscow")
    session.add(st)
    await session.commit()

    avg = await homework_avg_last_n(session, st.id, n=10)
    assert avg is None


@pytest.mark.asyncio
async def test_homework_avg_last_n_ignores_ungraded(session):
    st = Student(full_name="A", timezone="Europe/Moscow")
    session.add(st)
    await session.commit()

    now = datetime.now(timezone.utc)

    # 2 урока, но оценки нет
    l1 = Lesson(student_id=st.id, start_at=now + timedelta(days=1), duration_min=60)
    l2 = Lesson(student_id=st.id, start_at=now + timedelta(days=2), duration_min=60)
    session.add_all([l1, l2])
    await session.commit()

    hw1 = Homework(lesson_id=l1.id, title="t1", description="d1", grade=None, graded_at=None)
    hw2 = Homework(lesson_id=l2.id, title="t2", description="d2", grade=None, graded_at=None)
    session.add_all([hw1, hw2])
    await session.commit()

    avg = await homework_avg_last_n(session, st.id, n=10)
    assert avg is None


@pytest.mark.asyncio
async def test_homework_avg_last_n_takes_last_n_by_graded_at(session):
    st = Student(full_name="A", timezone="Europe/Moscow")
    session.add(st)
    await session.commit()

    base = datetime(2026, 1, 1, tzinfo=timezone.utc)

    # сделаем 12 домашних работ с оценками 1..12 (по graded_at возрастают)
    for i in range(12):
        lesson = Lesson(student_id=st.id, start_at=base + timedelta(days=i), duration_min=60)
        session.add(lesson)
        await session.flush()  # чтобы появился lesson.id без commit

        hw = Homework(
            lesson_id=lesson.id,
            title=f"hw{i}",
            description="desc",
            grade=i + 1,
            graded_at=base + timedelta(hours=i),  # важный порядок
        )
        session.add(hw)

    await session.commit()

    # последние 10 оценок: 3..12 => среднее = (3+...+12)/10 = 7.5
    avg = await homework_avg_last_n(session, st.id, n=10)
    assert avg == pytest.approx(7.5, rel=1e-9)
