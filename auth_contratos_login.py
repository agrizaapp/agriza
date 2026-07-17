import hashlib
import secrets
from datetime import datetime, timedelta

from core.config import IS_POSTGRES
from core.database import q, scalar, ex, insert_id
from core.security import hpw

def setup_complete():
    value = scalar(
        "SELECT setting_value FROM app_settings WHERE setting_key='setup_complete'"
    )
    return value == "1"


def save_setting(key, value):
    if IS_POSTGRES:
        ex(
            """INSERT INTO app_settings(setting_key,setting_value)
               VALUES(:k,:v)
               ON CONFLICT(setting_key) DO UPDATE SET setting_value=EXCLUDED.setting_value""",
            {"k": key, "v": value},
        )
    else:
        ex(
            "INSERT OR REPLACE INTO app_settings(setting_key,setting_value) VALUES(:k,:v)",
            {"k": key, "v": value},
        )


def create_initial_admin(name, email, password):
    email = email.strip().lower()
    existing = q("SELECT id FROM users WHERE lower(email)=:e", {"e": email})
    if existing:
        admin_id = existing[0]["id"]
        ex(
            """UPDATE users SET name=:n,password_hash=:p,role='admin',active=TRUE
               WHERE id=:id""",
            {"n": name.strip(), "p": hpw(password), "id": admin_id},
        )
    else:
        admin_id = insert_id(
            """INSERT INTO users(name,email,password_hash,role,active)
               VALUES(:n,:e,:p,'admin',TRUE)""",
            {"n": name.strip(), "e": email, "p": hpw(password)},
        )
    save_setting("setup_complete", "1")
    return admin_id


def _token_hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_persistent_session(user_id, days=365):
    """Cria uma sessão revogável para lembrar o login neste dispositivo."""
    token = secrets.token_urlsafe(32)
    expires_at = datetime.utcnow() + timedelta(days=days)
    ex(
        """INSERT INTO auth_sessions(user_id,token_hash,expires_at,revoked)
           VALUES(:u,:h,:e,FALSE)""",
        {
            "u": user_id,
            "h": _token_hash(token),
            "e": expires_at,
        },
    )
    return token, expires_at


def get_user_from_session_token(token):
    if not token:
        return None

    rows = q(
        """SELECT users.*
           FROM auth_sessions
           JOIN users ON users.id=auth_sessions.user_id
           WHERE auth_sessions.token_hash=:h
             AND auth_sessions.revoked=FALSE
             AND auth_sessions.expires_at > CURRENT_TIMESTAMP
             AND users.active=TRUE
           ORDER BY auth_sessions.id DESC
           LIMIT 1""",
        {"h": _token_hash(token)},
    )
    if not rows:
        return None

    return {
        key: value
        for key, value in rows[0].items()
        if key != "password_hash"
    }


def revoke_persistent_session(token):
    if not token:
        return
    ex(
        """UPDATE auth_sessions
           SET revoked=TRUE
           WHERE token_hash=:h""",
        {"h": _token_hash(token)},
    )


def cleanup_expired_sessions():
    ex(
        """DELETE FROM auth_sessions
           WHERE revoked=TRUE OR expires_at <= CURRENT_TIMESTAMP"""
    )
