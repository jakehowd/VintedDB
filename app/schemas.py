"""Pydantic request / response schemas."""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel


# ── Searches ──────────────────────────────────────────────

class SearchCreate(BaseModel):
    search_text: str
    brand: Optional[str] = None
    category: Optional[str] = None
    categories: Optional[List[str]] = None
    must_exclude: Optional[str] = None
    exclude_child_sizes: Optional[bool] = False
    antirep_settings: Optional[Dict[str, Any]] = None
    variants_enabled: Optional[bool] = False
    auto_research_variants: Optional[bool] = False


class SearchUpdate(BaseModel):
    search_text: Optional[str] = None
    brand: Optional[str] = None
    category: Optional[str] = None
    categories: Optional[List[str]] = None
    must_exclude: Optional[str] = None
    exclude_child_sizes: Optional[bool] = None
    antirep_settings: Optional[Dict[str, Any]] = None
    variants_enabled: Optional[bool] = None
    is_active: Optional[bool] = None


class SearchResponse(BaseModel):
    id: int
    search_text: str
    brand: Optional[str] = None
    category: Optional[str] = None
    categories: List[str] = []
    must_exclude: Optional[str] = None
    exclude_child_sizes: bool = False
    antirep_settings: Optional[Dict[str, Any]] = None
    antirep_config: Optional[Dict[str, Any]] = None
    is_active: bool
    created_at: Optional[datetime] = None
    item_count: int = 0

    model_config = {"from_attributes": True}



class SuggestClassificationRequest(BaseModel):
    query: str


class SuggestClassificationResponse(BaseModel):
    query: str
    suggested_brand: str
    suggested_categories: List[str]
    suggested_category_labels: List[str]
    detected_item_type: str
    explanation: str



# ── Analytics ─────────────────────────────────────────────

class TimePeriodData(BaseModel):
    period_label: str
    count: int
    avg_price: float
    min_price: float
    max_price: float
    total_revenue: float


class SalesOverTimeResponse(BaseModel):
    search_id: int
    search_text: str
    period: str
    data: List[TimePeriodData]


class SummaryResponse(BaseModel):
    search_id: int
    search_text: str
    total_items: int
    avg_price: float
    min_price: float
    max_price: float
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None


class SoldItemResponse(BaseModel):
    id: int
    title: str
    price: float
    currency: str
    brand: Optional[str] = None
    size: Optional[str] = None
    photo_url: Optional[str] = None
    url: Optional[str] = None
    captured_at: Optional[datetime] = None
