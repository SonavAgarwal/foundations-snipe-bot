from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import random
import re

from aiohttp import web
import discord
from discord import app_commands
from discord.ext import commands

from foundations_bot.charts import render_two_week_graph
from foundations_bot.config import AppConfig
from foundations_bot.store import FoundationsStore, PersonScore


ROLE_MENTION_RE = re.compile(r"<@&(\d+)>")
USER_MENTION_RE = re.compile(r"<@!?(\d+)>")
SCORE_EMOJIS = {
    0: "❌",
    1: "1️⃣",
    2: "2️⃣",
    3: "3️⃣",
    4: "4️⃣",
    5: "5️⃣",
    6: "6️⃣",
    7: "7️⃣",
    8: "8️⃣",
    9: "9️⃣",
}
TRACKED_SCORE_REACTIONS = set(SCORE_EMOJIS.values()) | {"🔟"}
NO_FAMILY_REACTIONS = ("🔥", "📸", "🤨", "💀", "❤️", "🙏")
TRACKED_SCORE_REACTIONS.update(NO_FAMILY_REACTIONS)


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
            discord.Object(
                id=config.guild_id) if config.guild_id is not None else None
        )
        self._health_runner: web.AppRunner | None = None
        self._register_commands()

    async def setup_hook(self) -> None:
        self._health_runner = await start_health_server(self.config.http_port)
        if self.command_guild is not None:
            self.tree.clear_commands(guild=None)
            await self.tree.sync()
            await self.tree.sync(guild=self.command_guild)
            return
        await self.tree.sync()

    async def close(self) -> None:
        if self._health_runner is not None:
            await self._health_runner.cleanup()
        self.store.close()
        await super().close()

    async def on_ready(self) -> None:
        await self._leave_disallowed_guilds()
        print(
            f"{self.config.bot_name} ready as {self.user} at {datetime.utcnow().isoformat()}Z")

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return
        if not self._is_allowed_guild_id(message.guild.id):
            return

        await self._handle_spotting_message(message)

    async def on_guild_join(self, guild: discord.Guild) -> None:
        if not self._is_allowed_guild_id(guild.id):
            await guild.leave()

    def _register_commands(self) -> None:
        command_kwargs = {}
        if self.command_guild is not None:
            command_kwargs["guild"] = self.command_guild

        @self.tree.error
        async def on_tree_error(
            interaction: discord.Interaction, error: app_commands.AppCommandError
        ) -> None:
            try:
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
            except discord.NotFound:
                return

        @self.tree.command(
            name="set-lship-role",
            description="Store the leadership role used for spotting validation.",
            **command_kwargs,
        )
        async def set_lship_role(
            interaction: discord.Interaction, role: discord.Role
        ) -> None:
            self._require_bot_admin(interaction)
            guild = self._require_guild(interaction)
            self.store.set_lship_role(guild.id, role.id)
            await interaction.response.send_message(
                f"Lship role set to {role.name}.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )

        @self.tree.command(
            name="set-genmem-role",
            description="Store the general member role used for spotting validation.",
            **command_kwargs,
        )
        async def set_genmem_role(
            interaction: discord.Interaction, role: discord.Role
        ) -> None:
            self._require_bot_admin(interaction)
            guild = self._require_guild(interaction)
            self.store.set_genmem_role(guild.id, role.id)
            await interaction.response.send_message(
                f"Genmem role set to {role.name}.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )

        @self.tree.command(
            name="hello",
            description="Basic connectivity test.",
            **command_kwargs,
        )
        async def hello(interaction: discord.Interaction) -> None:
            await interaction.response.send_message(
                "Foundations Bot is online.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )

        @self.tree.command(
            name="setchannel",
            description="Bind spotting behavior to a specific channel.",
            **command_kwargs,
        )
        async def setchannel(
            interaction: discord.Interaction, channel: discord.TextChannel
        ) -> None:
            self._require_bot_admin(interaction)
            guild = self._require_guild(interaction)
            self.store.set_sniping_channel(guild.id, channel.id)
            await interaction.response.send_message(
                f"Sniping channel set to {channel.mention}.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )

        @self.tree.command(
            name="setfam",
            description="Assign or clear family roles for a list of tagged users or usernames.",
            **command_kwargs,
        )
        async def setfam(
            interaction: discord.Interaction, family: str, members: str
        ) -> None:
            self._require_bot_admin(interaction)
            await interaction.response.defer(
                ephemeral=True,
                thinking=False,
            )
            guild = self._require_guild(interaction)
            resolved_members = await self._resolve_members(guild, members)
            if not resolved_members:
                await interaction.followup.send(
                    "No members matched. Use mentions if possible.",
                    ephemeral=True,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                return

            tracked_family_roles = self.store.get_family_roles(guild.id)
            tracked_role_ids = {role_id for role_id, _ in tracked_family_roles}
            family_role = self._resolve_family_role(guild, family)

            if family_role is not None:
                self.store.register_family_role(
                    guild.id, family_role.id, family_role.name)
                tracked_role_ids.add(family_role.id)

            try:
                for member in resolved_members:
                    roles_to_remove = [
                        role for role in member.roles if role.id in tracked_role_ids and role != family_role
                    ]
                    if roles_to_remove:
                        await member.remove_roles(*roles_to_remove, reason="Foundations Bot /setfam")
                    if family_role is not None and family_role not in member.roles:
                        await member.add_roles(family_role, reason="Foundations Bot /setfam")
            except discord.Forbidden as error:
                raise app_commands.AppCommandError(
                    "I need `Manage Roles`, and my bot role must be above the family roles."
                ) from error
            except discord.HTTPException as error:
                raise app_commands.AppCommandError(
                    "Discord rejected the role update.") from error

            label = "NONE" if family_role is None else family_role.name
            member_mentions = ", ".join(
                f"<@{member.id}>" for member in resolved_members)
            await interaction.followup.send(
                f"Set family `{label}` for {member_mentions}.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )

        @self.tree.command(
            name="adjust",
            description="Add or subtract points for a family or an existing event's family.",
            **command_kwargs,
        )
        async def adjust(
            interaction: discord.Interaction,
            event_id: int,
            points: int,
            family: str | None = None,
            reason: str | None = None,
        ) -> None:
            self._require_bot_admin(interaction)
            guild = self._require_guild(interaction)
            family_name: str | None = None
            event = None
            reason_text = reason.strip() if reason and reason.strip() else "Manual adjustment"

            if event_id < 0:
                event = self.store.get_recent_adjustment_target(guild.id, abs(event_id))
                if event is None:
                    await interaction.response.send_message(
                        "That recent event was not found.",
                        ephemeral=True,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                    return
                family_name = event.family_name
                reason_text = (
                    f"{reason_text} (adjustment for event #{event.row_id}, via {event_id})"
                )
            elif event_id > 0:
                event = self.store.get_event_by_id(guild.id, event_id)
                if event is None:
                    await interaction.response.send_message(
                        "That event ID was not found.",
                        ephemeral=True,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                    return
                family_name = event.family_name
                reason_text = f"{reason_text} (adjustment for event #{event_id})"
            elif family is not None:
                family_role = await self._resolve_adjust_family_role(guild, family)
                if family_role is None:
                    await interaction.response.send_message(
                        "Adjustments need a real family role or a member with a family role.",
                        ephemeral=True,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                    return
                self.store.register_family_role(
                    guild.id, family_role.id, family_role.name)
                family_name = family_role.name
            else:
                await interaction.response.send_message(
                    "Use a positive event ID, a negative recent event number, or `event_id:0` with `family`.",
                    ephemeral=True,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                return

            now = discord.utils.utcnow()
            event_date = now.astimezone(self.config.bot_timezone).date()
            source_message_id = event.source_message_id if event is not None else None
            source_channel_id = event.source_channel_id if event is not None else None
            self.store.create_adjustment(
                guild_id=guild.id,
                family_name=family_name,
                points=points,
                reason=reason_text,
                actor_user_id=interaction.user.id,
                event_date=event_date,
                created_at=_utc_naive(now),
                source_message_id=source_message_id,
                source_channel_id=source_channel_id,
            )
            await self._refresh_score_reaction(
                guild,
                source_channel_id,
                source_message_id,
            )
            await interaction.response.send_message(
                f"Adjusted `{family_name}` by {points} point(s). Reason: {reason_text}",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )

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
            self._require_bot_admin(interaction)
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

            summary = f"<@{interaction.user.id}> voided {voided.event_type.value} `#{voided.row_id}`"
            if voided.event_type.value == "snipe" and voided.target_user_id is not None:
                summary += f" from <@{voided.actor_user_id}> on <@{voided.target_user_id}>"
            elif voided.actor_user_id:
                summary += f" by <@{voided.actor_user_id}>"
            summary += "."
            await self._refresh_score_reaction(
                guild,
                voided.source_channel_id,
                voided.source_message_id,
            )
            await interaction.response.send_message(
                summary,
                allowed_mentions=discord.AllowedMentions(users=True),
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
            self._require_bot_admin(interaction)
            await interaction.response.defer(
                ephemeral=True,
                thinking=False,
            )
            guild = self._require_guild(interaction)
            events = self.store.get_recent_events(guild.id, limit=limit)
            if not events:
                await interaction.followup.send(
                    "No events found yet.",
                    ephemeral=True,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                return

            lines = ["**Recent Events**"]
            for event in events:
                timestamp = event.created_at.replace(tzinfo=timezone.utc).astimezone(
                    self.config.bot_timezone
                ).strftime("%H:%M")
                if event.event_type.value == "snipe":
                    lines.append(
                        f"`{event.row_id}` <@{event.actor_user_id}> -> <@{event.target_user_id}> {timestamp}"
                    )
                elif event.event_type.value == "photo":
                    lines.append(
                        f"`{event.row_id}` photo by <@{event.actor_user_id}> "
                        f"({event.family_name}, 0 pts) {timestamp}"
                    )
                elif event.actor_user_id is not None:
                    lines.append(
                        f"`{event.row_id}` {event.event_type.value} by <@{event.actor_user_id}> {timestamp}"
                    )
                else:
                    lines.append(
                        f"`{event.row_id}` {event.event_type.value} {timestamp}")

            chunks = _chunk_message(lines)
            await interaction.followup.send(
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
            await interaction.response.defer(thinking=False)
            guild = self._require_guild(interaction)
            snapshot = self.store.get_scoreboard(
                guild.id, include_all_people=full)
            settings = self.store.get_guild_settings(guild.id)

            lines = ["**Fam Standings**"]
            if snapshot.families:
                for index, family in enumerate(snapshot.families, start=1):
                    lines.append(
                        f"{index}. `{family.family_name}` - {family.points}")
            else:
                lines.append("No family points yet.")

            # lines.append("")
            # lines.append("**People**")
            # people = snapshot.people
            # if full:
            #     point_map = {
            #         row.user_id: row.points for row in snapshot.people}
            #     people = [
            #         PersonScore(user_id=member.id,
            #                     points=point_map.get(member.id, 0))
            #         for member in guild.members
            #         if not member.bot
            #         and self._member_has_trackable_role(
            #             member, settings.lship_role_id, settings.genmem_role_id
            #         )
            #     ]
            #     people = sorted(
            #         people, key=lambda row: (-row.points, row.user_id))

            # if people:
            #     for index, person in enumerate(people[:5] if not full else people, start=1):
            #         member = guild.get_member(person.user_id)
            #         current_family = (
            #             self._current_family_name(
            #                 guild, member) if member is not None else None
            #         )
            #         family_suffix = f" ({current_family})" if current_family else ""
            #         lines.append(
            #             f"{index}. <@{person.user_id}>{family_suffix} - {person.points}"
            #         )
            # else:
            #     lines.append("No individual spotting points yet.")

            chunks = _chunk_message(lines)
            await interaction.followup.send(
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
            await interaction.response.defer(thinking=False)
            guild = self._require_guild(interaction)
            now = discord.utils.utcnow().astimezone(self.config.bot_timezone).date()
            start_date = now - timedelta(days=13)
            graph_series = self.store.get_family_graph_series(
                guild.id, start_date, now)
            if not graph_series:
                await interaction.followup.send(
                    "No point activity exists in the last two weeks.",
                    ephemeral=True,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                return

            image = render_two_week_graph(graph_series, start_date, now)
            await interaction.followup.send(
                file=discord.File(image, filename="foundations-graph.png"),
                allowed_mentions=discord.AllowedMentions.none(),
            )

    def _require_guild(self, interaction: discord.Interaction) -> discord.Guild:
        if interaction.guild is None:
            raise app_commands.AppCommandError(
                "This command only works in a server.")
        if not self._is_allowed_guild_id(interaction.guild.id):
            raise app_commands.AppCommandError(
                "This bot is restricted to a different server.")
        return interaction.guild

    def _require_bot_admin(self, interaction: discord.Interaction) -> None:
        self._require_guild(interaction)
        if not isinstance(interaction.user, discord.Member):
            raise app_commands.AppCommandError(
                "This command only works for server members.")

        configured_role = self.config.bot_admin_role
        if configured_role:
            normalized_role = configured_role.casefold()
            if any(role.name.casefold() == normalized_role for role in interaction.user.roles):
                return
            raise app_commands.AppCommandError(
                f"You need the `{configured_role}` role to use this command."
            )

        if interaction.user.guild_permissions.manage_guild or interaction.user.guild_permissions.manage_roles:
            return

        raise app_commands.AppCommandError(
            "You do not have permission to use this command.")

    def _is_allowed_guild_id(self, guild_id: int) -> bool:
        if self.config.guild_id is None:
            return True
        return guild_id == self.config.guild_id

    async def _leave_disallowed_guilds(self) -> None:
        if self.config.guild_id is None:
            return
        for guild in list(self.guilds):
            if guild.id != self.config.guild_id:
                await guild.leave()

    def _resolve_family_role(self, guild: discord.Guild, raw_value: str) -> discord.Role | None:
        stripped = raw_value.strip()
        if stripped.upper() == "NONE":
            return None

        match = ROLE_MENTION_RE.fullmatch(stripped)
        if match:
            role = guild.get_role(int(match.group(1)))
            if role is None:
                raise app_commands.AppCommandError(
                    "That family role could not be found.")
            return role

        role = discord.utils.get(guild.roles, name=stripped)
        if role is None:
            raise app_commands.AppCommandError(
                "That family role could not be found by name.")
        return role

    async def _resolve_adjust_family_role(
        self, guild: discord.Guild, raw_value: str
    ) -> discord.Role | None:
        try:
            return self._resolve_family_role(guild, raw_value)
        except app_commands.AppCommandError:
            pass

        members = await self._resolve_members(guild, raw_value)
        if len(members) != 1:
            raise app_commands.AppCommandError(
                "That family value must be a family role or exactly one member."
            )
        return self._current_family_role(guild, members[0])

    async def _resolve_members(
        self, guild: discord.Guild, raw_value: str
    ) -> list[discord.Member]:
        found: dict[int, discord.Member] = {}

        for user_id in USER_MENTION_RE.findall(raw_value):
            member = guild.get_member(int(user_id))
            if member is None:
                try:
                    member = await guild.fetch_member(int(user_id))
                except discord.NotFound:
                    member = None
                except discord.HTTPException:
                    member = None
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
            normalized_token = token.lstrip("@").strip()
            if not normalized_token:
                continue

            member = discord.utils.get(guild.members, name=normalized_token)
            if member is None:
                member = discord.utils.get(
                    guild.members, display_name=normalized_token)
            if member is None:
                member = discord.utils.get(
                    guild.members, global_name=normalized_token)
            if member is not None:
                found[member.id] = member

        return list(found.values())

    def _member_has_trackable_role(
        self, member: discord.Member, lship_role_id: int | None, genmem_role_id: int | None
    ) -> bool:
        role_ids = {role.id for role in member.roles}
        configured_role_ids = {role_id for role_id in (
            lship_role_id, genmem_role_id) if role_id}
        if not configured_role_ids:
            return not member.bot
        return bool(role_ids & configured_role_ids)

    def _current_family_role(
        self, guild: discord.Guild, member: discord.Member
    ) -> discord.Role | None:
        tracked_family_roles = {
            role_id: guild.get_role(role_id) for role_id, _ in self.store.get_family_roles(guild.id)
        }
        member_family_roles = [
            role for role in member.roles if role.id in tracked_family_roles and tracked_family_roles[role.id] is not None
        ]
        if not member_family_roles:
            return None
        member_family_roles.sort(key=lambda role: role.position, reverse=True)
        return member_family_roles[0]

    def _current_family_name(
        self, guild: discord.Guild, member: discord.Member
    ) -> str | None:
        family_role = self._current_family_role(guild, member)
        return None if family_role is None else family_role.name

    def _score_reaction_emoji(self, total_points: int) -> str:
        if total_points >= 10:
            return "🔟"
        return SCORE_EMOJIS.get(total_points, "❌")

    async def _sync_reaction_emoji(self, message: discord.Message, emoji: str) -> None:
        for reaction in message.reactions:
            if str(reaction.emoji) in TRACKED_SCORE_REACTIONS and reaction.me:
                await message.remove_reaction(reaction.emoji, self.user)
        await message.add_reaction(emoji)

    async def _sync_score_reaction(self, message: discord.Message, total_points: int) -> None:
        await self._sync_reaction_emoji(message, self._score_reaction_emoji(total_points))

    async def _refresh_score_reaction(
        self, guild: discord.Guild, channel_id: int | None, message_id: int | None
    ) -> None:
        if channel_id is None or message_id is None:
            return
        channel = guild.get_channel(channel_id)
        if channel is None or not isinstance(channel, discord.TextChannel):
            return
        try:
            message = await channel.fetch_message(message_id)
        except discord.NotFound:
            return
        total_points = self.store.get_active_points_for_message(
            guild.id, message_id)
        await self._sync_score_reaction(message, total_points)

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

        sender_family_role = self._current_family_role(
            message.guild, message.author)
        if sender_family_role is None:
            await self._sync_reaction_emoji(message, random.choice(NO_FAMILY_REACTIONS))
            return
        sender_family = sender_family_role.name

        created_at = _utc_naive(message.created_at)
        event_date = message.created_at.astimezone(
            self.config.bot_timezone).date()

        if not self._member_has_trackable_role(
            message.author, settings.lship_role_id, settings.genmem_role_id
        ):
            self.store.ensure_photo_reference_event(
                guild_id=message.guild.id,
                actor_user_id=message.author.id,
                family_name=sender_family,
                source_message_id=message.id,
                source_channel_id=message.channel.id,
                attachment_url=image_url,
                event_date=event_date,
                created_at=created_at,
            )
            await self._sync_score_reaction(message, 0)
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
            self.store.ensure_photo_reference_event(
                guild_id=message.guild.id,
                actor_user_id=message.author.id,
                family_name=sender_family,
                source_message_id=message.id,
                source_channel_id=message.channel.id,
                attachment_url=image_url,
                event_date=event_date,
                created_at=created_at,
            )
            await self._sync_score_reaction(message, 0)
            return

        mentioned_members = list(unique_mentions.values())
        mentioned_ids = [member.id for member in mentioned_members]

        present_team_members = {message.author.id, *mentioned_ids}
        same_family_tagged_count = sum(
            1
            for member in mentioned_members
            if any(role.id == sender_family_role.id for role in member.roles)
        )
        award_hoop = len(
            present_team_members) >= 3 and same_family_tagged_count >= 2

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
            await self._sync_score_reaction(message, recording.total_points_for_message)
            return
        await self._sync_score_reaction(message, recording.total_points_for_message)


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
