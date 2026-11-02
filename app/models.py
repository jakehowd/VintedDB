"""SQLAlchemy ORM models."""

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

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


class User(Base):
    """User account profile for multi-tenant data storage."""

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, nullable=False, index=True)
    password_hash = Column(String, nullable=False)
    is_admin = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=_utcnow)

    searches = relationship(
        "TrackedSearch",
        back_populates="user",
        cascade="all, delete-orphan",
        order_by="TrackedSearch.id",
    )
    sessions = relationship(
        "UserSession",
        back_populates="user",
        cascade="all, delete-orphan",
    )


class UserSession(Base):
    """Active session tokens for authenticated user profiles."""

    __tablename__ = "user_sessions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    session_token = Column(String, unique=True, nullable=False, index=True)
    created_at = Column(DateTime, default=_utcnow)
    expires_at = Column(DateTime, nullable=False)

    user = relationship("User", back_populates="sessions")


class TrackedSearch(Base):
    """A search term the user wants to monitor on Vinted."""

    __tablename__ = "tracked_searches"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    search_text = Column(String, nullable=False)
    brand = Column(String, nullable=True)
    category = Column(String, nullable=True)
    must_exclude = Column(String, nullable=True)
    exclude_child_sizes = Column(Boolean, default=False, nullable=False)
    antirep_settings = Column(Text, nullable=True)  # JSON config for Anti-Rep filters
    variants_enabled = Column(Boolean, default=False, nullable=False)
    catalogue_summary = Column(Text, nullable=True)  # JSON metadata from LLM Deep Research & Local Vision training
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=_utcnow)

    user = relationship("User", back_populates="searches")
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
    variant_ids_json = Column(Text, nullable=True)     # Compact JSON list e.g. "[6,8]" for multi-category facets
    variant_names_json = Column(Text, nullable=True)   # JSON list e.g. '["Cable-Knit","Wool / Merino"]'
    variant_confidence = Column(Float, default=0.0)
    variant_method = Column(String, nullable=True)
    variant_evidence = Column(Text, nullable=True)
    captured_at = Column(DateTime, default=_utcnow, index=True)
    item_created_at = Column(DateTime, nullable=True)
    raw_data = Column(Text, nullable=True)  # JSON blob for future-proofing

    tracked_search = relationship("TrackedSearch", back_populates="items")
    variant = relationship("ProductVariant", back_populates="items")

    def parsed_variant_ids(self) -> List[int]:
        if self.variant_ids_json:
            try:
                arr = json.loads(self.variant_ids_json)
                if isinstance(arr, list):
                    return [int(x) for x in arr if x is not None]
            except Exception:
                pass
        return [int(self.variant_id)] if self.variant_id is not None else []

    def parsed_variant_names(self) -> List[str]:
        if self.variant_names_json:
            try:
                arr = json.loads(self.variant_names_json)
                if isinstance(arr, list):
                    return [str(x) for x in arr if x]
            except Exception:
                pass
        return [self.variant_name] if self.variant_name else []

    def apply_variant_result(self, res: Optional[Dict[str, Any]]) -> None:
        """Synchronize both multi-category JSON arrays and primary variant fields on SoldItem."""
        if not res:
            self.variant_id = None
            self.variant_name = None
            self.variant_ids_json = None
            self.variant_names_json = None
            self.variant_confidence = 0.0
            self.variant_method = None
            self.variant_evidence = None
            return

        v_ids = [int(x) for x in (res.get("variant_ids") or []) if x is not None]
        if not v_ids and res.get("variant_id") is not None:
            v_ids = [int(res["variant_id"])]
        v_names = [str(x) for x in (res.get("variant_names") or []) if x]
        if not v_names and res.get("variant_name"):
            v_names = [str(res["variant_name"])]

        # Store the most specific matched variant ID (sub-style if matched, else parent model)
        self.variant_id = v_ids[-1] if v_ids else None
        self.variant_name = " › ".join(v_names) if v_names else None
        self.variant_ids_json = json.dumps(v_ids, separators=(",", ":")) if v_ids else None
        self.variant_names_json = json.dumps(v_names, ensure_ascii=False) if v_names else None
        self.variant_confidence = float(res.get("confidence") or 0.0)
        self.variant_method = res.get("method")
        self.variant_evidence = res.get("evidence")


class ProductVariant(Base):
    """Hierarchical Product Model (`parent_variant_id=None`) or Model-Exclusive Sub-Style (`parent_variant_id=<model.id>`)."""

    __tablename__ = "product_variants"

    id = Column(Integer, primary_key=True, index=True)
    tracked_search_id = Column(
        Integer, ForeignKey("tracked_searches.id", ondelete="CASCADE"), nullable=False, index=True
    )
    parent_variant_id = Column(
        Integer, ForeignKey("product_variants.id", ondelete="CASCADE"), nullable=True, index=True
    )
    variant_slug = Column(String, nullable=False, index=True)
    variant_name = Column(String, nullable=False)
    dimension = Column(String, default="Model", nullable=False)  # 'Model' (top-level) or 'Style' (exclusive sub-style)
    modality = Column(String, default="visual", nullable=False)  # 'visual' (CLIP+OCR+Text) or 'label' (Text+OCR only)
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
    parent = relationship("ProductVariant", remote_side=[id], back_populates="sub_styles")
    sub_styles = relationship(
        "ProductVariant",
        back_populates="parent",
        cascade="all, delete-orphan",
        order_by="ProductVariant.id",
    )

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


class AutoBuyOrder(Base):
    """Ledger of simulated (paper trading) and live automated snipe purchases."""

    __tablename__ = "autobuy_orders"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(String, unique=True, index=True, nullable=False)
    deal_id = Column(String, index=True, nullable=False)
    vinted_item_id = Column(String, index=True, nullable=False)
    tracked_search_id = Column(
        Integer, ForeignKey("tracked_searches.id", ondelete="SET NULL"), nullable=True
    )
    title = Column(String, nullable=False)
    brand = Column(String, nullable=True)
    variant_name = Column(String, nullable=True)
    item_price = Column(Float, nullable=False)
    buyer_fee = Column(Float, nullable=False)
    shipping_cost = Column(Float, nullable=False)
    total_landed_cost = Column(Float, nullable=False)
    projected_resale_price = Column(Float, nullable=False)
    projected_net_profit = Column(Float, nullable=False)
    projected_roi_pct = Column(Float, nullable=False)
    ai_score = Column(Integer, nullable=True)
    ai_verdict = Column(String, nullable=True)
    is_simulation = Column(Boolean, default=True, nullable=False)
    status = Column(String, default="SIMULATED_SUCCESS", nullable=False)
    execution_notes = Column(Text, nullable=True)
    shipping_name = Column(String, nullable=True)
    shipping_postcode = Column(String, nullable=True)
    card_last4 = Column(String, nullable=True)
    photo_url = Column(String, nullable=True)
    item_url = Column(String, nullable=True)
    created_at = Column(DateTime, default=_utcnow, index=True)


