from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import create_engine, desc, func, select
from sqlalchemy.orm import Session, sessionmaker

from foundations_bot.models import Base, EventRow, EventType, FamilyRole, GuildSettings


@dataclass(frozen=True)
class GuildRuntimeSettings:
    guild_id: int
    sniping_channel_id: int | None
    lship_role_id: int | None
    genmem_role_id: int | None


@dataclass(frozen=True)
class MessageRecording:
    recorded_target_ids: list[int]
    duplicate_target_ids: list[int]
    hoop_recorded: bool
    total_points_for_message: int


@dataclass(frozen=True)
class FamilyScore:
    role_id: int | None
    family_name: str
    points: int


@dataclass(frozen=True)
class PersonScore:
    user_id: int
    points: int


@dataclass(frozen=True)
class ScoreboardSnapshot:
    families: list[FamilyScore]
    people: list[PersonScore]


@dataclass(frozen=True)
class FamilyGraphSeries:
    family_name: str
    daily_points: dict[date, int]


@dataclass(frozen=True)
class VoidedSnipe:
    row_id: int
    event_type: EventType
    points: int
    actor_user_id: int
    target_user_id: int | None
    family_name: str
    reason: str | None
    source_message_id: int | None
    source_channel_id: int | None
    created_at: datetime


@dataclass(frozen=True)
class RecentEvent:
    row_id: int
    event_type: EventType
    family_name: str
    points: int
    actor_user_id: int | None
    target_user_id: int | None
    reason: str | None
    created_at: datetime
    voided_at: datetime | None


@dataclass(frozen=True)
class EventReference:
    row_id: int
    event_type: EventType
    family_name: str
    points: int
    actor_user_id: int | None
    target_user_id: int | None
    reason: str | None
    source_message_id: int | None
    source_channel_id: int | None
    created_at: datetime
    voided_at: datetime | None


