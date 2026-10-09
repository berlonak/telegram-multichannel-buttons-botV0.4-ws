# copyright by berlonak
# telegram: @Kilax123
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Set


def load_dotenv(path: Path) -> None:
    """Tiny .env loader. Real environment variables have priority."""
    if not path.exists():
        return

    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].strip()
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if not key:
                continue
            if (value.startswith("'") and value.endswith("'")) or (
                value.startswith('"') and value.endswith('"')
            ):
                value = value[1:-1]
            os.environ.setdefault(key, value)
    except Exception as exc:
        print(f"WARNING: cannot read {path}: {exc}", file=sys.stderr)


def parse_bool(value: str, default: bool = False) -> bool:
    if value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on", "да"}


def parse_owner_ids(value: str) -> Set[int]:
    owners: Set[int] = set()
    for item in value.split(","):
        item = item.strip()
        if item.isdigit():
            owners.add(int(item))
    return owners


@dataclass(frozen=True)
class Settings:
    project_dir: Path
    bot_token: str
    api_url: str
    database_path: Path
    owner_ids: Set[int]
    allow_self_add_channels: bool
    api_timeout: int
    poll_timeout: int
    preview_ttl_hours: int
    log_level: str

    @classmethod
    def load(cls, project_dir: Path) -> "Settings":
        load_dotenv(project_dir / ".env")
        load_dotenv(Path.cwd() / ".env")

        bot_token = os.getenv("BOT_TOKEN", "").strip()
        if not bot_token:
            print("ERROR: set BOT_TOKEN in .env", file=sys.stderr)
            sys.exit(1)

        db_raw = os.getenv("DATABASE_PATH", "data/bot.sqlite3").strip() or "data/bot.sqlite3"
        database_path = Path(db_raw)
        if not database_path.is_absolute():
            database_path = project_dir / database_path

        return cls(
            project_dir=project_dir,
            bot_token=bot_token,
            api_url=f"https://api.telegram.org/bot{bot_token}",
            database_path=database_path,
            owner_ids=parse_owner_ids(os.getenv("OWNER_IDS", "")),
            allow_self_add_channels=parse_bool(os.getenv("ALLOW_SELF_ADD_CHANNELS", "true"), True),
            api_timeout=int(os.getenv("API_TIMEOUT", "35")),
            poll_timeout=int(os.getenv("POLL_TIMEOUT", "25")),
            preview_ttl_hours=int(os.getenv("PREVIEW_TTL_HOURS", "24")),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        )
