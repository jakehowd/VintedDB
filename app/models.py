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
    variants_enabled = Column(Boolean, default=False, nullable=False)
    catalogue_summary = Column(Text, nullable=True)  # JSON metadata from LLM Deep Research & Local Vision training
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=_utcnow)

    items = relationship(
        "SoldItem",
        back_populates="tracked_search",
        cascade="all, delete-orphan",
    )
    variants = relationship(
        "ProductVariant",
        back_populates="tracked_search",
        cascade="all, delete-orphan",
        order_by="ProductVariant.id",
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
    variant_id = Column(
        Integer, ForeignKey("product_variants.id", ondelete="SET NULL"), nullable=True, index=True
    )
    variant_name = Column(String, nullable=True, index=True)
    variant_confidence = Column(Float, default=0.0)
    variant_method = Column(String, nullable=True)
    variant_evidence = Column(Text, nullable=True)
    captured_at = Column(DateTime, default=_utcnow, index=True)
    item_created_at = Column(DateTime, nullable=True)
    raw_data = Column(Text, nullable=True)  # JSON blob for future-proofing

    tracked_search = relationship("TrackedSearch", back_populates="items")
    variant = relationship("ProductVariant", back_populates="items")


class ProductVariant(Base):
    """Specific product variant / sub-model generated via Deep Research & learned by Local Vision AI."""

    __tablename__ = "product_variants"

    id = Column(Integer, primary_key=True, index=True)
    tracked_search_id = Column(
        Integer, ForeignKey("tracked_searches.id", ondelete="CASCADE"), nullable=False, index=True
    )
    variant_slug = Column(String, nullable=False, index=True)
    variant_name = Column(String, nullable=False)
    short_description = Column(Text, nullable=True)
    resale_tier = Column(String, default="STANDARD")  # PREMIUM, HIGH, STANDARD, BUDGET
    est_market_value_gbp = Column(Float, nullable=True)
    visual_cues = Column(Text, nullable=True)          # JSON list of CLIP visual descriptions
    ocr_signatures = Column(Text, nullable=True)       # JSON list of printed badge / lens / cuff / tag strings
    text_keywords = Column(Text, nullable=True)        # JSON list of positive title/description keywords
    negative_keywords = Column(Text, nullable=True)    # JSON list of disqualifying keywords for this variant
    prototype_embedding = Column(Text, nullable=True)  # JSON list of 512 floats (trained CLIP visual centroid)
    training_sample_count = Column(Integer, default=0)
    target_max_price = Column(Float, nullable=True)    # Custom sniper max buy price (£) for this variant
    is_sniping_enabled = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    tracked_search = relationship("TrackedSearch", back_populates="variants")
    items = relationship("SoldItem", back_populates="variant")

    def parsed_list(self, field_name: str) -> list[str]:
        raw = getattr(self, field_name, None)
        if not raw:
            return []
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                return [str(x).strip() for x in data if str(x).strip()]
        except Exception:
            pass
        return []


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

