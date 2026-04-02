from __future__ import annotations

from dataclasses import dataclass
import os
from urllib.parse import quote_plus
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
    user = _require_env("DB_USER")
    password = _require_env("DB_PASSWORD")
    database = _require_env("DB_NAME")
    instance_connection_name = os.getenv("INSTANCE_CONNECTION_NAME")

    if instance_connection_name:
        return (
            "mysql+pymysql://"
            f"{quote_plus(user)}:{quote_plus(password)}@/"
            f"{quote_plus(database)}?unix_socket=/cloudsql/{instance_connection_name}"
        )

    host = os.getenv("DB_HOST", "127.0.0.1")
    port = os.getenv("DB_PORT", "3306")
    return (
        "mysql+pymysql://"
        f"{quote_plus(user)}:{quote_plus(password)}@{host}:{port}/{quote_plus(database)}"
    )


@dataclass(frozen=True)
class AppConfig:
    discord_token: str
    guild_id: int | None
    database_url: str
    http_port: int
    bot_timezone: ZoneInfo
    bot_name: str

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
        )
