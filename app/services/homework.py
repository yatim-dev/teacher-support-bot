from sqlalchemy import select, func
from ..models import Homework, Lesson

DEFAULT_HW_AVG_N = 10

async def homework_avg_last_n(session, student_id: int, n: int = DEFAULT_HW_AVG_N) -> float | None:
    subq = (
        select(Homework.grade)
        .join(Lesson, Lesson.id == Homework.lesson_id)
        .where(Lesson.student_id == student_id, Homework.grade.is_not(None))
        .order_by(Homework.graded_at.desc())
        .limit(n)
        .subquery()
    )
    avg_val = (await session.execute(select(func.avg(subq.c.grade)))).scalar_one()
    return float(avg_val) if avg_val is not None else None
