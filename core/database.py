from sqlalchemy import inspect, text
from core.config import engine, IS_POSTGRES

def q(sql, params=None):
    with engine.connect() as conn:
        result = conn.execute(text(sql), params or {})
        return [dict(row._mapping) for row in result]


def scalar(sql, params=None):
    with engine.connect() as conn:
        return conn.execute(text(sql), params or {}).scalar()


def ex(sql, params=None):
    with engine.begin() as conn:
        return conn.execute(text(sql), params or {})


def insert_id(sql, params):
    with engine.begin() as conn:
        if IS_POSTGRES:
            result = conn.execute(text(sql + " RETURNING id"), params)
            return int(result.scalar())
        result = conn.execute(text(sql), params)
        return int(result.lastrowid)


def table_columns(table_name):
    try:
        return {col["name"] for col in inspect(engine).get_columns(table_name)}
    except Exception:
        return set()


def add_missing_column(table_name, column_name, definition):
    if column_name not in table_columns(table_name):
        ex(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")


def init_db():
    id_def = "SERIAL PRIMARY KEY" if IS_POSTGRES else "INTEGER PRIMARY KEY AUTOINCREMENT"
    ddl = [
        f"""CREATE TABLE IF NOT EXISTS users(
            id {id_def},
            name VARCHAR(120) NOT NULL,
            email VARCHAR(180) NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role VARCHAR(30) NOT NULL DEFAULT 'operador',
            active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        f"""CREATE TABLE IF NOT EXISTS app_settings(
            setting_key VARCHAR(120) PRIMARY KEY,
            setting_value TEXT NOT NULL
        )""",
        f"""CREATE TABLE IF NOT EXISTS auth_sessions(
            id {id_def},
            user_id INTEGER NOT NULL,
            token_hash VARCHAR(64) NOT NULL UNIQUE,
            expires_at TIMESTAMP NOT NULL,
            revoked BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        f"""CREATE TABLE IF NOT EXISTS seasons(
            id {id_def},
            name VARCHAR(160) NOT NULL,
            crop VARCHAR(50) NOT NULL,
            area_ha NUMERIC(14,2) NOT NULL,
            cost_ha NUMERIC(14,2) NOT NULL,
            yield_sc_ha NUMERIC(14,2) NOT NULL,
            margin_pct NUMERIC(8,2) NOT NULL DEFAULT 20,
            actual_production_sc NUMERIC(16,2),
            harvest_date DATE,
            production_result VARCHAR(30),
            production_reason VARCHAR(120),
            production_notes TEXT,
            active BOOLEAN NOT NULL DEFAULT TRUE,
            created_by INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        f"""CREATE TABLE IF NOT EXISTS machinery(
            id {id_def},
            name VARCHAR(180) NOT NULL,
            brand VARCHAR(120),
            model VARCHAR(120),
            year INTEGER,
            serial_number VARCHAR(120),
            acquisition_date DATE,
            acquisition_value NUMERIC(16,2),
            contract_id INTEGER,
            status VARCHAR(40) NOT NULL DEFAULT 'ativo',
            notes TEXT,
            created_by INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        f"""CREATE TABLE IF NOT EXISTS purchase_contracts(
            id {id_def},
            description VARCHAR(240) NOT NULL,
            supplier VARCHAR(180),
            category VARCHAR(80) NOT NULL DEFAULT 'Máquinas',
            total_value NUMERIC(16,2) NOT NULL,
            purchase_date DATE,
            notes TEXT,
            status VARCHAR(30) NOT NULL DEFAULT 'aberto',
            created_by INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        f"""CREATE TABLE IF NOT EXISTS commitments(
            id {id_def},
            contract_id INTEGER,
            installment_no INTEGER,
            season_id INTEGER,
            category VARCHAR(80) NOT NULL,
            description VARCHAR(240) NOT NULL,
            supplier VARCHAR(180),
            total_value NUMERIC(16,2) NOT NULL,
            purchase_date DATE,
            due_date DATE NOT NULL,
            payment_crop VARCHAR(80),
            notes TEXT,
            status VARCHAR(30) NOT NULL DEFAULT 'aberto',
            created_by INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        f"""CREATE TABLE IF NOT EXISTS sales(
            id {id_def},
            season_id INTEGER NOT NULL,
            sale_date DATE NOT NULL,
            payment_date DATE,
            quantity_sc NUMERIC(16,2) NOT NULL,
            price_sc NUMERIC(16,2) NOT NULL,
            buyer VARCHAR(180),
            commitment_id INTEGER,
            notes TEXT,
            created_by INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        f"""CREATE TABLE IF NOT EXISTS quotes(
            id {id_def},
            crop VARCHAR(50) NOT NULL,
            price_sc NUMERIC(16,2) NOT NULL,
            source VARCHAR(180),
            quoted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            created_by INTEGER,
            region VARCHAR(120),
            quote_type VARCHAR(30),
            source_url TEXT
        )""",
        f"""CREATE TABLE IF NOT EXISTS payments(
            id {id_def},
            commitment_id INTEGER NOT NULL,
            payment_date DATE NOT NULL,
            amount NUMERIC(16,2) NOT NULL,
            notes TEXT,
            created_by INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        f"""CREATE TABLE IF NOT EXISTS activity_log(
            id {id_def},
            user_id INTEGER,
            action VARCHAR(100) NOT NULL,
            entity VARCHAR(80) NOT NULL,
            entity_id INTEGER,
            details TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        f"""CREATE TABLE IF NOT EXISTS pilot_feedback(
            id {id_def},
            user_id INTEGER,
            module VARCHAR(80) NOT NULL,
            feedback_type VARCHAR(50) NOT NULL,
            priority VARCHAR(30) NOT NULL,
            description TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
    ]

    with engine.begin() as conn:
        for statement in ddl:
            conn.execute(text(statement))
        for statement in [
            "CREATE INDEX IF NOT EXISTS idx_sales_season ON sales(season_id)",
            "CREATE INDEX IF NOT EXISTS idx_commitments_due ON commitments(due_date)",
            "CREATE INDEX IF NOT EXISTS idx_quotes_crop_date ON quotes(crop, quoted_at)",
        ]:
            conn.execute(text(statement))

    # Migrações para quem já estava usando a versão anterior
    timestamp_column = "TIMESTAMP DEFAULT CURRENT_TIMESTAMP" if IS_POSTGRES else "TIMESTAMP"
    add_missing_column("users", "created_at", timestamp_column)
    add_missing_column("seasons", "created_at", timestamp_column)
    add_missing_column("seasons", "actual_production_sc", "NUMERIC(16,2)")
    add_missing_column("seasons", "harvest_date", "DATE")
    add_missing_column("seasons", "production_result", "VARCHAR(30)")
    add_missing_column("seasons", "production_reason", "VARCHAR(120)")
    add_missing_column("seasons", "production_notes", "TEXT")
    add_missing_column("commitments", "status", "VARCHAR(30) DEFAULT 'aberto'")
    add_missing_column("commitments", "created_at", timestamp_column)
    add_missing_column("commitments", "purchase_date", "DATE")
    add_missing_column("commitments", "contract_id", "INTEGER")
    add_missing_column("commitments", "installment_no", "INTEGER")
    add_missing_column("sales", "created_at", timestamp_column)
    add_missing_column("sales", "payment_date", "DATE")
    # Mantém os metadados do Mercado Regional compatíveis entre SQLite e PostgreSQL.
    add_missing_column("quotes", "region", "VARCHAR(120)")
    add_missing_column("quotes", "quote_type", "VARCHAR(30)")
    add_missing_column("quotes", "source_url", "TEXT")


def log_action(user_id, action, entity, entity_id=None, details=""):
    ex(
        """INSERT INTO activity_log(user_id,action,entity,entity_id,details)
           VALUES(:u,:a,:e,:i,:d)""",
        {"u": user_id, "a": action, "e": entity, "i": entity_id, "d": details},
    )
