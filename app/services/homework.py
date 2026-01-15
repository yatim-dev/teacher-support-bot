from sqlalchemy import select, func
from app.models import Homework

DEFAULT_HW_AVG_N = 10  # если у вас уже определён - оставьте

async def homework_avg_last_n(session, student_id: int, n: int = DEFAULT_HW_AVG_N) -> float | None:
    # берём последние N выставленных оценок по ДЗ этого ученика
    subq = (
        select(Homework.grade)
        .where(Homework.student_id == student_id, Homework.grade.is_not(None))
        .order_by(Homework.graded_at.desc())
        .limit(n)
        .subquery()
    )

    avg = (await session.execute(select(func.avg(subq.c.grade)))).scalar_one()
    return float(avg) if avg is not None else None
