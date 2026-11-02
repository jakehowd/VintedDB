"""Pydantic request / response schemas."""

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


class SuggestClassificationRequest(BaseModel):
    query: str


class SuggestClassificationResponse(BaseModel):
    query: str
    suggested_brand: str
    suggested_categories: List[str]
    suggested_category_labels: List[str]
    detected_item_type: str
    explanation: str

