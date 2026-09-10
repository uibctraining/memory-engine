"""
Memory Engine — Database Factory
Per-user SQLite with central PostgreSQL.

Pattern: each user gets their own SQLite file.
Factory returns the right engine based on user_id.
"""

import os
from contextlib import contextmanager
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, Session
from app.models.schema import Base

# ─── Configuration ────────────────────────────────────────

DATA_DIR = os.getenv("ME_DATA_DIR", os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'users'))
CENTRAL_DB_URL = os.getenv("ME_CENTRAL_DB", "postgresql://localhost/memory_engine_central")

# In-memory cache of engines per user (avoids recreating connections)
_engine_cache: dict = {}
_central_engine = None


# ═══ Per-User SQLite Factory ══════════════════════════════

def get_user_db_path(user_id: str) -> str:
    """Get the SQLite file path for a user."""
    safe_id = user_id.replace('/', '_').replace('\\', '_').replace('..', '_')
    return os.path.join(DATA_DIR, safe_id, 'memory.db')


def get_user_engine(user_id: str):
    """Get or create SQLAlchemy engine for a user's SQLite database."""
    if user_id in _engine_cache:
        return _engine_cache[user_id]

    db_path = get_user_db_path(user_id)
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    url = f"sqlite:///{db_path}"
    engine = create_engine(url, connect_args={"check_same_thread": False}, echo=False)

    # Enable WAL mode for better concurrent read performance
    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA cache_size=-64000")  # 64MB cache
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    # Create tables if they don't exist
    Base.metadata.create_all(bind=engine)

    _engine_cache[user_id] = engine
    return engine


def get_user_session(user_id: str) -> Session:
    """Get a new database session for a user."""
    engine = get_user_engine(user_id)
    SessionLocal = sessionmaker(bind=engine)
    return SessionLocal()


@contextmanager
def user_db(user_id: str):
    """Context manager for user database sessions."""
    session = get_user_session(user_id)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def create_user_db(user_id: str) -> str:
    """
    Create a new user database.
    Returns the database path.
    Called on user registration.
    """
    db_path = get_user_db_path(user_id)
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)

    # Initialize default config
    from app.models.schema import UserConfig
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    defaults = [
        UserConfig(key='language', value='auto'),
        UserConfig(key='weight_threshold', value=0.01),
        UserConfig(key='sync_enabled', value=True),
        UserConfig(key='archive_after_days', value=30),
    ]
    session.add_all(defaults)
    session.commit()
    session.close()

    _engine_cache[user_id] = engine
    return db_path


def delete_user_db(user_id: str):
    """Delete a user's database. GDPR compliance."""
    import shutil
    db_path = get_user_db_path(user_id)
    user_dir = os.path.dirname(db_path)

    # Remove from cache
    _engine_cache.pop(user_id, None)

    # Remove directory
    if os.path.exists(user_dir):
        shutil.rmtree(user_dir)


def list_users() -> list:
    """List all user IDs that have databases."""
    if not os.path.exists(DATA_DIR):
        return []
    return [
        d for d in os.listdir(DATA_DIR)
        if os.path.isfile(os.path.join(DATA_DIR, d, 'memory.db'))
    ]


# ═══ Central PostgreSQL ══════════════════════════════════

def get_central_engine():
    """Get the central PostgreSQL engine (singleton)."""
    global _central_engine
    if _central_engine is None:
        _central_engine = create_engine(CENTRAL_DB_URL, echo=False)
    return _central_engine


@contextmanager
def central_db():
    """Context manager for central database sessions."""
    engine = get_central_engine()
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
