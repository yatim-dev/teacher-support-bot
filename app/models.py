from __future__ import annotations

import enum
from datetime import datetime, date, time
from typing import Optional

from sqlalchemy import (
    BigInteger, Boolean, Date, DateTime, Enum, ForeignKey,
    Integer, Numeric, String, Text, Time, UniqueConstraint, func
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Role(str, enum.Enum):
    teacher = "teacher"
    student = "student"
    parent = "parent"


class BillingMode(str, enum.Enum):
    subscription = "subscription"  # пакет N уроков
    single = "single"              # разово (оплата после "Проведён")


class LessonStatus(str, enum.Enum):
    planned = "planned"
    done = "done"
    canceled = "canceled"


class ChargeStatus(str, enum.Enum):
    pending = "pending"
    paid = "paid"
    canceled = "canceled"


class NotificationStatus(str, enum.Enum):
    pending = "pending"
    sent = "sent"
    failed = "failed"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tg_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    role: Mapped[Role] = mapped_column(Enum(Role), index=True)

    name: Mapped[Optional[str]] = mapped_column(String(255))
    timezone: Mapped[Optional[str]] = mapped_column(String(64))  # IANA timezone, напр. Europe/Moscow

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Student(Base):
    __tablename__ = "students"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        unique=True
    )

    full_name: Mapped[str] = mapped_column(String(255))
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Moscow")

    billing_mode: Mapped[BillingMode] = mapped_column(Enum(BillingMode), default=BillingMode.subscription)
    price_per_lesson: Mapped[Optional[float]] = mapped_column(Numeric(10, 2))

    user: Mapped[Optional[User]] = relationship()


class Parent(Base):
    __tablename__ = "parents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)

    full_name: Mapped[str] = mapped_column(String(255))
    user: Mapped[User] = relationship()


class ParentStudent(Base):
    __tablename__ = "parent_student"
    __table_args__ = (UniqueConstraint("parent_id", "student_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    parent_id: Mapped[int] = mapped_column(ForeignKey("parents.id", ondelete="CASCADE"))
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"))


class RegistrationKey(Base):
    __tablename__ = "registration_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True, index=True)

    role_target: Mapped[Role] = mapped_column(Enum(Role))  # student или parent
    student_id: Mapped[Optional[int]] = mapped_column(ForeignKey("students.id", ondelete="SET NULL"))

    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    max_uses: Mapped[int] = mapped_column(Integer, default=1)
    used_count: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ScheduleRule(Base):
    __tablename__ = "schedule_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), index=True)

    weekday: Mapped[int] = mapped_column(Integer)  # 0=Mon ... 6=Sun
    time_local: Mapped[time] = mapped_column(Time)
    duration_min: Mapped[int] = mapped_column(Integer, default=60)

    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[Optional[date]] = mapped_column(Date)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Lesson(Base):
    __tablename__ = "lessons"
    __table_args__ = (UniqueConstraint("student_id", "start_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), index=True)

    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)  # хранить UTC aware
    duration_min: Mapped[int] = mapped_column(Integer, default=60)
    status: Mapped[LessonStatus] = mapped_column(Enum(LessonStatus), default=LessonStatus.planned)

    source_rule_id: Mapped[Optional[int]] = mapped_column(ForeignKey("schedule_rules.id", ondelete="SET NULL"))
    topic: Mapped[Optional[str]] = mapped_column(String(255))

    done_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class StudentBalance(Base):
    __tablename__ = "student_balance"

    student_id: Mapped[int] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), primary_key=True)
    lessons_left: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class LessonCharge(Base):
    __tablename__ = "lesson_charges"
    __table_args__ = (UniqueConstraint("lesson_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lesson_id: Mapped[int] = mapped_column(ForeignKey("lessons.id", ondelete="CASCADE"))
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), index=True)

    amount: Mapped[float] = mapped_column(Numeric(10, 2))
    status: Mapped[ChargeStatus] = mapped_column(Enum(ChargeStatus), default=ChargeStatus.pending)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (UniqueConstraint("user_id", "type", "entity_id", "send_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)

    type: Mapped[str] = mapped_column(String(64))      # lesson_24h, lesson_1h
    entity_id: Mapped[int] = mapped_column(Integer)    # lesson_id
    send_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    payload: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[NotificationStatus] = mapped_column(Enum(NotificationStatus), default=NotificationStatus.pending)
    last_error: Mapped[Optional[str]] = mapped_column(Text)
