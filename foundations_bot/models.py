from __future__ import annotations

from datetime import datetime, date
from enum import Enum

from sqlalchemy import BigInteger, Date, DateTime, Enum as SqlEnum, Integer, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class EventType(str, Enum):
    SNIPE = "snipe"
    HOOP = "hoop"
    ADJUSTMENT = "adjustment"


class GuildSettings(Base):
    __tablename__ = "guild_settings"

    guild_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    sniping_channel_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    lship_role_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    genmem_role_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


class FamilyRole(Base):
    __tablename__ = "family_roles"

    guild_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    role_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    role_name: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


class EventRow(Base):
    __tablename__ = "event_rows"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    event_type: Mapped[EventType] = mapped_column(
        SqlEnum(EventType, native_enum=False, length=32), index=True, nullable=False
    )
    family_name: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    points: Mapped[int] = mapped_column(Integer, nullable=False)
    actor_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    attributed_user_id: Mapped[int | None] = mapped_column(BigInteger, index=True, nullable=True)
    target_user_id: Mapped[int | None] = mapped_column(BigInteger, index=True, nullable=True)
    source_message_id: Mapped[int | None] = mapped_column(BigInteger, index=True, nullable=True)
    source_channel_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    attachment_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    event_date: Mapped[date] = mapped_column(Date, index=True, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    voided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    voided_by_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    void_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
