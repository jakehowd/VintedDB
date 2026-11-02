"""SQLAlchemy database engine, session factory, and base class."""

from datetime import datetime, timezone
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
    """Ensure newly added columns and user auth tables exist in existing SQLite database."""
    with target_engine.connect() as conn:
        cursor = conn.connection.cursor()
        
        # 0. Ensure users and user_sessions tables exist
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                is_admin BOOLEAN DEFAULT 0 NOT NULL,
                created_at DATETIME
            );
        """)
        cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_username ON users (username);")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                session_token TEXT NOT NULL UNIQUE,
                created_at DATETIME,
                expires_at DATETIME NOT NULL
            );
        """)
        cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_user_sessions_token ON user_sessions (session_token);")
        cursor.execute("CREATE INDEX IF NOT EXISTS ix_user_sessions_user ON user_sessions (user_id);")

        # Seed ADMIN user if not present (Username: ADMIN, Password: ADMIN)
        from .auth import hash_password
        admin_row = cursor.execute("SELECT id FROM users WHERE username = 'ADMIN' OR username = 'admin'").fetchone()
        if not admin_row:
            admin_hash = hash_password("ADMIN")
            cursor.execute(
                "INSERT INTO users (username, password_hash, is_admin, created_at) VALUES ('ADMIN', ?, 1, ?)",
                (admin_hash, datetime.now(timezone.utc)),
            )
            admin_id = cursor.lastrowid
        else:
            admin_id = admin_row[0]

        # 0b. Ensure product_variants table exists
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS product_variants (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tracked_search_id INTEGER NOT NULL REFERENCES tracked_searches(id) ON DELETE CASCADE,
                parent_variant_id INTEGER REFERENCES product_variants(id) ON DELETE CASCADE,
                variant_slug TEXT NOT NULL,
                variant_name TEXT NOT NULL,
                dimension TEXT DEFAULT 'Model',
                modality TEXT DEFAULT 'visual',
                short_description TEXT,
                resale_tier TEXT DEFAULT 'STANDARD',
                est_market_value_gbp REAL,
                visual_cues TEXT,
                ocr_signatures TEXT,
                text_keywords TEXT,
                negative_keywords TEXT,
                prototype_embedding TEXT,
                training_sample_count INTEGER DEFAULT 0,
                target_max_price REAL,
                is_sniping_enabled BOOLEAN DEFAULT 1,
                created_at DATETIME,
                updated_at DATETIME
            );
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS ix_product_variants_search ON product_variants (tracked_search_id);")
        pv_cols = [c[1] for c in cursor.execute("PRAGMA table_info(product_variants)").fetchall()]
        if pv_cols:
            if "parent_variant_id" not in pv_cols:
                cursor.execute("ALTER TABLE product_variants ADD COLUMN parent_variant_id INTEGER REFERENCES product_variants(id) ON DELETE CASCADE;")
            if "dimension" not in pv_cols:
                cursor.execute("ALTER TABLE product_variants ADD COLUMN dimension TEXT DEFAULT 'Model';")
            if "modality" not in pv_cols:
                cursor.execute("ALTER TABLE product_variants ADD COLUMN modality TEXT DEFAULT 'visual';")
        cursor.execute("CREATE INDEX IF NOT EXISTS ix_product_variants_parent ON product_variants (parent_variant_id);")

        # 1. tracked_searches columns
        ts_cols = [c[1] for c in cursor.execute("PRAGMA table_info(tracked_searches)").fetchall()]
        if ts_cols:
            if "user_id" not in ts_cols:
                cursor.execute("ALTER TABLE tracked_searches ADD COLUMN user_id INTEGER REFERENCES users(id) ON DELETE CASCADE;")
            if "antirep_settings" not in ts_cols:
                cursor.execute("ALTER TABLE tracked_searches ADD COLUMN antirep_settings TEXT;")
            if "exclude_child_sizes" not in ts_cols:
                cursor.execute("ALTER TABLE tracked_searches ADD COLUMN exclude_child_sizes BOOLEAN DEFAULT 0;")
            if "variants_enabled" not in ts_cols:
                cursor.execute("ALTER TABLE tracked_searches ADD COLUMN variants_enabled BOOLEAN DEFAULT 0;")
            if "catalogue_summary" not in ts_cols:
                cursor.execute("ALTER TABLE tracked_searches ADD COLUMN catalogue_summary TEXT;")
            
            # Migrate any unowned searches to the ADMIN user
            cursor.execute("UPDATE tracked_searches SET user_id = ? WHERE user_id IS NULL", (admin_id,))
            cursor.execute("CREATE INDEX IF NOT EXISTS ix_tracked_searches_user_id ON tracked_searches (user_id);")
        
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
            if "variant_id" not in si_cols:
                cursor.execute("ALTER TABLE sold_items ADD COLUMN variant_id INTEGER REFERENCES product_variants(id) ON DELETE SET NULL;")
            if "variant_name" not in si_cols:
                cursor.execute("ALTER TABLE sold_items ADD COLUMN variant_name TEXT;")
            if "variant_ids_json" not in si_cols:
                cursor.execute("ALTER TABLE sold_items ADD COLUMN variant_ids_json TEXT;")
            if "variant_names_json" not in si_cols:
                cursor.execute("ALTER TABLE sold_items ADD COLUMN variant_names_json TEXT;")
            if "variant_confidence" not in si_cols:
                cursor.execute("ALTER TABLE sold_items ADD COLUMN variant_confidence REAL DEFAULT 0.0;")
            if "variant_method" not in si_cols:
                cursor.execute("ALTER TABLE sold_items ADD COLUMN variant_method TEXT;")
            if "variant_evidence" not in si_cols:
                cursor.execute("ALTER TABLE sold_items ADD COLUMN variant_evidence TEXT;")

            # 3. High-performance indexes for search, status, replica, child size, and variant lookups
            cursor.execute("CREATE INDEX IF NOT EXISTS ix_sold_items_search_captured ON sold_items (tracked_search_id, captured_at DESC);")
            cursor.execute("CREATE INDEX IF NOT EXISTS ix_sold_items_replica ON sold_items (is_replica);")
            cursor.execute("CREATE INDEX IF NOT EXISTS ix_sold_items_sold ON sold_items (is_sold);")
            cursor.execute("CREATE INDEX IF NOT EXISTS ix_sold_items_child ON sold_items (is_child_size);")
            cursor.execute("CREATE INDEX IF NOT EXISTS ix_sold_items_variant ON sold_items (variant_id);")

        # 4. autobuy_orders table for simulated paper trading and automated snipes
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS autobuy_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id TEXT NOT NULL UNIQUE,
                deal_id TEXT NOT NULL,
                vinted_item_id TEXT NOT NULL,
                tracked_search_id INTEGER REFERENCES tracked_searches(id) ON DELETE SET NULL,
                title TEXT NOT NULL,
                brand TEXT,
                variant_name TEXT,
                item_price REAL NOT NULL,
                buyer_fee REAL NOT NULL,
                shipping_cost REAL NOT NULL,
                total_landed_cost REAL NOT NULL,
                projected_resale_price REAL NOT NULL,
                projected_net_profit REAL NOT NULL,
                projected_roi_pct REAL NOT NULL,
                ai_score INTEGER,
                ai_verdict TEXT,
                is_simulation BOOLEAN DEFAULT 1 NOT NULL,
                status TEXT DEFAULT 'SIMULATED_SUCCESS' NOT NULL,
                execution_notes TEXT,
                shipping_name TEXT,
                shipping_postcode TEXT,
                card_last4 TEXT,
                photo_url TEXT,
                item_url TEXT,
                created_at DATETIME
            );
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS ix_autobuy_orders_deal ON autobuy_orders (deal_id);")
        cursor.execute("CREATE INDEX IF NOT EXISTS ix_autobuy_orders_created ON autobuy_orders (created_at DESC);")
                
        conn.connection.commit()

