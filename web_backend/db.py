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


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
