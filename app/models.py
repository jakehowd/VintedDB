"""SQLAlchemy ORM models."""

import json
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import relationship

from .database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


DEFAULT_ANTIREP_CONFIG = {
    "antirep_enabled": True,
    "block_new_with_tags": True,
    "block_no_reviews": True,
    "min_seller_reviews": 1,
    "block_multiple_seller_listings": True,
    "max_seller_listings": 2,
    "block_duplicate_photos": True,
    "block_dormant_accounts": False,
    "dormant_account_days": 90,
}


class TrackedSearch(Base):
    """A search term the user wants to monitor on Vinted."""

    __tablename__ = "tracked_searches"

    id = Column(Integer, primary_key=True, index=True)
    search_text = Column(String, nullable=False, unique=True)
    brand = Column(String, nullable=True)
    category = Column(String, nullable=True)
    must_exclude = Column(String, nullable=True)
    exclude_child_sizes = Column(Boolean, default=False, nullable=False)
    antirep_settings = Column(Text, nullable=True)  # JSON config for Anti-Rep filters
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=_utcnow)

    items = relationship(
        "SoldItem",
        back_populates="tracked_search",
        cascade="all, delete-orphan",
    )

    @property
    def antirep_config(self) -> dict:
        """Return parsed anti-rep settings with fallback defaults."""
        config = dict(DEFAULT_ANTIREP_CONFIG)
        if self.antirep_settings:
            try:
                parsed = json.loads(self.antirep_settings)
                if isinstance(parsed, dict):
                    config.update(parsed)
            except Exception:
                pass
        return config


class SoldItem(Base):
    """An individual item captured from Vinted search results."""

    __tablename__ = "sold_items"

    id = Column(Integer, primary_key=True, index=True)
    tracked_search_id = Column(
        Integer, ForeignKey("tracked_searches.id"), nullable=False
    )
    vinted_item_id = Column(String, unique=True, nullable=False, index=True)
    title = Column(String, default="")
    price = Column(Float, default=0.0)
    currency = Column(String, default="GBP")
    brand = Column(String, nullable=True)
    size = Column(String, nullable=True)
    condition = Column(String, nullable=True)
    seller_id = Column(String, nullable=True, index=True)
    seller_username = Column(String, nullable=True)
    photo_url = Column(String, nullable=True)
    url = Column(String, nullable=True)
    status = Column(String, default="active", nullable=True)
    is_sold = Column(Boolean, default=False, index=True)
    is_replica = Column(Boolean, default=False, index=True)
    replica_reason = Column(String, nullable=True)
    is_child_size = Column(Boolean, default=False, index=True)
    child_size_reason = Column(String, nullable=True)
    captured_at = Column(DateTime, default=_utcnow, index=True)
    item_created_at = Column(DateTime, nullable=True)
    raw_data = Column(Text, nullable=True)  # JSON blob for future-proofing

    tracked_search = relationship("TrackedSearch", back_populates="items")


class ScrapedPhotoRecord(Base):
    """Database of photo fingerprints across all scrapes to detect duplicate / stock photos."""

    __tablename__ = "scraped_photos"

    id = Column(Integer, primary_key=True, index=True)
    photo_key = Column(String, index=True, nullable=False)
    photo_url = Column(String, nullable=True)
    dhash = Column(String, index=True, nullable=True)
    vinted_item_id = Column(String, index=True, nullable=False)
    seller_id = Column(String, nullable=True)
    tracked_search_id = Column(
        Integer, ForeignKey("tracked_searches.id", ondelete="SET NULL"), nullable=True
    )
    created_at = Column(DateTime, default=_utcnow)

