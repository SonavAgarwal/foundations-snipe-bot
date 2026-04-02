from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import re

from aiohttp import web
import discord
from discord import app_commands
from discord.ext import commands

from foundations_bot.charts import render_two_week_graph
from foundations_bot.config import AppConfig
from foundations_bot.store import FoundationsStore


ROLE_MENTION_RE = re.compile(r"<@&(\d+)>")
USER_MENTION_RE = re.compile(r"<@!?(\d+)>")


def _utc_naive(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def _chunk_message(lines: list[str], limit: int = 1900) -> list[str]:
    chunks: list[str] = []
    current = ""
    for line in lines:
        candidate = line if not current else f"{current}\n{line}"
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
        current = line

    if current:
        chunks.append(current)

    return chunks


class FoundationsBot(commands.Bot):
    def __init__(self, config: AppConfig, store: FoundationsStore) -> None:
        intents = discord.Intents.default()
        intents.guilds = True
        intents.members = True
        intents.messages = True
        intents.message_content = True

        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=intents,
            activity=discord.Game(name="tracking family points"),
        )
        self.config = config
        self.store = store
        self.command_guild = (
            discord.Object(id=config.guild_id) if config.guild_id is not None else None
        )
        self._health_runner: web.AppRunner | None = None
        self._register_commands()

    async def setup_hook(self) -> None:
        self._health_runner = await start_health_server(self.config.http_port)
        if self.command_guild is not None:
            await self.tree.sync(guild=self.command_guild)
            return
        await self.tree.sync()

    async def close(self) -> None:
        if self._health_runner is not None:
            await self._health_runner.cleanup()
        self.store.close()
        await super().close()

    async def on_ready(self) -> None:
        print(f"{self.config.bot_name} ready as {self.user} at {datetime.utcnow().isoformat()}Z")

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return

        await self._handle_spotting_message(message)

    def _register_commands(self) -> None:
        command_kwargs = {}
        if self.command_guild is not None:
            command_kwargs["guild"] = self.command_guild

        @self.tree.error
        async def on_tree_error(
            interaction: discord.Interaction, error: app_commands.AppCommandError
        ) -> None:
            if interaction.response.is_done():
                await interaction.followup.send(
                    f"Command failed: {error}",
                    ephemeral=True,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                return

            await interaction.response.send_message(
                f"Command failed: {error}",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )

        @app_commands.default_permissions(manage_guild=True)
        @self.tree.command(
            name="set-lship-role",
            description="Store the leadership role used for spotting validation.",
            **command_kwargs,
        )
        async def set_lship_role(
            interaction: discord.Interaction, role: discord.Role
        ) -> None:
            guild = self._require_guild(interaction)
            self.store.set_lship_role(guild.id, role.id)
            await interaction.response.send_message(
                f"Lship role set to {role.name}.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )

        @app_commands.default_permissions(manage_guild=True)
        @self.tree.command(
            name="set-genmem-role",
            description="Store the general member role used for spotting validation.",
            **command_kwargs,
        )
        async def set_genmem_role(
            interaction: discord.Interaction, role: discord.Role
        ) -> None:
            guild = self._require_guild(interaction)
            self.store.set_genmem_role(guild.id, role.id)
            await interaction.response.send_message(
                f"Genmem role set to {role.name}.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )

        @app_commands.default_permissions(manage_guild=True)
        @self.tree.command(
            name="setchannel",
            description="Bind spotting behavior to a specific channel.",
            **command_kwargs,
        )
        async def setchannel(
            interaction: discord.Interaction, channel: discord.TextChannel
        ) -> None:
            guild = self._require_guild(interaction)
            self.store.set_sniping_channel(guild.id, channel.id)
            await interaction.response.send_message(
                f"Sniping channel set to {channel.mention}.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )

        @app_commands.default_permissions(manage_guild=True)
        @self.tree.command(
            name="setfam",
            description="Assign a family to a list of tagged users or usernames.",
            **command_kwargs,
        )
        async def setfam(
            interaction: discord.Interaction, family: str, members: str
        ) -> None:
            guild = self._require_guild(interaction)
            resolved_family = self._resolve_family_name(guild, family)
            resolved_members = self._resolve_members(guild, members)
            if not resolved_members:
                await interaction.response.send_message(
                    "No members matched. Use mentions if possible.",
                    ephemeral=True,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                return

            self.store.set_family_for_users(
                guild.id,
                [member.id for member in resolved_members],
                resolved_family,
            )

            label = "NONE" if resolved_family is None else resolved_family
            member_mentions = ", ".join(f"<@{member.id}>" for member in resolved_members)
            await interaction.response.send_message(
                f"Set family `{label}` for {member_mentions}.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )

        @app_commands.default_permissions(manage_guild=True)
        @self.tree.command(
            name="adjust",
            description="Add or subtract family points with a reason.",
            **command_kwargs,
        )
        async def adjust(
            interaction: discord.Interaction, family: str, points: int, reason: str
        ) -> None:
            guild = self._require_guild(interaction)
            resolved_family = self._resolve_family_name(guild, family)
            if resolved_family is None:
                await interaction.response.send_message(
                    "Adjustments need a real family name, not NONE.",
                    ephemeral=True,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                return

            now = discord.utils.utcnow()
            event_date = now.astimezone(self.config.bot_timezone).date()
            self.store.create_adjustment(
                guild_id=guild.id,
                family_name=resolved_family,
                points=points,
                reason=reason,
                actor_user_id=interaction.user.id,
                event_date=event_date,
                created_at=_utc_naive(now),
            )
            await interaction.response.send_message(
                f"Adjusted `{resolved_family}` by {points} point(s). Reason: {reason}",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )

        @app_commands.default_permissions(manage_guild=True)
        @self.tree.command(
            name="void",
            description="Void any event by ID, or void the latest matching snipe for a shooter and target.",
            **command_kwargs,
        )
        async def void(
            interaction: discord.Interaction,
            event_id: int | None = None,
            sender: discord.Member | None = None,
            sniped: discord.Member | None = None,
        ) -> None:
            guild = self._require_guild(interaction)
            voided = None
            if event_id is not None:
                voided = self.store.void_event_by_id(
                    guild_id=guild.id,
                    row_id=event_id,
                    voided_by_user_id=interaction.user.id,
                )
            elif sender is not None and sniped is not None:
                voided = self.store.void_latest_snipe(
                    guild_id=guild.id,
                    actor_user_id=sender.id,
                    target_user_id=sniped.id,
                    voided_by_user_id=interaction.user.id,
                )
            else:
                await interaction.response.send_message(
                    "Pass either `event_id`, or both `sender` and `sniped`.",
                    ephemeral=True,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                return

            if voided is None:
                await interaction.response.send_message(
                    "No matching active event was found.",
                    ephemeral=True,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                return

            created_at = voided.created_at.replace(tzinfo=timezone.utc)
            timestamp = discord.utils.format_dt(created_at, style="f")
            summary = (
                f"Voided event `#{voided.row_id}` "
                f"({voided.event_type.value}, {voided.points:+} for `{voided.family_name}`)"
            )
            if voided.event_type.value == "snipe" and voided.target_user_id is not None:
                summary += f" from <@{voided.actor_user_id}> on <@{voided.target_user_id}>"
            elif voided.actor_user_id:
                summary += f" created by <@{voided.actor_user_id}>"
            summary += f" from {timestamp}."
            await interaction.response.send_message(
                summary,
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )

        @app_commands.default_permissions(manage_guild=True)
        @self.tree.command(
            name="recent-events",
            description="Show recent event rows so you can void any of them by ID.",
            **command_kwargs,
        )
        async def recent_events(
            interaction: discord.Interaction, limit: app_commands.Range[int, 1, 50] = 15
        ) -> None:
            guild = self._require_guild(interaction)
            events = self.store.get_recent_events(guild.id, limit=limit)
            if not events:
                await interaction.response.send_message(
                    "No events found yet.",
                    ephemeral=True,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                return

            lines = ["**Recent Events**"]
            for event in events:
                status = "VOIDED" if event.voided_at else "ACTIVE"
                timestamp = event.created_at.replace(tzinfo=timezone.utc).strftime(
                    "%Y-%m-%d %H:%M UTC"
                )
                detail = (
                    f"#{event.row_id} | {status} | {event.event_type.value} | "
                    f"`{event.family_name}` | {event.points:+}"
                )
                if event.event_type.value == "snipe":
                    detail += f" | <@{event.actor_user_id}> -> <@{event.target_user_id}>"
                elif event.actor_user_id is not None:
                    detail += f" | by <@{event.actor_user_id}>"
                if event.reason:
                    detail += f" | {event.reason}"
                detail += f" | {timestamp}"
                lines.append(detail)

            chunks = _chunk_message(lines)
            await interaction.response.send_message(
                chunks[0],
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            for chunk in chunks[1:]:
                await interaction.followup.send(
                    chunk,
                    ephemeral=True,
                    allowed_mentions=discord.AllowedMentions.none(),
                )

        @self.tree.command(
            name="leaderboard",
            description="Show family standings and the top people by spotting points.",
            **command_kwargs,
        )
        async def leaderboard(
            interaction: discord.Interaction, full: bool = False
        ) -> None:
            guild = self._require_guild(interaction)
            snapshot = self.store.get_scoreboard(guild.id, include_all_people=full)

            lines = ["**Family Standings**"]
            if snapshot.families:
                for index, family in enumerate(snapshot.families, start=1):
                    lines.append(f"{index}. `{family.family_name}` - {family.points}")
            else:
                lines.append("No family points yet.")

            lines.append("")
            lines.append("**People**")
            if snapshot.people:
                for index, person in enumerate(snapshot.people, start=1):
                    family_suffix = f" ({person.family_name})" if person.family_name else ""
                    lines.append(
                        f"{index}. <@{person.user_id}>{family_suffix} - {person.points}"
                    )
            else:
                lines.append("No individual spotting points yet.")

            chunks = _chunk_message(lines)
            await interaction.response.send_message(
                chunks[0], allowed_mentions=discord.AllowedMentions.none()
            )
            for chunk in chunks[1:]:
                await interaction.followup.send(
                    chunk, allowed_mentions=discord.AllowedMentions.none()
                )

        @self.tree.command(
            name="graph",
            description="Show a two-week cumulative graph of family points.",
            **command_kwargs,
        )
        async def graph(interaction: discord.Interaction) -> None:
            guild = self._require_guild(interaction)
            now = discord.utils.utcnow().astimezone(self.config.bot_timezone).date()
            start_date = now - timedelta(days=13)
            graph_series = self.store.get_family_graph_series(guild.id, start_date, now)
            if not graph_series:
                await interaction.response.send_message(
                    "No point activity exists in the last two weeks.",
                    ephemeral=True,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                return

            image = render_two_week_graph(graph_series, start_date, now)
            await interaction.response.send_message(
                file=discord.File(image, filename="foundations-graph.png"),
                allowed_mentions=discord.AllowedMentions.none(),
            )

    def _require_guild(self, interaction: discord.Interaction) -> discord.Guild:
        if interaction.guild is None:
            raise app_commands.AppCommandError("This command only works in a server.")
        return interaction.guild

    def _resolve_family_name(self, guild: discord.Guild, raw_value: str) -> str | None:
        stripped = raw_value.strip()
        if stripped.upper() == "NONE":
            return None

        match = ROLE_MENTION_RE.fullmatch(stripped)
        if match:
            role = guild.get_role(int(match.group(1)))
            if role is None:
                raise app_commands.AppCommandError("That family role could not be found.")
            return role.name

        return stripped

    def _resolve_members(self, guild: discord.Guild, raw_value: str) -> list[discord.Member]:
        found: dict[int, discord.Member] = {}

        for user_id in USER_MENTION_RE.findall(raw_value):
            member = guild.get_member(int(user_id))
            if member is not None:
                found[member.id] = member

        cleaned_value = USER_MENTION_RE.sub("", raw_value).strip()
        if not cleaned_value:
            return list(found.values())

        if "," in cleaned_value:
            tokens = [token.strip() for token in cleaned_value.split(",")]
        else:
            tokens = [token.strip() for token in cleaned_value.split()]

        for token in tokens:
            if not token:
                continue

            member = discord.utils.get(guild.members, name=token)
            if member is None:
                member = discord.utils.get(guild.members, display_name=token)
            if member is not None:
                found[member.id] = member

        return list(found.values())

    def _member_has_trackable_role(
        self, member: discord.Member, lship_role_id: int | None, genmem_role_id: int | None
    ) -> bool:
        role_ids = {role.id for role in member.roles}
        configured_role_ids = {role_id for role_id in (lship_role_id, genmem_role_id) if role_id}
        if not configured_role_ids:
            return not member.bot
        return bool(role_ids & configured_role_ids)

    def _image_attachment_url(self, message: discord.Message) -> str | None:
        for attachment in message.attachments:
            if attachment.content_type and attachment.content_type.startswith("image/"):
                return attachment.url
            lower_name = attachment.filename.lower()
            if lower_name.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp")):
                return attachment.url
        return None

    async def _handle_spotting_message(self, message: discord.Message) -> None:
        settings = self.store.get_guild_settings(message.guild.id)
        if settings.sniping_channel_id is None or message.channel.id != settings.sniping_channel_id:
            return

        if not isinstance(message.author, discord.Member):
            return

        image_url = self._image_attachment_url(message)
        if image_url is None:
            return

        if not self._member_has_trackable_role(
            message.author, settings.lship_role_id, settings.genmem_role_id
        ):
            return

        sender_family = self.store.get_family_for_user(message.guild.id, message.author.id)
        if sender_family is None:
            await message.reply(
                "No family is set for you yet. Use `/setfam` first.",
                mention_author=False,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        unique_mentions: dict[int, discord.Member] = {}
        for member in message.mentions:
            if member.bot or member.id == message.author.id:
                continue
            if not self._member_has_trackable_role(
                member, settings.lship_role_id, settings.genmem_role_id
            ):
                continue
            unique_mentions[member.id] = member

        if not unique_mentions:
            return

        mentioned_members = list(unique_mentions.values())
        mentioned_ids = [member.id for member in mentioned_members]
        families = self.store.get_families_for_users(message.guild.id, [message.author.id, *mentioned_ids])

        present_team_members = {message.author.id, *mentioned_ids}
        same_family_tagged_count = sum(
            1 for member in mentioned_members if families.get(member.id) == sender_family
        )
        award_hoop = len(present_team_members) >= 3 and same_family_tagged_count >= 2

        created_at = _utc_naive(message.created_at)
        event_date = message.created_at.astimezone(self.config.bot_timezone).date()
        recording = self.store.record_message_activity(
            guild_id=message.guild.id,
            actor_user_id=message.author.id,
            family_name=sender_family,
            target_user_ids=mentioned_ids,
            award_hoop=award_hoop,
            source_message_id=message.id,
            source_channel_id=message.channel.id,
            attachment_url=image_url,
            event_date=event_date,
            created_at=created_at,
        )

        if not recording.recorded_target_ids and not recording.hoop_recorded:
            if recording.duplicate_target_ids:
                duplicate_mentions = ", ".join(
                    f"<@{user_id}>" for user_id in recording.duplicate_target_ids
                )
                await message.reply(
                    f"No new snipe points recorded. Already counted today for {duplicate_mentions}.",
                    mention_author=False,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            return

        summary_lines = [f"Recorded points for `{sender_family}`."]
        if recording.recorded_target_ids:
            targets = ", ".join(f"<@{user_id}>" for user_id in recording.recorded_target_ids)
            summary_lines.append(
                f"Snipe points: +{len(recording.recorded_target_ids)} from {targets}."
            )
        if recording.hoop_recorded:
            summary_lines.append("HOOPing bonus: +2.")
        if recording.duplicate_target_ids:
            duplicates = ", ".join(f"<@{user_id}>" for user_id in recording.duplicate_target_ids)
            summary_lines.append(f"Skipped already-counted-today targets: {duplicates}.")

        await message.reply(
            "\n".join(summary_lines),
            mention_author=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )


async def start_health_server(port: int) -> web.AppRunner:
    app = web.Application()

    async def healthcheck(_: web.Request) -> web.Response:
        return web.json_response({"status": "ok"})

    app.router.add_get("/", healthcheck)
    app.router.add_get("/healthz", healthcheck)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=port)
    await site.start()
    return runner


async def _run() -> None:
    config = AppConfig.from_env()
    store = FoundationsStore(config.database_url)
    store.initialize()

    bot = FoundationsBot(config, store)
    try:
        await bot.start(config.discord_token)
    finally:
        await bot.close()


def main() -> None:
    asyncio.run(_run())