class FoundationsStore:
    def __init__(self, database_url: str) -> None:
        self.engine = create_engine(
            database_url,
            future=True,
            pool_pre_ping=True,
            pool_recycle=1800,
            pool_size=5,
            max_overflow=2,
        )
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)

    def initialize(self) -> None:
        Base.metadata.create_all(self.engine)

    def close(self) -> None:
        self.engine.dispose()

    def _get_or_create_settings(self, session: Session, guild_id: int) -> GuildSettings:
        settings = session.get(GuildSettings, guild_id)
        if settings is None:
            settings = GuildSettings(guild_id=guild_id)
            session.add(settings)
            session.flush()
        return settings

    @staticmethod
    def _touch(model: GuildSettings | FamilyRole) -> None:
        model.updated_at = datetime.utcnow()

    def get_guild_settings(self, guild_id: int) -> GuildRuntimeSettings:
        with self.session_factory.begin() as session:
            settings = self._get_or_create_settings(session, guild_id)
            return GuildRuntimeSettings(
                guild_id=settings.guild_id,
                sniping_channel_id=settings.sniping_channel_id,
                lship_role_id=settings.lship_role_id,
                genmem_role_id=settings.genmem_role_id,
            )

    def set_sniping_channel(self, guild_id: int, channel_id: int) -> None:
        with self.session_factory.begin() as session:
            settings = self._get_or_create_settings(session, guild_id)
            settings.sniping_channel_id = channel_id
            self._touch(settings)

    def set_lship_role(self, guild_id: int, role_id: int) -> None:
        with self.session_factory.begin() as session:
            settings = self._get_or_create_settings(session, guild_id)
            settings.lship_role_id = role_id
            self._touch(settings)

    def set_genmem_role(self, guild_id: int, role_id: int) -> None:
        with self.session_factory.begin() as session:
            settings = self._get_or_create_settings(session, guild_id)
            settings.genmem_role_id = role_id
            self._touch(settings)

    def register_family_role(self, guild_id: int, role_id: int, role_name: str) -> None:
        with self.session_factory.begin() as session:
            family_role = session.execute(
                select(FamilyRole).where(
                    FamilyRole.guild_id == guild_id,
                    FamilyRole.role_id == role_id,
                )
            ).scalar_one_or_none()
            if family_role is None:
                session.add(
                    FamilyRole(
                        guild_id=guild_id,
                        role_id=role_id,
                        role_name=role_name,
                        updated_at=datetime.utcnow(),
                    )
                )
                return

            family_role.role_name = role_name
            self._touch(family_role)

    def get_family_roles(self, guild_id: int) -> list[tuple[int, str]]:
        with self.session_factory.begin() as session:
            return [
                (int(role_id), role_name)
                for role_id, role_name in session.execute(
                    select(FamilyRole.role_id, FamilyRole.role_name).where(
                        FamilyRole.guild_id == guild_id
                    )
                ).all()
            ]

    def record_message_activity(
        self,
        guild_id: int,
        actor_user_id: int,
        family_name: str,
        target_user_ids: list[int],
        award_hoop: bool,
        source_message_id: int,
        source_channel_id: int,
        attachment_url: str | None,
        event_date: date,
        created_at: datetime,
    ) -> MessageRecording:
        recorded_target_ids: list[int] = []
        duplicate_target_ids: list[int] = []
        unique_targets = list(dict.fromkeys(target_user_ids))

        with self.session_factory.begin() as session:
            for target_user_id in unique_targets:
                already_counted = session.execute(
                    select(EventRow.id).where(
                        EventRow.guild_id == guild_id,
                        EventRow.event_type == EventType.SNIPE,
                        EventRow.actor_user_id == actor_user_id,
                        EventRow.target_user_id == target_user_id,
                        EventRow.event_date == event_date,
                        EventRow.voided_at.is_(None),
                    )
                ).scalar_one_or_none()

                if already_counted is not None:
                    duplicate_target_ids.append(target_user_id)
                    continue

                session.add(
                    EventRow(
                        guild_id=guild_id,
                        event_type=EventType.SNIPE,
                        family_name=family_name,
                        points=1,
                        actor_user_id=actor_user_id,
                        attributed_user_id=actor_user_id,
                        target_user_id=target_user_id,
                        source_message_id=source_message_id,
                        source_channel_id=source_channel_id,
                        attachment_url=attachment_url,
                        event_date=event_date,
                        created_at=created_at,
                    )
                )
                recorded_target_ids.append(target_user_id)

            hoop_recorded = False
            if award_hoop:
                existing_hoop = session.execute(
                    select(EventRow.id).where(
                        EventRow.guild_id == guild_id,
                        EventRow.event_type == EventType.HOOP,
                        EventRow.source_message_id == source_message_id,
                        EventRow.voided_at.is_(None),
                    )
                ).scalar_one_or_none()

                if existing_hoop is None:
                    session.add(
                        EventRow(
                            guild_id=guild_id,
                            event_type=EventType.HOOP,
                            family_name=family_name,
                            points=2,
                            actor_user_id=actor_user_id,
                            attributed_user_id=None,
                            target_user_id=None,
                            source_message_id=source_message_id,
                            source_channel_id=source_channel_id,
                            attachment_url=attachment_url,
                            event_date=event_date,
                            reason="Automatic HOOPing bonus",
                            created_at=created_at,
                        )
                    )
                    hoop_recorded = True

            total_points_for_message = self._active_points_for_message_session(
                session, guild_id, source_message_id
            )

        return MessageRecording(
            recorded_target_ids=recorded_target_ids,
            duplicate_target_ids=duplicate_target_ids,
            hoop_recorded=hoop_recorded,
            total_points_for_message=total_points_for_message,
        )

    def create_adjustment(
        self,
        guild_id: int,
        family_name: str,
        points: int,
        reason: str,
        actor_user_id: int,
        event_date: date,
        created_at: datetime,
    ) -> None:
        with self.session_factory.begin() as session:
            session.add(
                EventRow(
                    guild_id=guild_id,
                    event_type=EventType.ADJUSTMENT,
                    family_name=family_name,
                    points=points,
                    actor_user_id=actor_user_id,
                    attributed_user_id=None,
                    target_user_id=None,
                    source_message_id=None,
                    source_channel_id=None,
                    attachment_url=None,
                    event_date=event_date,
                    reason=reason,
                    created_at=created_at,
                )
            )

    def void_latest_snipe(
        self,
        guild_id: int,
        actor_user_id: int,
        target_user_id: int,
        voided_by_user_id: int,
    ) -> VoidedSnipe | None:
        with self.session_factory.begin() as session:
            row = session.execute(
                select(EventRow).where(
                    EventRow.guild_id == guild_id,
                    EventRow.event_type == EventType.SNIPE,
                    EventRow.actor_user_id == actor_user_id,
                    EventRow.target_user_id == target_user_id,
                    EventRow.voided_at.is_(None),
                ).order_by(desc(EventRow.created_at), desc(EventRow.id))
            ).scalars().first()

            if row is None:
                return None

            row.voided_at = datetime.utcnow()
            row.voided_by_user_id = voided_by_user_id
            row.void_reason = "Voided by admin"

            return VoidedSnipe(
                row_id=row.id,
                event_type=row.event_type,
                points=row.points,
                actor_user_id=row.actor_user_id or actor_user_id,
                target_user_id=row.target_user_id,
                family_name=row.family_name,
                reason=row.reason,
                source_message_id=row.source_message_id,
                source_channel_id=row.source_channel_id,
                created_at=row.created_at,
            )

    def void_event_by_id(
        self, guild_id: int, row_id: int, voided_by_user_id: int
    ) -> VoidedSnipe | None:
        with self.session_factory.begin() as session:
            row = session.execute(
                select(EventRow).where(
                    EventRow.guild_id == guild_id,
                    EventRow.id == row_id,
                    EventRow.voided_at.is_(None),
                )
            ).scalars().first()

            if row is None:
                return None

            row.voided_at = datetime.utcnow()
            row.voided_by_user_id = voided_by_user_id
            row.void_reason = "Voided by admin"

            return VoidedSnipe(
                row_id=row.id,
                event_type=row.event_type,
                points=row.points,
                actor_user_id=row.actor_user_id or 0,
                target_user_id=row.target_user_id,
                family_name=row.family_name,
                reason=row.reason,
                source_message_id=row.source_message_id,
                source_channel_id=row.source_channel_id,
                created_at=row.created_at,
            )

    def get_active_points_for_message(self, guild_id: int, source_message_id: int) -> int:
        with self.session_factory.begin() as session:
            return self._active_points_for_message_session(session, guild_id, source_message_id)

    def _active_points_for_message_session(
        self, session: Session, guild_id: int, source_message_id: int
    ) -> int:
        total = session.execute(
            select(func.coalesce(func.sum(EventRow.points), 0)).where(
                EventRow.guild_id == guild_id,
                EventRow.source_message_id == source_message_id,
                EventRow.voided_at.is_(None),
            )
        ).scalar_one()
        return int(total)

    def get_recent_events(self, guild_id: int, limit: int = 15) -> list[RecentEvent]:
        with self.session_factory.begin() as session:
            rows = (
                session.execute(
                    select(EventRow)
                    .where(EventRow.guild_id == guild_id)
                    .order_by(desc(EventRow.created_at), desc(EventRow.id))
                    .limit(limit)
                )
                .scalars()
                .all()
            )

        return [
            RecentEvent(
                row_id=row.id,
                event_type=row.event_type,
                family_name=row.family_name,
                points=row.points,
                actor_user_id=row.actor_user_id,
                target_user_id=row.target_user_id,
                reason=row.reason,
                created_at=row.created_at,
                voided_at=row.voided_at,
            )
            for row in rows
        ]

    def get_event_by_id(self, guild_id: int, row_id: int) -> EventReference | None:
        with self.session_factory.begin() as session:
            row = session.execute(
                select(EventRow).where(
                    EventRow.guild_id == guild_id,
                    EventRow.id == row_id,
                )
            ).scalars().first()

        if row is None:
            return None

        return EventReference(
            row_id=row.id,
            event_type=row.event_type,
            family_name=row.family_name,
            points=row.points,
            actor_user_id=row.actor_user_id,
            target_user_id=row.target_user_id,
            reason=row.reason,
            source_message_id=row.source_message_id,
            source_channel_id=row.source_channel_id,
            created_at=row.created_at,
            voided_at=row.voided_at,
        )

    def get_scoreboard(self, guild_id: int, include_all_people: bool) -> ScoreboardSnapshot:
        with self.session_factory.begin() as session:
            point_rows = session.execute(
                select(EventRow.family_name, func.coalesce(func.sum(EventRow.points), 0)).where(
                    EventRow.guild_id == guild_id,
                    EventRow.voided_at.is_(None),
                ).group_by(EventRow.family_name)
            ).all()
            family_points = {family_name: int(points) for family_name, points in point_rows}

            tracked_family_roles = (
                session.execute(
                    select(FamilyRole.role_id, FamilyRole.role_name).where(
                        FamilyRole.guild_id == guild_id
                    )
                )
                .all()
            )
            for _, family_name in tracked_family_roles:
                if family_name not in family_points:
                    family_points[family_name] = 0

            families = sorted(
                [
                    FamilyScore(
                        role_id=next(
                            (int(role_id) for role_id, role_name in tracked_family_roles if role_name == family_name),
                            None,
                        ),
                        family_name=family_name,
                        points=points,
                    )
                    for family_name, points in family_points.items()
                ],
                key=lambda row: (-row.points, row.family_name.lower()),
            )

            person_point_rows = session.execute(
                select(EventRow.attributed_user_id, func.coalesce(func.sum(EventRow.points), 0)).where(
                    EventRow.guild_id == guild_id,
                    EventRow.event_type == EventType.SNIPE,
                    EventRow.attributed_user_id.is_not(None),
                    EventRow.voided_at.is_(None),
                ).group_by(EventRow.attributed_user_id)
            ).all()
            person_points = {
                int(user_id): int(points)
                for user_id, points in person_point_rows
                if user_id is not None
            }

            people_user_ids = set(person_points)

            people = sorted(
                [
                    PersonScore(
                        user_id=user_id,
                        points=person_points.get(user_id, 0),
                    )
                    for user_id in people_user_ids
                ],
                key=lambda row: (-row.points, row.user_id),
            )

            if not include_all_people:
                people = people[:5]

            return ScoreboardSnapshot(families=families, people=people)

    def get_family_graph_series(
        self, guild_id: int, start_date: date, end_date: date
    ) -> list[FamilyGraphSeries]:
        with self.session_factory.begin() as session:
            rows = session.execute(
                select(EventRow.event_date, EventRow.family_name, func.sum(EventRow.points)).where(
                    EventRow.guild_id == guild_id,
                    EventRow.event_date >= start_date,
                    EventRow.event_date <= end_date,
                    EventRow.voided_at.is_(None),
                ).group_by(EventRow.event_date, EventRow.family_name)
            ).all()

        by_family: dict[str, dict[date, int]] = {}
        for event_date, family_name, points in rows:
            family_points = by_family.setdefault(family_name, {})
            family_points[event_date] = int(points)

        return [
            FamilyGraphSeries(family_name=family_name, daily_points=daily_points)
            for family_name, daily_points in sorted(by_family.items(), key=lambda item: item[0].lower())
        ]
