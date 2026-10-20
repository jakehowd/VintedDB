"""SQLAlchemy database engine, session factory, and base class."""

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import DATABASE_URL

from sqlalchemy import event

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False, "timeout": 30},  # Required for SQLite
    echo=False,
)

@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    """Enable SQLite WAL mode and busy timeout for concurrent read/write stability."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""
    pass


def get_db():
    """FastAPI dependency — yields a DB session and closes it after the request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ensure_db_schema(target_engine=engine):
    """Ensure newly added columns exist in existing SQLite tables."""
    with target_engine.connect() as conn:
        cursor = conn.connection.cursor()
        
        # 1. tracked_searches columns
        ts_cols = [c[1] for c in cursor.execute("PRAGMA table_info(tracked_searches)").fetchall()]
        if ts_cols:
            if "antirep_settings" not in ts_cols:
                cursor.execute("ALTER TABLE tracked_searches ADD COLUMN antirep_settings TEXT;")
            if "exclude_child_sizes" not in ts_cols:
                cursor.execute("ALTER TABLE tracked_searches ADD COLUMN exclude_child_sizes BOOLEAN DEFAULT 0;")
        
        # 2. sold_items columns
        si_cols = [c[1] for c in cursor.execute("PRAGMA table_info(sold_items)").fetchall()]
        if si_cols:
            if "seller_id" not in si_cols:
                cursor.execute("ALTER TABLE sold_items ADD COLUMN seller_id TEXT;")
            if "seller_username" not in si_cols:
                cursor.execute("ALTER TABLE sold_items ADD COLUMN seller_username TEXT;")
            if "condition" not in si_cols:
                cursor.execute("ALTER TABLE sold_items ADD COLUMN condition TEXT;")
            if "status" not in si_cols:
                cursor.execute("ALTER TABLE sold_items ADD COLUMN status TEXT;")
            if "is_sold" not in si_cols:
                cursor.execute("ALTER TABLE sold_items ADD COLUMN is_sold BOOLEAN DEFAULT 0;")
            if "is_replica" not in si_cols:
                cursor.execute("ALTER TABLE sold_items ADD COLUMN is_replica BOOLEAN DEFAULT 0;")
            if "replica_reason" not in si_cols:
                cursor.execute("ALTER TABLE sold_items ADD COLUMN replica_reason TEXT;")
            if "is_child_size" not in si_cols:
                cursor.execute("ALTER TABLE sold_items ADD COLUMN is_child_size BOOLEAN DEFAULT 0;")
            if "child_size_reason" not in si_cols:
                cursor.execute("ALTER TABLE sold_items ADD COLUMN child_size_reason TEXT;")
            if "item_created_at" not in si_cols:
                cursor.execute("ALTER TABLE sold_items ADD COLUMN item_created_at DATETIME;")

            # 3. High-performance indexes for search, status, replica and child size lookups
            cursor.execute("CREATE INDEX IF NOT EXISTS ix_sold_items_search_captured ON sold_items (tracked_search_id, captured_at DESC);")
            cursor.execute("CREATE INDEX IF NOT EXISTS ix_sold_items_replica ON sold_items (is_replica);")
            cursor.execute("CREATE INDEX IF NOT EXISTS ix_sold_items_sold ON sold_items (is_sold);")
            cursor.execute("CREATE INDEX IF NOT EXISTS ix_sold_items_child ON sold_items (is_child_size);")
                
        conn.connection.commit()

