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
