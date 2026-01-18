from datetime import datetime, timedelta, timezone

import pytest

from app.models import Student, Homework
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

    hw1 = Homework(student_id=st.id, title="t1", description="d1", grade=None, graded_at=None, due_at=None, student_done_at=None)
    hw2 = Homework(student_id=st.id, title="t2", description="d2", grade=None, graded_at=None, due_at=None, student_done_at=None)
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

    # сделаем 12 домашних работ с оценками 1..12 (graded_at возрастают)
    for i in range(12):
        hw = Homework(
            student_id=st.id,
            title=f"hw{i}",
            description="desc",
            grade=i + 1,
            graded_at=base + timedelta(hours=i),  # важный порядок
            due_at=None,
            student_done_at=None,
        )
        session.add(hw)

    await session.commit()

    # последние 10 оценок: 3..12 => среднее = (3+...+12)/10 = 7.5
    avg = await homework_avg_last_n(session, st.id, n=10)
    assert avg == pytest.approx(7.5, rel=1e-9)
