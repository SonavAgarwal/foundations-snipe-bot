from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from zoneinfo import ZoneInfo


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _optional_int(name: str) -> int | None:
    value = os.getenv(name)
    if value in (None, ""):
        return None
    return int(value)


def _build_database_url() -> str:
    sqlite_path = Path(os.getenv("SQLITE_PATH", "data/foundations_bot.db")).expanduser()
    sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{sqlite_path.resolve()}"


@dataclass(frozen=True)
class AppConfig:
    discord_token: str
    guild_id: int | None
    database_url: str
    http_port: int
    bot_timezone: ZoneInfo
    bot_name: str
    bot_admin_role: str | None

    @classmethod
    def from_env(cls) -> "AppConfig":
        timezone_name = os.getenv("BOT_TIMEZONE", "America/Los_Angeles")
        database_url = os.getenv("DATABASE_URL") or _build_database_url()
        return cls(
            discord_token=_require_env("DISCORD_TOKEN"),
            guild_id=_optional_int("DISCORD_GUILD_ID"),
            database_url=database_url,
            http_port=int(os.getenv("PORT", "8080")),
            bot_timezone=ZoneInfo(timezone_name),
            bot_name=os.getenv("BOT_NAME", "Foundations Bot"),
            bot_admin_role=os.getenv("BOT_ADMIN_ROLE"),
        )
