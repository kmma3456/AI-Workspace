"""관리자 계정·세션 (SQLite)."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.db import Database

SESSION_COOKIE = "admin_session"
SESSION_HEADER = "X-Admin-Session"
SESSION_DAYS = 30
PBKDF2_ITERATIONS = 260_000


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash_password(password: str, salt: bytes) -> str:
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return digest.hex()


def hash_password(password: str) -> tuple[str, str]:
    salt = secrets.token_bytes(16)
    return salt.hex(), _hash_password(password, salt)


def verify_password(password: str, salt_hex: str, stored_hash: str) -> bool:
    salt = bytes.fromhex(salt_hex)
    candidate = _hash_password(password, salt)
    return hmac.compare_digest(candidate, stored_hash)


@dataclass
class AdminUser:
    id: int
    email: str
    created_at: str
    disabled: bool


class AdminAuth:
    def __init__(self, db: Database):
        self.db = db
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS admin_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                password_salt TEXT NOT NULL,
                created_at TEXT NOT NULL,
                disabled INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS admin_sessions (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES admin_users(id)
            );
            CREATE INDEX IF NOT EXISTS idx_admin_sessions_user ON admin_sessions(user_id);
            """
        )

    def ensure_user(self, email: str, password: str) -> AdminUser | None:
        email = email.strip().lower()
        if self.get_user_by_email(email):
            return None
        return self.create_user(email, password)

    def create_user(self, email: str, password: str) -> AdminUser:
        email = email.strip().lower()
        if not email or "@" not in email:
            raise ValueError("invalid email")
        if len(password) < 8:
            raise ValueError("password too short")
        salt_hex, pwd_hash = hash_password(password)
        created = _now().isoformat()
        user_id = self.db.execute_returning_id(
            "INSERT INTO admin_users (email, password_hash, password_salt, created_at, disabled) VALUES (?, ?, ?, ?, 0)",
            (email, pwd_hash, salt_hex, created),
        )
        return AdminUser(id=user_id, email=email, created_at=created, disabled=False)

    def get_user_by_email(self, email: str) -> AdminUser | None:
        row = self.db.fetchone(
            "SELECT id, email, created_at, disabled FROM admin_users WHERE email = ? COLLATE NOCASE",
            (email.strip().lower(),),
        )
        return self._row_user(row) if row else None

    def get_user_by_id(self, user_id: int) -> AdminUser | None:
        row = self.db.fetchone(
            "SELECT id, email, created_at, disabled FROM admin_users WHERE id = ?",
            (user_id,),
        )
        return self._row_user(row) if row else None

    def list_users(self) -> list[AdminUser]:
        rows = self.db.fetchall(
            "SELECT id, email, created_at, disabled FROM admin_users ORDER BY id ASC"
        )
        return [self._row_user(r) for r in rows]

    def set_disabled(self, user_id: int, disabled: bool) -> bool:
        cur = self.db.execute(
            "UPDATE admin_users SET disabled = ? WHERE id = ?",
            (1 if disabled else 0, user_id),
        )
        return cur > 0

    def set_password(self, user_id: int, password: str) -> None:
        if len(password) < 8:
            raise ValueError("password too short")
        salt_hex, pwd_hash = hash_password(password)
        self.db.execute(
            "UPDATE admin_users SET password_hash = ?, password_salt = ? WHERE id = ?",
            (pwd_hash, salt_hex, user_id),
        )
        self.revoke_sessions_for_user(user_id)

    def authenticate(self, email: str, password: str) -> AdminUser | None:
        row = self.db.fetchone(
            "SELECT id, email, created_at, disabled, password_hash, password_salt FROM admin_users WHERE email = ? COLLATE NOCASE",
            (email.strip().lower(),),
        )
        if not row or row["disabled"]:
            return None
        if not verify_password(password, row["password_salt"], row["password_hash"]):
            return None
        return AdminUser(id=row["id"], email=row["email"], created_at=row["created_at"], disabled=False)

    def create_session(self, user_id: int) -> str:
        token = secrets.token_urlsafe(32)
        now = _now()
        expires = now + timedelta(days=SESSION_DAYS)
        self.db.execute(
            "INSERT INTO admin_sessions (token, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (token, user_id, now.isoformat(), expires.isoformat()),
        )
        return token

    def resolve_token(self, token: str) -> AdminUser | None:
        token = (token or "").strip()
        if not token:
            return None
        row = self.db.fetchone(
            """
            SELECT u.id, u.email, u.created_at, u.disabled, s.expires_at
            FROM admin_sessions s
            JOIN admin_users u ON u.id = s.user_id
            WHERE s.token = ?
            """,
            (token,),
        )
        if not row or row["disabled"]:
            return None
        if datetime.fromisoformat(row["expires_at"]) < _now():
            self.revoke_token(token)
            return None
        return AdminUser(id=row["id"], email=row["email"], created_at=row["created_at"], disabled=False)

    def revoke_token(self, token: str) -> None:
        self.db.execute("DELETE FROM admin_sessions WHERE token = ?", (token.strip(),))

    def revoke_sessions_for_user(self, user_id: int) -> None:
        self.db.execute("DELETE FROM admin_sessions WHERE user_id = ?", (user_id,))

    def count_users(self) -> int:
        row = self.db.fetchone("SELECT COUNT(*) AS n FROM admin_users")
        return int(row["n"]) if row else 0

    @staticmethod
    def _row_user(row) -> AdminUser:
        return AdminUser(
            id=row["id"],
            email=row["email"],
            created_at=row["created_at"],
            disabled=bool(row["disabled"]),
        )

    def user_public(self, user: AdminUser) -> dict:
        return {"id": user.id, "email": user.email, "created_at": user.created_at, "disabled": user.disabled}
