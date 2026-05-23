from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


@dataclass(frozen=True)
class Settings:
    bot_token: str
    db_path: Path

    @classmethod
    def load(cls) -> "Settings":
        _load_dotenv(BASE_DIR / ".env")

        bot_token = os.getenv("BOT_TOKEN", "").strip()
        if not bot_token:
            raise RuntimeError(
                "BOT_TOKEN is empty. Create .env from .env.example and paste your BotFather token."
            )

        db_path = Path(os.getenv("BOT_DB_PATH", "hisbot.sqlite3"))
        if not db_path.is_absolute():
            db_path = BASE_DIR / db_path

        return cls(bot_token=bot_token, db_path=db_path)

