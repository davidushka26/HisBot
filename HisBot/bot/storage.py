from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PendingDuel:
    code: str
    creator_id: int
    created_at: int


@dataclass(frozen=True)
class Duel:
    id: int
    code: str
    player1_id: int
    player2_id: int
    score1: int
    score2: int
    question_ids: list[int]
    current_index: int
    current_winner_id: int | None
    status: str
    created_at: int
    finished_at: int | None


class Storage:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._lock = asyncio.Lock()
        self._connection = sqlite3.connect(db_path)
        self._connection.row_factory = sqlite3.Row

    async def init(self) -> None:
        async with self._lock:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    first_name TEXT NOT NULL,
                    username TEXT,
                    updated_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS pending_duels (
                    code TEXT PRIMARY KEY,
                    creator_id INTEGER NOT NULL,
                    created_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS duels (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    code TEXT NOT NULL UNIQUE,
                    player1_id INTEGER NOT NULL,
                    player2_id INTEGER NOT NULL,
                    score1 INTEGER NOT NULL DEFAULT 0,
                    score2 INTEGER NOT NULL DEFAULT 0,
                    question_ids TEXT NOT NULL,
                    current_index INTEGER NOT NULL DEFAULT 0,
                    current_winner_id INTEGER,
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at INTEGER NOT NULL,
                    finished_at INTEGER
                );

                CREATE TABLE IF NOT EXISTS duel_messages (
                    duel_id INTEGER NOT NULL,
                    question_index INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL,
                    PRIMARY KEY (duel_id, question_index, user_id)
                );

                CREATE TABLE IF NOT EXISTS duel_answers (
                    duel_id INTEGER NOT NULL,
                    question_index INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    selected_option INTEGER NOT NULL,
                    is_correct INTEGER NOT NULL,
                    answered_at INTEGER NOT NULL,
                    PRIMARY KEY (duel_id, question_index, user_id)
                );
                """
            )
            self._connection.commit()

    async def close(self) -> None:
        async with self._lock:
            self._connection.close()

    async def upsert_user(self, user_id: int, first_name: str, username: str | None) -> None:
        async with self._lock:
            self._connection.execute(
                """
                INSERT INTO users (user_id, first_name, username, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    first_name = excluded.first_name,
                    username = excluded.username,
                    updated_at = excluded.updated_at
                """,
                (user_id, first_name, username, int(time.time())),
            )
            self._connection.commit()

    async def get_user_name(self, user_id: int) -> str:
        async with self._lock:
            row = self._connection.execute(
                "SELECT first_name, username FROM users WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        if not row:
            return f"Игрок {user_id}"
        if row["username"]:
            return f"@{row['username']}"
        return row["first_name"]

    async def create_pending_duel(self, code: str, creator_id: int) -> None:
        async with self._lock:
            self._connection.execute(
                "INSERT INTO pending_duels (code, creator_id, created_at) VALUES (?, ?, ?)",
                (code, creator_id, int(time.time())),
            )
            self._connection.commit()

    async def get_pending_duel(self, code: str) -> PendingDuel | None:
        async with self._lock:
            row = self._connection.execute(
                "SELECT code, creator_id, created_at FROM pending_duels WHERE code = ?",
                (code,),
            ).fetchone()
        if not row:
            return None
        return PendingDuel(code=row["code"], creator_id=row["creator_id"], created_at=row["created_at"])

    async def delete_pending_duel(self, code: str) -> None:
        async with self._lock:
            self._connection.execute("DELETE FROM pending_duels WHERE code = ?", (code,))
            self._connection.commit()

    async def create_duel(
        self,
        code: str,
        player1_id: int,
        player2_id: int,
        question_ids: list[int],
    ) -> Duel:
        async with self._lock:
            cursor = self._connection.execute(
                """
                INSERT INTO duels (
                    code, player1_id, player2_id, question_ids, created_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (code, player1_id, player2_id, json.dumps(question_ids), int(time.time())),
            )
            self._connection.commit()
            duel_id = cursor.lastrowid
        duel = await self.get_duel(duel_id)
        if duel is None:
            raise RuntimeError("Created duel was not found")
        return duel

    async def get_duel(self, duel_id: int) -> Duel | None:
        async with self._lock:
            row = self._connection.execute(
                """
                SELECT id, code, player1_id, player2_id, score1, score2, question_ids,
                       current_index, current_winner_id, status, created_at, finished_at
                FROM duels
                WHERE id = ?
                """,
                (duel_id,),
            ).fetchone()
        if not row:
            return None
        return self._row_to_duel(row)

    async def get_recent_question_ids_for_players(
        self,
        player1_id: int,
        player2_id: int,
        limit: int = 30,
    ) -> set[int]:
        first, second = sorted((player1_id, player2_id))
        async with self._lock:
            rows = self._connection.execute(
                """
                SELECT question_ids
                FROM duels
                WHERE ((player1_id = ? AND player2_id = ?) OR (player1_id = ? AND player2_id = ?))
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (first, second, second, first, limit),
            ).fetchall()

        used: set[int] = set()
        for row in rows:
            used.update(json.loads(row["question_ids"]))
        return used

    async def record_duel_message(
        self,
        duel_id: int,
        question_index: int,
        user_id: int,
        message_id: int,
    ) -> None:
        async with self._lock:
            self._connection.execute(
                """
                INSERT INTO duel_messages (duel_id, question_index, user_id, message_id)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(duel_id, question_index, user_id) DO UPDATE SET
                    message_id = excluded.message_id
                """,
                (duel_id, question_index, user_id, message_id),
            )
            self._connection.commit()

    async def get_duel_messages(self, duel_id: int, question_index: int) -> dict[int, int]:
        async with self._lock:
            rows = self._connection.execute(
                """
                SELECT user_id, message_id
                FROM duel_messages
                WHERE duel_id = ? AND question_index = ?
                """,
                (duel_id, question_index),
            ).fetchall()
        return {row["user_id"]: row["message_id"] for row in rows}

    async def record_duel_answer(
        self,
        duel_id: int,
        question_index: int,
        user_id: int,
        selected_option: int,
        is_correct: bool,
    ) -> bool:
        async with self._lock:
            cursor = self._connection.execute(
                """
                INSERT OR IGNORE INTO duel_answers (
                    duel_id, question_index, user_id, selected_option, is_correct, answered_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    duel_id,
                    question_index,
                    user_id,
                    selected_option,
                    1 if is_correct else 0,
                    int(time.time()),
                ),
            )
            self._connection.commit()
        return cursor.rowcount > 0

    async def get_duel_answers(self, duel_id: int, question_index: int) -> dict[int, bool]:
        async with self._lock:
            rows = self._connection.execute(
                """
                SELECT user_id, is_correct
                FROM duel_answers
                WHERE duel_id = ? AND question_index = ?
                """,
                (duel_id, question_index),
            ).fetchall()
        return {row["user_id"]: bool(row["is_correct"]) for row in rows}

    async def mark_question_winner(self, duel_id: int, winner_id: int) -> Duel | None:
        duel = await self.get_duel(duel_id)
        if duel is None or duel.status != "active" or duel.current_winner_id is not None:
            return duel

        score1_delta = 1 if winner_id == duel.player1_id else 0
        score2_delta = 1 if winner_id == duel.player2_id else 0
        async with self._lock:
            self._connection.execute(
                """
                UPDATE duels
                SET current_winner_id = ?,
                    score1 = score1 + ?,
                    score2 = score2 + ?
                WHERE id = ? AND current_winner_id IS NULL AND status = 'active'
                """,
                (winner_id, score1_delta, score2_delta, duel_id),
            )
            self._connection.commit()
        return await self.get_duel(duel_id)

    async def advance_duel(self, duel_id: int) -> Duel | None:
        duel = await self.get_duel(duel_id)
        if duel is None or duel.status != "active":
            return duel

        next_index = duel.current_index + 1
        if next_index >= len(duel.question_ids):
            async with self._lock:
                self._connection.execute(
                    """
                    UPDATE duels
                    SET status = 'finished', finished_at = ?
                    WHERE id = ?
                    """,
                    (int(time.time()), duel_id),
                )
                self._connection.commit()
        else:
            async with self._lock:
                self._connection.execute(
                    """
                    UPDATE duels
                    SET current_index = ?, current_winner_id = NULL
                    WHERE id = ?
                    """,
                    (next_index, duel_id),
                )
                self._connection.commit()
        return await self.get_duel(duel_id)

    async def cancel_pending_duel(self, code: str, creator_id: int) -> bool:
        async with self._lock:
            cursor = self._connection.execute(
                "DELETE FROM pending_duels WHERE code = ? AND creator_id = ?",
                (code, creator_id),
            )
            self._connection.commit()
        return cursor.rowcount > 0

    @staticmethod
    def _row_to_duel(row: sqlite3.Row) -> Duel:
        return Duel(
            id=row["id"],
            code=row["code"],
            player1_id=row["player1_id"],
            player2_id=row["player2_id"],
            score1=row["score1"],
            score2=row["score2"],
            question_ids=json.loads(row["question_ids"]),
            current_index=row["current_index"],
            current_winner_id=row["current_winner_id"],
            status=row["status"],
            created_at=row["created_at"],
            finished_at=row["finished_at"],
        )
