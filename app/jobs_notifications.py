from datetime import datetime, timedelta, timezone
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert

from . import db
from .models import (
    Lesson, LessonStatus, Student, ParentStudent, Parent, User,
    Notification, NotificationStatus
)
from .utils_time import fmt_dt_for_tz

HORIZON_DAYS = 7


async def plan_lesson_notifications_job():
    async with db.SessionMaker() as session:
        now = datetime.now(timezone.utc)
        horizon = now + timedelta(days=HORIZON_DAYS)

        lessons = (await session.execute(
            select(Lesson).where(
                Lesson.status == LessonStatus.planned,
                Lesson.start_at > now,
                Lesson.start_at <= horizon
            )
        )).scalars().all()

        if not lessons:
            return

        students = (await session.execute(
            select(Student).where(Student.id.in_([l.student_id for l in lessons]))
        )).scalars().all()
        st_map = {s.id: s for s in students}

        rows = []
        for lesson in lessons:
            st = st_map[lesson.student_id]
            targets_user_ids: list[int] = []

            # ученик (если зарегистрировался)
            if st.user_id:
                targets_user_ids.append(st.user_id)

            # родители
            parent_ids = (await session.execute(
                select(ParentStudent.parent_id).where(ParentStudent.student_id == st.id)
            )).scalars().all()
            if parent_ids:
                parent_user_ids = (await session.execute(
                    select(Parent.user_id).where(Parent.id.in_(parent_ids))
                )).scalars().all()
                targets_user_ids.extend(parent_user_ids)

            for uid in set(targets_user_ids):
                for kind, delta in (("lesson_24h", timedelta(hours=24)), ("lesson_1h", timedelta(hours=1))):
                    send_at = lesson.start_at - delta
                    if send_at <= now:
                        continue
                    rows.append({
                        "user_id": uid,
                        "type": kind,
                        "entity_id": lesson.id,
                        "send_at": send_at,
                        "payload": None,
                        "status": NotificationStatus.pending
                    })

        if not rows:
            return

        stmt = insert(Notification).values(rows)
        stmt = stmt.on_conflict_do_nothing(index_elements=["user_id", "type", "entity_id", "send_at"])
        await session.execute(stmt)
        await session.commit()


async def send_notifications_job(bot, batch_size: int = 50):
    async with db.SessionMaker() as session:
        now = datetime.now(timezone.utc)

        notifs = (await session.execute(
            select(Notification)
            .where(Notification.status == NotificationStatus.pending, Notification.send_at <= now)
            .order_by(Notification.send_at)
            .limit(batch_size)
        )).scalars().all()

        if not notifs:
            return

        user_ids = list({n.user_id for n in notifs})
        users = (await session.execute(select(User).where(User.id.in_(user_ids)))).scalars().all()
        u_map = {u.id: u for u in users}

        for n in notifs:
            u = u_map.get(n.user_id)
            if not u:
                # пользователь мог быть удалён (например, удалили ученика/родителя)
                await session.execute(
                    update(Notification).where(Notification.id == n.id)
                    .values(status=NotificationStatus.failed, last_error="User not found")
                )
                await session.commit()
                continue

            try:
                if n.type in ("lesson_24h", "lesson_1h"):
                    lesson = (await session.execute(
                        select(Lesson).where(Lesson.id == n.entity_id)
                    )).scalar_one()

                    student = (await session.execute(
                        select(Student).where(Student.id == lesson.student_id)
                    )).scalar_one()

                    when = fmt_dt_for_tz(lesson.start_at, u.timezone)
                    tzname = u.timezone or "Europe/Moscow"
                    msg = (
                        "Напоминание: урок скоро.\n"
                        f"Ученик: {student.full_name}\n"
                        f"Время: {when} ({tzname})"
                    )
                    await bot.send_message(u.tg_id, msg)

                elif n.type == "hw_graded":
                    # payload формируем при выставлении оценки (ученик+родители),
                    # поэтому тут просто отправляем готовый текст
                    await bot.send_message(u.tg_id, n.payload or "Выставлена оценка за домашнее задание.")

                else:
                    # неизвестный тип уведомления
                    await session.execute(
                        update(Notification).where(Notification.id == n.id)
                        .values(status=NotificationStatus.failed, last_error=f"Unknown notification type: {n.type}")
                    )
                    await session.commit()
                    continue

                await session.execute(
                    update(Notification).where(Notification.id == n.id)
                    .values(status=NotificationStatus.sent, last_error=None)
                )
                await session.commit()

            except Exception as e:
                await session.execute(
                    update(Notification).where(Notification.id == n.id)
                    .values(status=NotificationStatus.failed, last_error=str(e)[:2000])
                )
                await session.commit()
