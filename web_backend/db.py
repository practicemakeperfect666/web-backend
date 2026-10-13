"""Engine + session factory for the web backend (read-only side).

The trading process uses MonitorWriter (writer.py) with its own engine;
the backend reads the same SQLite file via this module. The path comes
from MONITOR_DB_PATH (a fixed absolute path shared by all processes).
"""

import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from .models import Base

DB_PATH = os.getenv(
    "MONITOR_DB_PATH", os.path.join(os.path.expanduser("~"), "vnpy-data", "monitor.db")
)
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

engine = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={"check_same_thread": False},
)

with engine.connect() as conn:
    conn.exec_driver_sql("PRAGMA journal_mode=WAL")
    conn.exec_driver_sql("PRAGMA busy_timeout=5000")

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def init_db() -> None:
    Base.metadata.create_all(engine)
    _migrate()


def _migrate() -> None:
    """Migrate existing tables (create_all does not alter existing): add
    queue/cancel delay cols, drop legacy delay_ms (NOT NULL in old schemas;
    once the model stops writing it, INSERTs fail the constraint)."""
    with engine.begin() as conn:
        cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(orders)")}
        for col in ("queue_delay_ms", "cancel_delay_ms"):
            if col not in cols:
                conn.exec_driver_sql(
                    f"ALTER TABLE orders ADD COLUMN {col} FLOAT DEFAULT 0"
                )
        if "delay_ms" in cols:
            conn.exec_driver_sql("ALTER TABLE orders DROP COLUMN delay_ms")

        tcols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(trades)")}
        if "delay_ms" in tcols:
            conn.exec_driver_sql("ALTER TABLE trades DROP COLUMN delay_ms")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
