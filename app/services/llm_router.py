"""Multi-Provider Cloud LLM Router & Product Variant Deep Research Engine.

Supports spreading load across multiple LLM providers and API keys:
- Google Gemini (with live Google Search Grounding for product Deep Research)
- Groq (ultra-fast free-tier LLaMA / Scout inference)
- OpenRouter (access to :free and paid models across providers)
- Mistral / Custom OpenAI-compatible endpoints
- Local Ollama (zero-cost local fallback if running on localhost:11434)
- Live Vinted Corpus + Specialist Domain Knowledge Base (works even with 0 API keys!)
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx

from ..config import AI_CONFIG_PATH

logger = logging.getLogger(__name__)


DEFAULT_AI_CONFIG: Dict[str, Any] = {
    "routing_strategy": "spread",  # 'spread' | 'gemini_first' | 'free_first'
    "gemini_api_keys": [],
    "gemini_model": "gemini-2.5-flash",
    "enable_google_search_grounding": True,
    "groq_api_keys": [],
    "groq_model": "llama-3.3-70b-versatile",
    "openrouter_api_keys": [],
    "openrouter_model": "google/gemma-3-27b-it:free",
    "mistral_api_keys": [],
    "mistral_model": "mistral-small-latest",
    "custom_openai_base_url": "",
    "custom_openai_api_key": "",
    "custom_openai_model": "",
    "ollama_base_url": "http://127.0.0.1:11434",
    "ollama_vision_model": "moondream",
    "ollama_text_model": "",
}


# ---------------------------------------------------------------------------
# High-Precision Specialist Knowledge Base (Instant Seed + Zero-Key Fallback)
# ---------------------------------------------------------------------------

# Distinctive physical features that define a rarer variant. If a variant's name carries one of these
# and the tracked search itself does not (e.g. "Hooded Henley" inside a "Superdry henley" search), the
# variant is feature-gated: it needs explicit evidence and is never assigned by relative photo ranking.
_FEATURE_GROUPS: List[set] = [
    {"hood", "hooded", "hoodie", "hoody"},
    {"cropped", "crop"},
    {"gilet", "vest", "bodywarmer", "sleeveless"},
    {"reversible"},
]


def _defining_feature_group(variant_name: str, search_text: str) -> bool:
    name_toks = set(re.findall(r"[a-z]+", (variant_name or "").lower()))
    search_toks = set(re.findall(r"[a-z]+", (search_text or "").lower()))
    for grp in _FEATURE_GROUPS:
        if name_toks & grp and not (search_toks & grp):
            return True
    return False


SPECIALIST_CATALOGUES: List[Tuple[List[str], Dict[str, Any]]] = [
    (
        ["nuptse", "north face puffer", "tnf 700"],
        {
            "product_family": "The North Face Nuptse Outerwear",
            "domain": "fashion",
            "research_notes": "Hierarchical product-specific Nuptse models (1996 Retro 700, Summit Series 800, Vintage 90s/Classic 700, Reversible/Sherpa) with strictly model-exclusive sub-styles.",
            "variants": [
                {
                    "variant_slug": "1996_retro_nuptse",
                    "variant_name": "1996 Retro Nuptse (700)",
                    "dimension": "Model",
                    "modality": "visual",
                    "short_description": "Iconic boxy shiny ripstop nylon puffer with contrasting black shoulder yoke, stowable collar hood, and '700' cuff embroidery.",
                    "resale_tier": "HIGH",
                    "est_market_value_gbp": 115.0,
                    "visual_cues": [
                        "boxy shiny ripstop nylon puffer jacket with wide horizontal down baffles and contrasting black shoulder yoke",
                        "The North Face 1996 Retro Nuptse 700 down puffer with oversized baffles",
                    ],
                    "ocr_signatures": ["NF0A3C8D", "NF0A3XEO", "NF0A5GGE", "NF0A3JQQ", "STOW POCKET"],
                    "text_keywords": ["1996", "retro nuptse", "1996 retro", "nf0a3c8d", "nf0a3xeo", "nuptse short", "cropped nuptse"],
                    "negative_keywords": ["summit", "summit series", "800", "850", "pertex", "windstopper", "reversible", "sherpa", "borg", "hyvent"],
                    "sub_styles": [
                        {
                            "variant_slug": "1996_retro_boxy_jacket",
                            "variant_name": "Boxy Jacket",
                            "modality": "visual",
                            "short_description": "Full waist/hip-length long-sleeve 1996 Retro Nuptse puffer jacket (4–5 baffles).",
                            "resale_tier": "HIGH",
                            "est_market_value_gbp": 120.0,
                            "visual_cues": [
                                "regular hip-length long sleeve boxy 1996 Retro Nuptse down puffer jacket with 4 or 5 horizontal baffles",
                            ],
                            "ocr_signatures": ["NF0A3C8D", "NF0A3XEO"],
                            "text_keywords": ["1996 retro nuptse down jacket", "1996 retro nuptse jacket", "nf0a3c8d"],
                            "negative_keywords": ["gilet", "gillet", "vest", "bodywarmer", "body warmer", "sleeveless", "cropped", "nuptse short", "crop"],
                        },
                        {
                            "variant_slug": "1996_retro_cropped_short",
                            "variant_name": "Cropped / Nuptse Short",
                            "modality": "visual",
                            "short_description": "Women's high-waisted 2-to-3 baffle cropped 1996 Nuptse Short silhouette.",
                            "resale_tier": "STANDARD",
                            "est_market_value_gbp": 88.0,
                            "visual_cues": [
                                "short cropped high-waisted womens 1996 Nuptse puffer jacket with only two or three horizontal baffles",
                            ],
                            "ocr_signatures": ["NF0A5GGE", "NF0A5GGEJK3"],
                            "text_keywords": ["nuptse short", "cropped nuptse", "cropped", "crop", "short puffer"],
                            "negative_keywords": ["gilet", "gillet", "vest", "bodywarmer", "sleeveless", "mens"],
                        },
                        {
                            "variant_slug": "1996_retro_gilet_vest",
                            "variant_name": "1996 Gilet / Vest",
                            "modality": "visual",
                            "short_description": "Sleeveless 1996 Retro Nuptse 700 down bodywarmer gilet.",
                            "resale_tier": "STANDARD",
                            "est_market_value_gbp": 75.0,
                            "visual_cues": [
                                "sleeveless down puffer gilet vest bodywarmer with black shoulder yoke and no sleeves",
                            ],
                            "ocr_signatures": ["NF0A3JQQ"],
                            "text_keywords": ["gilet", "gillet", "vest", "bodywarmer", "body warmer", "sleeveless"],
                            "negative_keywords": ["long sleeve", "cropped"],
                        },
                    ],
                },
                {
                    "variant_slug": "summit_series_nuptse",
                    "variant_name": "Summit Series (800)",
                    "dimension": "Model",
                    "modality": "visual",
                    "short_description": "Technical alpine expedition edition with Summit Series sleeve crest and 800/850-fill goose down.",
                    "resale_tier": "PREMIUM",
                    "est_market_value_gbp": 165.0,
                    "visual_cues": [
                        "technical alpine expedition down jacket with Summit Series crest badge on sleeve and chest zip pocket",
                        "matte Pertex mountaineering down puffer jacket with 800 fill embroidery",
                    ],
                    "ocr_signatures": ["SUMMIT", "SUMMIT SERIES", "800", "850", "PERTEX", "WINDSTOPPER", "DRYLOFT"],
                    "text_keywords": ["summit", "summit series", "800", "850", "pertex", "windstopper", "baltoro", "dryloft"],
                    "negative_keywords": ["1996 retro", "nuptse short", "cropped nuptse", "sherpa", "reversible"],
                    "sub_styles": [
                        {
                            "variant_slug": "summit_800_pertex",
                            "variant_name": "800 Pro / Pertex Jacket",
                            "modality": "label",
                            "short_description": "Ultralight Pertex Quantum / 800-fill alpine Summit Series down jacket.",
                            "resale_tier": "PREMIUM",
                            "est_market_value_gbp": 160.0,
                            "visual_cues": [],
                            "ocr_signatures": ["PERTEX", "800", "850"],
                            "text_keywords": ["800", "850", "pertex", "pro down"],
                            "negative_keywords": ["windstopper", "dryloft", "baltoro"],
                        },
                        {
                            "variant_slug": "summit_windstopper_dryloft",
                            "variant_name": "Windstopper / Gore-Dryloft",
                            "modality": "label",
                            "short_description": "Weatherproof Gore Windstopper / Dryloft / Baltoro hooded expedition parka.",
                            "resale_tier": "PREMIUM",
                            "est_market_value_gbp": 185.0,
                            "visual_cues": [],
                            "ocr_signatures": ["WINDSTOPPER", "GORE", "DRYLOFT", "BALTORO"],
                            "text_keywords": ["windstopper", "dryloft", "gore", "baltoro", "hooded summit"],
                            "negative_keywords": [],
                        },
                    ],
                },
                {
                    "variant_slug": "classic_vintage_700_nuptse",
                    "variant_name": "Vintage 90s / Classic 700",
                    "dimension": "Model",
                    "modality": "visual",
                    "short_description": "Original 1990s–2000s classic 700-fill Nuptse jackets, 700 LTD editions, and vintage bodywarmers.",
                    "resale_tier": "HIGH",
                    "est_market_value_gbp": 98.0,
                    "visual_cues": [
                        "classic matte nylon North Face 700 down puffer jacket or gilet with vintage silhouette",
                    ],
                    "ocr_signatures": ["700", "700 LTD", "HYVENT"],
                    "text_keywords": ["vintage", "90s", "y2k", "00s", "700 ltd", "classic 700", "700 down", "700 puffer", "700 nuptse", "nuptse 700", "hyvent"],
                    "negative_keywords": ["1996", "1996 retro", "summit", "summit series", "800", "reversible", "sherpa", "borg", "nuptse short"],
                    "sub_styles": [
                        {
                            "variant_slug": "classic_700_jacket",
                            "variant_name": "Classic 700 Jacket",
                            "modality": "visual",
                            "short_description": "Original 90s/00s long-sleeve 700-fill down puffer jacket.",
                            "resale_tier": "HIGH",
                            "est_market_value_gbp": 102.0,
                            "visual_cues": ["long sleeve matte nylon vintage North Face 700 down puffer coat"],
                            "ocr_signatures": ["700"],
                            "text_keywords": ["puffer jacket", "down jacket", "puffer coat", "700 ltd", "hooded"],
                            "negative_keywords": ["gilet", "gillet", "vest", "bodywarmer", "body warmer", "sleeveless"],
                        },
                        {
                            "variant_slug": "classic_700_gilet",
                            "variant_name": "Classic 700 Gilet / Vest",
                            "modality": "visual",
                            "short_description": "Vintage 700-fill sleeveless down bodywarmer gilet.",
                            "resale_tier": "STANDARD",
                            "est_market_value_gbp": 68.0,
                            "visual_cues": ["sleeveless vintage North Face 700 down gilet bodywarmer vest"],
                            "ocr_signatures": ["700"],
                            "text_keywords": ["gilet", "gillet", "vest", "bodywarmer", "body warmer", "sleeveless"],
                            "negative_keywords": ["long sleeve", "coat"],
                        },
                    ],
                },
                {
                    "variant_slug": "reversible_sherpa_nuptse",
                    "variant_name": "Reversible / Sherpa Nuptse",
                    "dimension": "Model",
                    "modality": "visual",
                    "short_description": "High-pile teddy/Sherpa borg fleece Nuptse or reversible nylon-to-fleece construction.",
                    "resale_tier": "HIGH",
                    "est_market_value_gbp": 120.0,
                    "visual_cues": [
                        "fluffy textured sherpa borg teddy fleece North Face Nuptse puffer jacket",
                        "reversible fleece and nylon quilted down puffer coat",
                    ],
                    "ocr_signatures": ["HEATSEEKER"],
                    "text_keywords": ["reversible", "sherpa", "borg", "teddy", "high pile", "fleece nuptse"],
                    "negative_keywords": ["summit series", "800"],
                    "sub_styles": [],
                },
            ],
        },
    ),
    (
        ["ralph lauren quarter zip", "polo quarter zip", "ralph lauren half zip", "ralph lauren 1/4 zip", "ralph lauren jumper", "ralph lauren knit"],
        {
            "product_family": "Polo Ralph Lauren Quarter-Zip Pullovers",
            "domain": "fashion",
            "research_notes": "Product-specific Ralph Lauren models: Cable-Knit Quarter-Zip (with exclusive Wool/Cashmere & Pima sub-styles), Estate-Rib & Double-Knit Pullover, Merino & Cashmere Fine-Knit, Polo Bear/Crest Collector Editions, and Polar Fleece/Waffle/RLX.",
            "variants": [
                {
                    "variant_slug": "cable_knit_quarter_zip",
                    "variant_name": "Cable-Knit Quarter-Zip",
                    "dimension": "Model",
                    "modality": "visual",
                    "short_description": "Signature raised twisted-rope braided cable-knit quarter-zip jumper (made in combed cotton, merino wool, or cashmere blends).",
                    "resale_tier": "PREMIUM",
                    "est_market_value_gbp": 58.0,
                    "visual_cues": [
                        "a close-up photo of a chunky braided cable-knit sweater jumper with raised twisted rope vertical cable stitches",
                        "textured vertical cable knit weave jumper with raised rope braids across torso and sleeves",
                    ],
                    "ocr_signatures": [],
                    "text_keywords": ["cable knit", "cable-knit", "cable", "braided", "rope knit", "chunky cable"],
                    "negative_keywords": ["estate rib", "estate-rib", "double-knit", "double knit", "sweatshirt", "fleece", "polar fleece", "waffle", "interlock", "ribbed", "fair isle", "argyle"],
                    "sub_styles": [
                        {
                            "variant_slug": "cable_knit_wool_cashmere",
                            "variant_name": "Wool & Cashmere Cable-Knit",
                            "modality": "label",
                            "short_description": "Luxury Italian wool, lambswool, or wool-cashmere blend cable-knit quarter-zip.",
                            "resale_tier": "PREMIUM",
                            "est_market_value_gbp": 72.0,
                            "visual_cues": [],
                            "ocr_signatures": ["CASHMERE", "100% WOOL", "MERINO", "LAMBSWOOL", "ITALIAN YARN"],
                            "text_keywords": ["cashmere", "wool", "merino", "lambswool", "lambs wool"],
                            "negative_keywords": ["100% cotton"],
                        },
                        {
                            "variant_slug": "cable_knit_pima_cotton",
                            "variant_name": "Pima / Combed Cotton Cable",
                            "modality": "label",
                            "short_description": "Classic breathable Pima or combed 100% cotton cable-knit quarter-zip.",
                            "resale_tier": "HIGH",
                            "est_market_value_gbp": 52.0,
                            "visual_cues": [],
                            "ocr_signatures": ["PIMA COTTON", "SUPIMA", "100% COTTON"],
                            "text_keywords": ["pima", "supima", "100% cotton", "cotton cable"],
                            "negative_keywords": ["cashmere", "100% wool", "lambswool", "merino"],
                        },
                    ],
                },
                {
                    "variant_slug": "estate_rib_double_knit",
                    "variant_name": "Estate-Rib & Double-Knit",
                    "dimension": "Model",
                    "modality": "visual",
                    "short_description": "Polo's core smooth peached interlock 'Estate-Rib' pullover and structured 'Double-Knit' sweatshirt with suede/metal zip pull.",
                    "resale_tier": "STANDARD",
                    "est_market_value_gbp": 34.0,
                    "visual_cues": [
                        "a photo of a smooth flat jersey Estate-Rib quarter-zip sweatshirt pullover with plain smooth fabric and no cable texture",
                        "flat plain smooth or subtle rib pullover sweatshirt without any twisted rope cable braids",
                    ],
                    "ocr_signatures": ["ESTATE RIB", "ESTATE-RIB", "DOUBLE-KNIT"],
                    "text_keywords": ["estate rib", "estate-rib", "double-knit", "double knit", "sweatshirt", "jersey", "interlock", "loopback", "peached"],
                    "negative_keywords": ["cable", "cable-knit", "cable knit", "cashmere", "merino", "lambswool", "100% wool", "waffle", "polar fleece", "sherpa", "polo bear"],
                    "sub_styles": [
                        {
                            "variant_slug": "estate_rib_classic",
                            "variant_name": "Estate-Rib Pullover",
                            "modality": "label",
                            "short_description": "Soft brushed peached-interlock Estate-Rib pullover with signature suede zip tab.",
                            "resale_tier": "STANDARD",
                            "est_market_value_gbp": 35.0,
                            "visual_cues": [],
                            "ocr_signatures": ["ESTATE RIB", "ESTATE-RIB"],
                            "text_keywords": ["estate rib", "estate-rib", "suede zip", "interlock"],
                            "negative_keywords": ["double-knit", "double knit"],
                        },
                        {
                            "variant_slug": "double_knit_tech_sweatshirt",
                            "variant_name": "Double-Knit Sweatshirt",
                            "modality": "label",
                            "short_description": "Modern smooth structured Double-Knit tech sweatshirt pullover.",
                            "resale_tier": "HIGH",
                            "est_market_value_gbp": 42.0,
                            "visual_cues": [],
                            "ocr_signatures": ["DOUBLE-KNIT", "DOUBLE KNIT"],
                            "text_keywords": ["double-knit", "double knit", "sweatshirt"],
                            "negative_keywords": ["estate rib", "estate-rib"],
                        },
                    ],
                },
                {
                    "variant_slug": "merino_cashmere_fine_knit",
                    "variant_name": "Merino & Cashmere Fine-Knit",
                    "dimension": "Model",
                    "modality": "label",
                    "short_description": "Polo's luxury fine-gauge dress knitwear line spun from extra-fine Merino wool, Lambswool, or Cashmere (non-cable).",
                    "resale_tier": "HIGH",
                    "est_market_value_gbp": 52.0,
                    "visual_cues": [],
                    "ocr_signatures": ["100% WOOL", "EXTRA FINE MERINO", "MERINO WOOL", "LAMBSWOOL", "PURE NEW WOOL", "CASHMERE", "ITALIAN YARN"],
                    "text_keywords": ["merino", "lambswool", "lambs wool", "pure new wool", "100% wool", "wool", "cashmere", "pima wool", "fine wool"],
                    "negative_keywords": ["cable", "cable-knit", "cable knit", "sweatshirt", "polar fleece", "cotton wool"],
                    "sub_styles": [
                        {
                            "variant_slug": "fine_knit_merino_lambswool",
                            "variant_name": "Merino / Lambswool",
                            "modality": "label",
                            "short_description": "100% Extra-Fine Italian Merino wool or Scottish/ Geelong Lambswool fine-knit.",
                            "resale_tier": "HIGH",
                            "est_market_value_gbp": 48.0,
                            "visual_cues": [],
                            "ocr_signatures": ["MERINO", "LAMBSWOOL", "100% WOOL", "PURE NEW WOOL"],
                            "text_keywords": ["merino", "lambswool", "lambs wool", "100% wool", "pure new wool", "wool"],
                            "negative_keywords": ["cashmere"],
                        },
                        {
                            "variant_slug": "fine_knit_cashmere_blend",
                            "variant_name": "Cashmere & Blend",
                            "modality": "label",
                            "short_description": "Pure cashmere or luxury wool-cashmere / silk-cashmere fine-gauge pullover.",
                            "resale_tier": "PREMIUM",
                            "est_market_value_gbp": 68.0,
                            "visual_cues": [],
                            "ocr_signatures": ["CASHMERE"],
                            "text_keywords": ["cashmere"],
                            "negative_keywords": [],
                        },
                    ],
                },
                {
                    "variant_slug": "polo_bear_crest_graphic",
                    "variant_name": "Polo Bear, Crest & Big Pony",
                    "dimension": "Model",
                    "modality": "label",
                    "short_description": "Collectible Polo Bear intarsia/embroidery, oversized Big Pony, Crest shield, or Spellout flag editions.",
                    "resale_tier": "PREMIUM",
                    "est_market_value_gbp": 64.0,
                    "visual_cues": [],
                    "ocr_signatures": ["POLO COUNTRY", "POLO SPORT"],
                    "text_keywords": ["polo bear", "big pony", "crest", "spellout", "spell out", "flag", "embroidery crest", "polo country", "golf bear"],
                    "negative_keywords": [],
                    "sub_styles": [
                        {
                            "variant_slug": "polo_bear_edition",
                            "variant_name": "Polo Bear",
                            "modality": "label",
                            "short_description": "Iconic dressed Polo Bear embroidered or knit motif.",
                            "resale_tier": "PREMIUM",
                            "est_market_value_gbp": 75.0,
                            "visual_cues": [],
                            "ocr_signatures": [],
                            "text_keywords": ["polo bear", "bear"],
                            "negative_keywords": [],
                        },
                        {
                            "variant_slug": "big_pony_crest_spellout",
                            "variant_name": "Big Pony / Crest / Spellout",
                            "modality": "label",
                            "short_description": "Oversized Big Pony chest embroidery, heritage Crest badge, or chest Spellout.",
                            "resale_tier": "HIGH",
                            "est_market_value_gbp": 52.0,
                            "visual_cues": [],
                            "ocr_signatures": ["POLO SPORT"],
                            "text_keywords": ["big pony", "crest", "spellout", "spell out", "flag"],
                            "negative_keywords": ["polo bear"],
                        },
                    ],
                },
                {
                    "variant_slug": "fleece_waffle_rlx",
                    "variant_name": "Polar Fleece, Waffle & RLX",
                    "dimension": "Model",
                    "modality": "label",
                    "short_description": "Polo Hi-Tech / Polar Fleece outdoor pullovers, textured Waffle thermals, Fair Isle, and RLX Golf performance tops.",
                    "resale_tier": "HIGH",
                    "est_market_value_gbp": 44.0,
                    "visual_cues": [],
                    "ocr_signatures": ["RLX", "POLO SPORT", "HI TECH"],
                    "text_keywords": ["fleece", "polar fleece", "sherpa", "borg", "waffle", "thermal", "fair isle", "argyle", "rlx", "golf", "hi tech", "polo sport", "corduroy", "velour"],
                    "negative_keywords": ["cable", "cable-knit", "estate rib"],
                    "sub_styles": [
                        {
                            "variant_slug": "polar_fleece_hitech",
                            "variant_name": "Polar Fleece / Sherpa",
                            "modality": "label",
                            "short_description": "Plush polar fleece or high-pile borg outdoor quarter-zip pullover.",
                            "resale_tier": "HIGH",
                            "est_market_value_gbp": 46.0,
                            "visual_cues": [],
                            "ocr_signatures": ["HI TECH", "POLO SPORT"],
                            "text_keywords": ["fleece", "polar fleece", "sherpa", "borg", "hi tech", "polo sport"],
                            "negative_keywords": ["waffle", "rlx"],
                        },
                        {
                            "variant_slug": "waffle_rlx_golf",
                            "variant_name": "Waffle / Fair Isle / RLX Golf",
                            "modality": "label",
                            "short_description": "Grid waffle thermal knit, Fair Isle jacquard, or RLX Golf stretch pullover.",
                            "resale_tier": "STANDARD",
                            "est_market_value_gbp": 40.0,
                            "visual_cues": [],
                            "ocr_signatures": ["RLX"],
                            "text_keywords": ["waffle", "thermal", "fair isle", "argyle", "rlx", "golf"],
                            "negative_keywords": ["polar fleece", "sherpa"],
                        },
                    ],
                },
            ],
        },
    ),
    (
        ["patagonia gilet", "patagonia vest", "patagonia bodywarmer"],
        {
            "product_family": "Patagonia Gilets & Vests",
            "domain": "fashion",
            "research_notes": "Product-specific Patagonia vest models: Classic Retro-X Sherpa Vest, Retro Pile & Los Gatos Fleece Gilets, Better Sweater & Synchilla Vests, and Down Sweater / Nano Puff Gilets.",
            "variants": [
                {
                    "variant_slug": "patagonia_retro_x_vest",
                    "variant_name": "Classic Retro-X Sherpa Vest",
                    "dimension": "Model",
                    "modality": "visual",
                    "short_description": "Windproof deep-pile sherpa fleece gilet with contrasting vertical zippered nylon chest pocket.",
                    "resale_tier": "PREMIUM",
                    "est_market_value_gbp": 78.0,
                    "visual_cues": ["shaggy sherpa fleece sleeveless vest with contrasting vertical zippered nylon chest patch pocket"],
                    "ocr_signatures": ["RETRO-X"],
                    "text_keywords": ["retro-x", "retro x", "classic retro", "sherpa vest", "sherpa gilet", "pelican"],
                    "negative_keywords": ["better sweater", "synchilla", "nano puff", "micro puff", "down sweater", "los gatos"],
                    "sub_styles": [],
                },
                {
                    "variant_slug": "patagonia_retro_pile_los_gatos_vest",
                    "variant_name": "Retro Pile & Los Gatos Gilet",
                    "dimension": "Model",
                    "modality": "visual",
                    "short_description": "Plush high-pile Polartec / Los Gatos sleeveless fleece vests without windproof nylon chest block.",
                    "resale_tier": "HIGH",
                    "est_market_value_gbp": 58.0,
                    "visual_cues": ["soft fluffy high-pile fleece sleeveless zip vest without contrasting nylon chest patch"],
                    "ocr_signatures": [],
                    "text_keywords": ["retro pile", "los gatos", "high pile", "teddy", "fluffy"],
                    "negative_keywords": ["retro-x", "retro x", "better sweater", "nano puff", "down sweater"],
                    "sub_styles": [
                        {
                            "variant_slug": "retro_pile_vest",
                            "variant_name": "Retro Pile Zip Gilet",
                            "modality": "label",
                            "short_description": "Double-sided shearling Polartec Retro Pile zip vest with mesh-lined pockets.",
                            "resale_tier": "HIGH",
                            "est_market_value_gbp": 60.0,
                            "visual_cues": [],
                            "ocr_signatures": [],
                            "text_keywords": ["retro pile"],
                            "negative_keywords": ["los gatos"],
                        },
                        {
                            "variant_slug": "los_gatos_vest",
                            "variant_name": "Los Gatos High-Pile Vest",
                            "modality": "label",
                            "short_description": "Silky high-pile Los Gatos women's fleece gilet.",
                            "resale_tier": "HIGH",
                            "est_market_value_gbp": 54.0,
                            "visual_cues": [],
                            "ocr_signatures": [],
                            "text_keywords": ["los gatos"],
                            "negative_keywords": ["retro pile"],
                        },
                    ],
                },
                {
                    "variant_slug": "patagonia_better_sweater_synchilla_vest",
                    "variant_name": "Better Sweater & Synchilla Vest",
                    "dimension": "Model",
                    "modality": "visual",
                    "short_description": "Smooth polar Synchilla or heathered knit-face Better Sweater full-zip gilet.",
                    "resale_tier": "STANDARD",
                    "est_market_value_gbp": 44.0,
                    "visual_cues": ["smooth polar fleece or marl sweater-knit sleeveless full-zip gilet vest"],
                    "ocr_signatures": ["SYNCHILLA"],
                    "text_keywords": ["better sweater", "synchilla", "polar fleece", "fleece gilet", "fleece vest"],
                    "negative_keywords": ["retro-x", "retro x", "retro pile", "los gatos", "nano puff", "micro puff", "down"],
                    "sub_styles": [
                        {
                            "variant_slug": "better_sweater_gilet",
                            "variant_name": "Better Sweater Vest",
                            "modality": "label",
                            "short_description": "Heathered sweater-knit face fleece gilet.",
                            "resale_tier": "STANDARD",
                            "est_market_value_gbp": 45.0,
                            "visual_cues": [],
                            "ocr_signatures": [],
                            "text_keywords": ["better sweater", "marl", "knit"],
                            "negative_keywords": ["synchilla"],
                        },
                        {
                            "variant_slug": "synchilla_fleece_gilet",
                            "variant_name": "Synchilla Fleece Gilet",
                            "modality": "label",
                            "short_description": "Classic double-faced Synchilla polar fleece zip gilet.",
                            "resale_tier": "HIGH",
                            "est_market_value_gbp": 48.0,
                            "visual_cues": [],
                            "ocr_signatures": ["SYNCHILLA"],
                            "text_keywords": ["synchilla"],
                            "negative_keywords": ["better sweater"],
                        },
                    ],
                },
                {
                    "variant_slug": "patagonia_down_nano_puff_vest",
                    "variant_name": "Down Sweater & Nano / Micro Puff",
                    "dimension": "Model",
                    "modality": "visual",
                    "short_description": "Quilted ripstop goose-down Down Sweater vest or brick-quilted PrimaLoft Nano/Micro Puff gilet.",
                    "resale_tier": "HIGH",
                    "est_market_value_gbp": 68.0,
                    "visual_cues": ["quilted ripstop nylon insulated puffer gilet vest with horizontal or brick baffles"],
                    "ocr_signatures": ["PRIMALOFT"],
                    "text_keywords": ["down sweater", "down vest", "down gilet", "nano puff", "micro puff", "micro puffer", "puffer gilet", "insulated", "quilted"],
                    "negative_keywords": ["retro-x", "retro pile", "los gatos", "synchilla", "better sweater"],
                    "sub_styles": [
                        {
                            "variant_slug": "down_sweater_vest",
                            "variant_name": "Down Sweater Goose-Down Vest",
                            "modality": "label",
                            "short_description": "800-fill-power traceable goose down ripstop gilet.",
                            "resale_tier": "PREMIUM",
                            "est_market_value_gbp": 78.0,
                            "visual_cues": [],
                            "ocr_signatures": ["800"],
                            "text_keywords": ["down sweater", "down vest", "down gilet", "goose down"],
                            "negative_keywords": ["nano puff", "micro puff"],
                        },
                        {
                            "variant_slug": "nano_micro_puff_vest",
                            "variant_name": "Nano Puff / Micro Puff Vest",
                            "modality": "label",
                            "short_description": "Lightweight brick-quilted PrimaLoft synthetic insulated gilet.",
                            "resale_tier": "HIGH",
                            "est_market_value_gbp": 62.0,
                            "visual_cues": [],
                            "ocr_signatures": ["PRIMALOFT"],
                            "text_keywords": ["nano puff", "micro puff", "micro puffer", "primaloft"],
                            "negative_keywords": ["goose down"],
                        },
                    ],
                },
            ],
        },
    ),
    (
        ["synchilla", "patagonia fleece"],
        {
            "product_family": "Patagonia Synchilla Fleece",
            "domain": "fashion",
            "research_notes": "Product-specific Patagonia Synchilla models: Snap-T Pullover (with exclusive Patterned/Aztec, Lightweight, and Vintage 90s styles), Full-Zip & Hooded Synchilla Jacket, and Quarter-Zip / Marsupial.",
            "variants": [
                {
                    "variant_slug": "synchilla_snap_t_pullover",
                    "variant_name": "Synchilla Snap-T Pullover",
                    "dimension": "Model",
                    "modality": "visual",
                    "short_description": "Iconic 4-snap nylon placket fleece pullover with contrasting chest flap pocket and Y-Joint sleeves.",
                    "resale_tier": "HIGH",
                    "est_market_value_gbp": 62.0,
                    "visual_cues": [
                        "polar fleece pullover jumper with four snap buttons at neck placket and contrasting nylon chest flap pocket",
                    ],
                    "ocr_signatures": ["SYNCHILLA"],
                    "text_keywords": ["snap-t", "snap t", "4 button", "button neck", "oatmeal", "aztec", "pullover"],
                    "negative_keywords": ["full zip", "full-zip", "zip jacket", "hooded", "quarter zip", "1/4 zip", "gilet", "vest"],
                    "sub_styles": [
                        {
                            "variant_slug": "snap_t_patterned_aztec",
                            "variant_name": "Collector Print / Aztec Snap-T",
                            "modality": "visual",
                            "short_description": "Collectible seasonal all-over print (Aztec, Tundra Cluster, geometric, floral, wildlife).",
                            "resale_tier": "PREMIUM",
                            "est_market_value_gbp": 82.0,
                            "visual_cues": ["colorful patterned geometric tribal aztec or floral all-over print Patagonia Snap-T fleece"],
                            "ocr_signatures": [],
                            "text_keywords": ["aztec", "patterned", "pattern", "print", "printed", "tundra", "geometric", "tribal", "rare print", "abstract"],
                            "negative_keywords": ["solid", "plain", "oatmeal"],
                        },
                        {
                            "variant_slug": "snap_t_lightweight_solid",
                            "variant_name": "Lightweight / Two-Tone Snap-T",
                            "modality": "visual",
                            "short_description": "Classic solid or two-tone colorblock (e.g. Oatmeal/Navy, Nickel) Lightweight Synchilla Snap-T.",
                            "resale_tier": "HIGH",
                            "est_market_value_gbp": 56.0,
                            "visual_cues": ["solid or two-tone colorblock Patagonia Synchilla Snap-T fleece pullover with contrast trim"],
                            "ocr_signatures": [],
                            "text_keywords": ["lightweight", "oatmeal", "two tone", "colourblock", "colorblock", "nickel"],
                            "negative_keywords": ["aztec", "patterned", "tundra", "geometric"],
                        },
                        {
                            "variant_slug": "snap_t_vintage_90s",
                            "variant_name": "Vintage 90s / USA Heavyweight",
                            "modality": "label",
                            "short_description": "Archive 1990s–early 2000s heavyweight Snap-T (Made in USA, Jamaica, or Mexico).",
                            "resale_tier": "PREMIUM",
                            "est_market_value_gbp": 85.0,
                            "visual_cues": [],
                            "ocr_signatures": ["MADE IN USA", "JAMAICA", "MEXICO"],
                            "text_keywords": ["90s", "1990s", "jamaica", "made in usa", "usa made", "early 2000s", "y2k", "vintage"],
                            "negative_keywords": ["lightweight"],
                        },
                    ],
                },
                {
                    "variant_slug": "synchilla_full_zip_jacket",
                    "variant_name": "Synchilla Full-Zip & Jacket",
                    "dimension": "Model",
                    "modality": "visual",
                    "short_description": "Full-length front zip Synchilla fleece jacket (stand-collar or hooded).",
                    "resale_tier": "STANDARD",
                    "est_market_value_gbp": 46.0,
                    "visual_cues": ["full length zipper Patagonia Synchilla polar fleece jacket with zippered hand pockets"],
                    "ocr_signatures": [],
                    "text_keywords": ["full zip", "full-zip", "zip jacket", "fleece jacket", "hooded", "cardigan"],
                    "negative_keywords": ["snap-t", "snap t", "quarter zip", "1/4 zip", "pullover"],
                    "sub_styles": [
                        {
                            "variant_slug": "synchilla_stand_collar_zip",
                            "variant_name": "Stand-Collar Full-Zip",
                            "modality": "label",
                            "short_description": "Classic collar full-zip Synchilla jacket without hood.",
                            "resale_tier": "STANDARD",
                            "est_market_value_gbp": 45.0,
                            "visual_cues": [],
                            "ocr_signatures": [],
                            "text_keywords": ["full zip", "full-zip", "fleece jacket"],
                            "negative_keywords": ["hooded", "hoodie", "hood"],
                        },
                        {
                            "variant_slug": "synchilla_hooded_zip",
                            "variant_name": "Hooded Synchilla Jacket",
                            "modality": "label",
                            "short_description": "Full-zip hooded Synchilla fleece jacket.",
                            "resale_tier": "HIGH",
                            "est_market_value_gbp": 55.0,
                            "visual_cues": [],
                            "ocr_signatures": [],
                            "text_keywords": ["hooded", "hoodie", "hood"],
                            "negative_keywords": [],
                        },
                    ],
                },
                {
                    "variant_slug": "synchilla_quarter_zip_marsupial",
                    "variant_name": "Quarter-Zip & Marsupial Pullover",
                    "dimension": "Model",
                    "modality": "visual",
                    "short_description": "Zip-neck Synchilla pullover or deep-zip Marsupial fleece with kangaroo pocket.",
                    "resale_tier": "STANDARD",
                    "est_market_value_gbp": 44.0,
                    "visual_cues": ["quarter zip neck Patagonia polar fleece pullover without snap buttons"],
                    "ocr_signatures": [],
                    "text_keywords": ["quarter zip", "1/4 zip", "half zip", "zip neck", "marsupial"],
                    "negative_keywords": ["snap-t", "snap t", "full zip", "full-zip"],
                    "sub_styles": [],
                },
            ],
        },
    ),
    (
        ["bape hoodie", "bathing ape hoodie", "bape zip", "bathing ape"],
        {
            "product_family": "A Bathing Ape (BAPE) Hoodies",
            "domain": "fashion",
            "research_notes": "Mainline Japan BAPE models (Shark Full-Zip WGM, Tiger/Baby Milo, College/Ape Head) separated cleanly from diffusion line AAPE.",
            "variants": [
                {
                    "variant_slug": "bape_shark_full_zip",
                    "variant_name": "Shark Full-Zip Hoodie (WGM)",
                    "dimension": "Model",
                    "modality": "visual",
                    "short_description": "Signature full-zip-over-hood Shark face appliqué with chenille 'WGM' varsity hood patches.",
                    "resale_tier": "PREMIUM",
                    "est_market_value_gbp": 145.0,
                    "visual_cues": [
                        "BAPE full zip camo hoodie with shark teeth eyes applique on hood and chenille WGM letters",
                    ],
                    "ocr_signatures": ["WGM"],
                    "text_keywords": ["shark", "wgm", "full-zip", "full zip", "ponr"],
                    "negative_keywords": ["aape", "bape style", "unbranded", "inspired"],
                    "sub_styles": [
                        {
                            "variant_slug": "shark_abc_1st_camo",
                            "variant_name": "1st / ABC / Color Camo Shark",
                            "modality": "label",
                            "short_description": "All-over 1st Camo, ABC Camo, or Color Camo Shark full-zip.",
                            "resale_tier": "PREMIUM",
                            "est_market_value_gbp": 150.0,
                            "visual_cues": [],
                            "ocr_signatures": ["WGM"],
                            "text_keywords": ["camo", "1st camo", "abc camo", "red camo", "blue camo", "green camo"],
                            "negative_keywords": ["split", "half", "neighborhood", "mastermind"],
                        },
                        {
                            "variant_slug": "shark_split_collab",
                            "variant_name": "Split Camo / Collab / Double Hood",
                            "modality": "label",
                            "short_description": "Half-and-half Split Camo, Double-Hood, or Neighborhood / Undefeated / Mastermind collab.",
                            "resale_tier": "PREMIUM",
                            "est_market_value_gbp": 185.0,
                            "visual_cues": [],
                            "ocr_signatures": [],
                            "text_keywords": ["split", "half", "double hood", "neighborhood", "mastermind", "undefeated", "psg", "xo"],
                            "negative_keywords": [],
                        },
                    ],
                },
                {
                    "variant_slug": "bape_tiger_milo_graphic",
                    "variant_name": "Tiger & Baby Milo Hoodie",
                    "dimension": "Model",
                    "modality": "label",
                    "short_description": "Tiger motif full-zip hood or Baby Milo / Bapesta cartoon graphic hoodie.",
                    "resale_tier": "HIGH",
                    "est_market_value_gbp": 115.0,
                    "visual_cues": [],
                    "ocr_signatures": ["BABY MILO", "FUNTHERA"],
                    "text_keywords": ["tiger", "panda", "baby milo", "milo", "kaws", "bapesta"],
                    "negative_keywords": ["aape", "bape style"],
                    "sub_styles": [],
                },
                {
                    "variant_slug": "bape_college_ape_head",
                    "variant_name": "College Logo & Ape Head Hoodie",
                    "dimension": "Model",
                    "modality": "visual",
                    "short_description": "Mainline A Bathing Ape pullover or standard zip hoodie featuring College arch logo or Ape Head chest print.",
                    "resale_tier": "HIGH",
                    "est_market_value_gbp": 95.0,
                    "visual_cues": ["pullover cotton hoodie with A Bathing Ape arch college logo and ape head graphic on chest"],
                    "ocr_signatures": ["A BATHING APE", "BUSY WORKS"],
                    "text_keywords": ["college", "ape head", "busy works", "pullover", "spellout", "general"],
                    "negative_keywords": ["shark", "wgm", "aape", "bape style"],
                    "sub_styles": [],
                },
                {
                    "variant_slug": "aape_diffusion_hoodie",
                    "variant_name": "AAPE by A Bathing Ape",
                    "dimension": "Model",
                    "modality": "label",
                    "short_description": "Diffusion sub-brand 'AAPE NOW / Moon Face' hoodie — significantly lower resale value than mainline BAPE.",
                    "resale_tier": "BUDGET",
                    "est_market_value_gbp": 42.0,
                    "visual_cues": [],
                    "ocr_signatures": ["AAPE", "AAPE NOW", "AAPE UNIVERSE"],
                    "text_keywords": ["aape", "aape by", "moon face", "aape now"],
                    "negative_keywords": [],
                    "sub_styles": [],
                },
            ],
        },
    ),
    (
        ["detroit jacket", "carhartt detroit", "carhartt jacket"],
        {
            "product_family": "Carhartt Detroit Jackets",
            "domain": "fashion",
            "research_notes": "Product-specific Detroit editions: Vintage USA Workwear (J97 Sandstone / J01 Firm Duck), Carhartt WIP OG Detroit (loose original cut), and Carhartt WIP Standard Detroit.",
            "variants": [
                {
                    "variant_slug": "vintage_workwear_detroit_j01_j97",
                    "variant_name": "Vintage Workwear Detroit (J01 / J97)",
                    "dimension": "Model",
                    "modality": "visual",
                    "short_description": "Authentic workwear duck-canvas Detroit (J97 Moss/Sandstone, J01 Black/Brown, 100 Years USA archive) with striped blanket lining.",
                    "resale_tier": "PREMIUM",
                    "est_market_value_gbp": 165.0,
                    "visual_cues": [
                        "faded distressed heavy duck canvas vintage Carhartt Detroit work jacket with corduroy collar and chest zip",
                    ],
                    "ocr_signatures": ["J97", "J01", "J43", "6BLJ", "MADE IN USA", "RN 14806", "100 YEARS"],
                    "text_keywords": ["j97", "j01", "j43", "6blj", "sandstone", "made in usa", "usa made", "100 years", "1989", "blanket lined", "mos", "moss"],
                    "negative_keywords": ["wip", "carhartt wip", "og detroit"],
                    "sub_styles": [
                        {
                            "variant_slug": "j97_sandstone_duck",
                            "variant_name": "J97 Sandstone (Faded / leatherette)",
                            "modality": "label",
                            "short_description": "Washed Sandstone duck canvas (MOS / PTL / BRN) with faux-leather chest patch and blanket lining.",
                            "resale_tier": "PREMIUM",
                            "est_market_value_gbp": 185.0,
                            "visual_cues": [],
                            "ocr_signatures": ["J97"],
                            "text_keywords": ["j97", "sandstone", "mos", "moss", "ptl", "petrol", "leather patch"],
                            "negative_keywords": ["j01"],
                        },
                        {
                            "variant_slug": "j01_firm_duck_usa",
                            "variant_name": "J01 / 6BLJ Firm Duck & USA Archive",
                            "modality": "label",
                            "short_description": "Rigid 12oz ring-spun cotton duck J01 / 6BLJ / 100-Year anniversary USA work jacket.",
                            "resale_tier": "PREMIUM",
                            "est_market_value_gbp": 160.0,
                            "visual_cues": [],
                            "ocr_signatures": ["J01", "6BLJ", "100 YEARS", "MADE IN USA"],
                            "text_keywords": ["j01", "6blj", "100 years", "made in usa", "usa made", "1989"],
                            "negative_keywords": ["j97"],
                        },
                    ],
                },
                {
                    "variant_slug": "carhartt_wip_og_detroit",
                    "variant_name": "Carhartt WIP OG Detroit",
                    "dimension": "Model",
                    "modality": "visual",
                    "short_description": "Carhartt WIP 'Original Loose Fit' OG Detroit in heavyweight 12oz organic Dearborn canvas with diamond-quilted or blanket lining.",
                    "resale_tier": "HIGH",
                    "est_market_value_gbp": 125.0,
                    "visual_cues": [
                        "boxy loose fit Carhartt WIP OG Detroit canvas jacket with corduroy collar and woven square chest patch",
                    ],
                    "ocr_signatures": ["OG DETROIT", "WORK IN PROGRESS"],
                    "text_keywords": ["og detroit", "og ", "original fit", "quilted lining", "diamond quilted"],
                    "negative_keywords": ["j97", "j01", "made in usa", "harrington"],
                    "sub_styles": [
                        {
                            "variant_slug": "wip_og_winter_lined",
                            "variant_name": "Winter Quilted / Blanket Lined",
                            "modality": "label",
                            "short_description": "Insulated winter OG Detroit with diamond-quilted or striped blanket body lining.",
                            "resale_tier": "HIGH",
                            "est_market_value_gbp": 132.0,
                            "visual_cues": [],
                            "ocr_signatures": [],
                            "text_keywords": ["quilted", "blanket", "winter", "lined", "padded"],
                            "negative_keywords": ["unlined", "summer", "spring"],
                        },
                        {
                            "variant_slug": "wip_og_summer_unlined",
                            "variant_name": "Spring / Summer Unlined",
                            "modality": "label",
                            "short_description": "Lightweight poplin/unlined SS edition OG Detroit.",
                            "resale_tier": "STANDARD",
                            "est_market_value_gbp": 105.0,
                            "visual_cues": [],
                            "ocr_signatures": [],
                            "text_keywords": ["summer", "spring", "unlined", "lightweight"],
                            "negative_keywords": ["quilted", "blanket", "winter"],
                        },
                    ],
                },
                {
                    "variant_slug": "carhartt_wip_standard_detroit",
                    "variant_name": "Carhartt WIP Standard Detroit",
                    "dimension": "Model",
                    "modality": "visual",
                    "short_description": "Modern tailored-fit Carhartt WIP Detroit / canvas zip jacket.",
                    "resale_tier": "STANDARD",
                    "est_market_value_gbp": 95.0,
                    "visual_cues": [
                        "clean modern slim regular fit Carhartt WIP Dearborn canvas Detroit zip jacket",
                    ],
                    "ocr_signatures": ["WORK IN PROGRESS"],
                    "text_keywords": ["wip", "dearborn", "fleece lining", "canvas jacket"],
                    "negative_keywords": ["og detroit", "j97", "j01", "made in usa"],
                    "sub_styles": [],
                },
            ],
        },
    ),
    (
        ["superdry henley", "super dry henley"],
        {
            "product_family": "Superdry Henley & Grandad Tops",
            "domain": "fashion",
            "research_notes": (
                "Superdry henleys are overwhelmingly the core button-placket grandad/heritage henley (waffle, ribbed or "
                "slub jersey, long or short sleeve). The Hooded Henley is a rarer, distinctive variant and is feature-gated: "
                "a listing only lands there if it names the hood or the photo clearly shows one. Plain listings with no "
                "evidence stay unclassified instead of being forced into a model."
            ),
            "variants": [
                {
                    "variant_slug": "superdry_y2k_grandad_henley",
                    "variant_name": "Classic Grandad / Heritage Henley",
                    "dimension": "Model",
                    "modality": "visual",
                    "short_description": "Core Superdry button-placket grandad henley without a hood (waffle, ribbed or slub jersey).",
                    "resale_tier": "STANDARD",
                    "est_market_value_gbp": 16.0,
                    "visual_cues": [
                        "a mens henley top with a short button placket at the collarless neck and no hood",
                        "a waffle or ribbed knit grandad top with buttons at the neck",
                    ],
                    "ocr_signatures": ["SUPERDRY", "ORANGE LABEL"],
                    "text_keywords": ["grandad", "granddad", "heritage", "waffle", "ribbed", "orange label", "slub", "striped"],
                    "negative_keywords": ["hooded", "hoodie", "hoody", "hood", "cami", "ruched", "tank", "polo shirt", "off shoulder"],
                    "sub_styles": [
                        {
                            "variant_slug": "superdry_henley_long_sleeve",
                            "variant_name": "Long-Sleeve",
                            "modality": "label",
                            "short_description": "Long-sleeve grandad henley.",
                            "resale_tier": "STANDARD",
                            "est_market_value_gbp": 17.0,
                            "visual_cues": [],
                            "ocr_signatures": [],
                            "text_keywords": ["long sleeve", "long-sleeve", "longsleeve", "long sleeved"],
                            "negative_keywords": ["short sleeve", "short-sleeve", "short sleeved"],
                        },
                        {
                            "variant_slug": "superdry_henley_short_sleeve",
                            "variant_name": "Short-Sleeve",
                            "modality": "label",
                            "short_description": "Short-sleeve button-neck henley tee.",
                            "resale_tier": "BUDGET",
                            "est_market_value_gbp": 12.0,
                            "visual_cues": [],
                            "ocr_signatures": [],
                            "text_keywords": ["short sleeve", "short-sleeve", "short sleeved", "tshirt", "t-shirt", "tee"],
                            "negative_keywords": ["long sleeve", "long-sleeve", "long sleeved"],
                        },
                    ],
                },
                {
                    "variant_slug": "superdry_womens_y2k_ruched_henley",
                    "variant_name": "Women's Fitted / Ruched Henley",
                    "dimension": "Model",
                    "modality": "visual",
                    "short_description": "Women's fitted, ruched, babydoll or lace-trim button-front henley top.",
                    "resale_tier": "STANDARD",
                    "est_market_value_gbp": 15.0,
                    "visual_cues": ["a womens fitted ruched button front henley top", "a womens lace trim cami henley top"],
                    "ocr_signatures": [],
                    "text_keywords": ["ruched", "fitted", "cami", "babydoll", "lace", "size 8", "size 10", "size 12"],
                    "negative_keywords": ["hooded", "hood", "grandad", "tank", "polo shirt", "mens"],
                    "sub_styles": [],
                },
                {
                    "variant_slug": "superdry_hooded_henley",
                    "variant_name": "Hooded Henley",
                    "dimension": "Model",
                    "modality": "feature",
                    "short_description": "Rarer button-placket henley with an attached hood — only assigned when the hood is named or clearly visible.",
                    "resale_tier": "HIGH",
                    "est_market_value_gbp": 28.0,
                    "visual_cues": [
                        "a long sleeve henley top with an attached hood",
                        "a button neck pullover top with a hood behind the neck",
                    ],
                    "ocr_signatures": [],
                    "text_keywords": ["hooded", "hood", "hoodie", "hoody", "hooded henley", "hooded grandad"],
                    "negative_keywords": ["cami", "ruched"],
                    "sub_styles": [],
                },
            ],
        },
    ),
    (
        ["ixus", "cannon ixus", "canon ixus", "powershot elph"],
        {
            "product_family": "Canon IXUS / PowerShot ELPH Cameras",
            "domain": "tech",
            "research_notes": "Exact Canon IXUS camera families with model-exclusive exact camera sub-models (CCD IXUS 70/75/80, 12x Wi-Fi 285/265 HS, 20MP 175/185/190, Mid-Era 105–155, Early Y2K 30–55).",
            "variants": [
                {
                    "variant_slug": "ixus_70_75_80_ccd",
                    "variant_name": "IXUS 70 / 75 / 80 IS (CCD)",
                    "dimension": "Model",
                    "modality": "visual",
                    "short_description": "Cult 2007–2008 metal CCD digicams with optical viewfinder window and 3x zoom (5.8-17.4mm).",
                    "resale_tier": "PREMIUM",
                    "est_market_value_gbp": 135.0,
                    "visual_cues": [
                        "boxy flat rectangular vintage silver metallic compact camera with small optical viewfinder window above 3x lens",
                    ],
                    "ocr_signatures": [
                        "IXUS 70", "IXUS 75", "IXUS 80", "IXUS 85", "SD1000", "SD750", "SD1100",
                        "7.1 MEGA PIXELS", "8.0 MEGA PIXELS", "5.8-17.4", "6.2-18.6", "3x",
                    ],
                    "text_keywords": [
                        "ixus 70", "ixus 75", "ixus 80", "ixus 85", "sd1000", "sd750", "sd1100",
                        "7.1mp", "ixus 60", "ixus 65",
                    ],
                    "negative_keywords": ["185", "190", "285", "275", "265", "175", "160", "12x", "10x", "8x", "wifi", "wi-fi"],
                    "sub_styles": [
                        {
                            "variant_slug": "ixus_70_sd1000",
                            "variant_name": "IXUS 70 (SD1000 Boxy)",
                            "modality": "label",
                            "short_description": "Iconic flat-edge two-tone 7.1MP IXUS 70 / PowerShot SD1000.",
                            "resale_tier": "PREMIUM",
                            "est_market_value_gbp": 150.0,
                            "visual_cues": [],
                            "ocr_signatures": ["IXUS 70", "SD1000", "7.1 MEGA PIXELS"],
                            "text_keywords": ["ixus 70", "sd1000"],
                            "negative_keywords": ["ixus 75", "ixus 80", "ixus 85"],
                        },
                        {
                            "variant_slug": "ixus_75_sd750",
                            "variant_name": "IXUS 75 (3.0\" LCD)",
                            "modality": "label",
                            "short_description": "7.1MP CCD IXUS 75 / SD750 with oversized chrome lens ring and 3-inch screen.",
                            "resale_tier": "PREMIUM",
                            "est_market_value_gbp": 130.0,
                            "visual_cues": [],
                            "ocr_signatures": ["IXUS 75", "SD750"],
                            "text_keywords": ["ixus 75", "sd750"],
                            "negative_keywords": ["ixus 70", "ixus 80"],
                        },
                        {
                            "variant_slug": "ixus_80_85_is",
                            "variant_name": "IXUS 80 / 85 IS",
                            "modality": "label",
                            "short_description": "8.0MP/10.0MP optically stabilised CCD digicam with viewfinder.",
                            "resale_tier": "HIGH",
                            "est_market_value_gbp": 125.0,
                            "visual_cues": [],
                            "ocr_signatures": ["IXUS 80", "IXUS 85", "SD1100", "8.0 MEGA PIXELS"],
                            "text_keywords": ["ixus 80", "ixus 85", "sd1100"],
                            "negative_keywords": ["ixus 70", "ixus 75"],
                        },
                    ],
                },
                {
                    "variant_slug": "ixus_285_hs_wifi",
                    "variant_name": "IXUS 285 / 275 / 265 HS (12x Wi-Fi)",
                    "dimension": "Model",
                    "modality": "visual",
                    "short_description": "Flagship pocket travel zoom with 12x Optical Zoom (25-300mm), HS CMOS sensor, and Wi-Fi/NFC.",
                    "resale_tier": "PREMIUM",
                    "est_market_value_gbp": 175.0,
                    "visual_cues": [
                        "modern sleek matte black silver or purple compact camera with thick 12x zoom lens barrel and Wi-Fi badge",
                    ],
                    "ocr_signatures": [
                        "285 HS", "275 HS", "265 HS", "ELPH 360", "12x", "4.5-54.0", "25-300", "Wi-Fi", "FULL HD",
                    ],
                    "text_keywords": ["ixus 285", "285 hs", "285hs", "ixus 275", "ixus 265", "elph 360", "12x zoom"],
                    "negative_keywords": ["ixus 70", "ixus 75", "ixus 80", "ixus 185", "8x"],
                    "sub_styles": [
                        {
                            "variant_slug": "ixus_285_hs_exact",
                            "variant_name": "IXUS 285 HS (20.2MP)",
                            "modality": "label",
                            "short_description": "Top-spec 20.2MP IXUS 285 HS / ELPH 360 HS.",
                            "resale_tier": "PREMIUM",
                            "est_market_value_gbp": 185.0,
                            "visual_cues": [],
                            "ocr_signatures": ["285 HS", "ELPH 360"],
                            "text_keywords": ["ixus 285", "285 hs", "285hs", "elph 360"],
                            "negative_keywords": ["265", "275"],
                        },
                        {
                            "variant_slug": "ixus_265_275_hs",
                            "variant_name": "IXUS 265 / 275 HS",
                            "modality": "label",
                            "short_description": "16MP/20MP 12x zoom Wi-Fi sibling models.",
                            "resale_tier": "HIGH",
                            "est_market_value_gbp": 145.0,
                            "visual_cues": [],
                            "ocr_signatures": ["265 HS", "275 HS"],
                            "text_keywords": ["ixus 265", "265 hs", "ixus 275", "275 hs"],
                            "negative_keywords": ["285"],
                        },
                    ],
                },
                {
                    "variant_slug": "ixus_185_190_175",
                    "variant_name": "IXUS 175 / 185 / 190 (20MP)",
                    "dimension": "Model",
                    "modality": "visual",
                    "short_description": "Late-generation 20.0MP compact cameras with 8x or 10x Optical Zoom.",
                    "resale_tier": "HIGH",
                    "est_market_value_gbp": 110.0,
                    "visual_cues": [
                        "slim rounded silver red or black compact camera with 8x or 10x Optical Zoom printed on lens ring and no viewfinder",
                    ],
                    "ocr_signatures": [
                        "IXUS 185", "IXUS 190", "IXUS 180", "IXUS 175", "IXUS 160", "IXUS 165", "IXUS 170",
                        "8x", "10x", "5.0-40.0", "4.3-43.0", "20.0",
                    ],
                    "text_keywords": [
                        "ixus 185", "ixus 190", "ixus 180", "ixus 175", "ixus 170", "ixus 165", "ixus 160",
                        "elph 180", "elph 190", "elph 200", "20mp", "20.0mp", "8x", "10x",
                    ],
                    "negative_keywords": ["ixus 70", "ixus 80", "ixus 285", "12x"],
                    "sub_styles": [
                        {
                            "variant_slug": "ixus_190_180_wifi",
                            "variant_name": "IXUS 190 / 180 (10x Wi-Fi)",
                            "modality": "label",
                            "short_description": "10x IS Optical Zoom with built-in Wi-Fi / NFC transfer.",
                            "resale_tier": "HIGH",
                            "est_market_value_gbp": 122.0,
                            "visual_cues": [],
                            "ocr_signatures": ["IXUS 190", "IXUS 180", "10x"],
                            "text_keywords": ["ixus 190", "ixus 180", "elph 190", "elph 200"],
                            "negative_keywords": ["ixus 185", "ixus 175"],
                        },
                        {
                            "variant_slug": "ixus_185_175_8x",
                            "variant_name": "IXUS 185 / 175 (8x 20MP)",
                            "modality": "label",
                            "short_description": "Ultra-light 20.0MP 8x zoom pocket camera.",
                            "resale_tier": "HIGH",
                            "est_market_value_gbp": 102.0,
                            "visual_cues": [],
                            "ocr_signatures": ["IXUS 185", "IXUS 175", "8x"],
                            "text_keywords": ["ixus 185", "ixus 175", "elph 180"],
                            "negative_keywords": ["ixus 190", "ixus 180"],
                        },
                    ],
                },
                {
                    "variant_slug": "ixus_105_155_mid",
                    "variant_name": "IXUS 105–155 Slim Metal",
                    "dimension": "Model",
                    "modality": "visual",
                    "short_description": "Slim anodised colourful 12MP–16MP models (pink, aqua, silver, black) with 4x–5x IS zoom.",
                    "resale_tier": "HIGH",
                    "est_market_value_gbp": 88.0,
                    "visual_cues": [
                        "curved anodized pink blue silver or black slim compact camera with 4x or 5x zoom ring",
                    ],
                    "ocr_signatures": [
                        "IXUS 105", "IXUS 115", "IXUS 125", "IXUS 130", "IXUS 132", "IXUS 135", "IXUS 140", "IXUS 145", "IXUS 155",
                        "12.1", "14.1", "16.0", "4x", "5x",
                    ],
                    "text_keywords": [
                        "ixus 105", "ixus 115", "ixus 125", "ixus 130", "ixus 132", "ixus 135",
                        "ixus 140", "ixus 145", "ixus 150", "ixus 155", "ixus 95", "ixus 100",
                    ],
                    "negative_keywords": ["ixus 285", "ixus 185", "ixus 70"],
                    "sub_styles": [],
                },
                {
                    "variant_slug": "ixus_early_classic_40_50",
                    "variant_name": "Early Y2K IXUS 30–55 / 400",
                    "dimension": "Model",
                    "modality": "visual",
                    "short_description": "Early-2000s stainless-steel Y2K CCD digicams (3.2MP–5.0MP) with optical viewfinder.",
                    "resale_tier": "STANDARD",
                    "est_market_value_gbp": 65.0,
                    "visual_cues": [
                        "chunky early 2000s stainless steel pocket digital camera with optical viewfinder and AiAF badge",
                    ],
                    "ocr_signatures": [
                        "IXUS 30", "IXUS 40", "IXUS 50", "IXUS 55", "IXUS 400", "IXUS 500", "AiAF", "3.2", "4.0", "5.0 MEGA PIXELS",
                    ],
                    "text_keywords": [
                        "ixus 30", "ixus 40", "ixus 50", "ixus 55", "ixus 400", "ixus 430", "ixus 500", "ixus ii", "ixus v2", "ixus v3",
                    ],
                    "negative_keywords": ["285", "185", "190", "12x", "wifi"],
                    "sub_styles": [],
                },
            ],
        },
    ),
    (
        ["cyber-shot", "cybershot", "sony dsc"],
        {
            "product_family": "Sony Cyber-shot Compact Cameras",
            "domain": "tech",
            "research_notes": "Product-specific Sony Cyber-shot series with model-exclusive camera sub-models (RX100 Pro, DSC-T Sliding Y2K, DSC-W Pocket, WX/HX Superzoom).",
            "variants": [
                {
                    "variant_slug": "cybershot_rx100_series",
                    "variant_name": "RX100 Pro 1-Inch Series",
                    "dimension": "Model",
                    "modality": "visual",
                    "short_description": "Enthusiast 1-inch sensor compact with matte black body and Zeiss Vario-Sonnar T* f/1.8 lens.",
                    "resale_tier": "PREMIUM",
                    "est_market_value_gbp": 260.0,
                    "visual_cues": ["matte black pro compact camera with large Zeiss T* lens control ring"],
                    "ocr_signatures": ["RX100", "Vario-Sonnar", "ZEISS", "1.8-4.9", "1.8-2.8"],
                    "text_keywords": ["rx100", "dsc-rx100", "rx100m2", "rx100m3", "rx100m4", "rx100m5"],
                    "negative_keywords": ["dsc-w", "dsc-t", "dsc-s"],
                    "sub_styles": [
                        {
                            "variant_slug": "rx100_mk3_mk5_evf",
                            "variant_name": "RX100 III / IV / V (Pop-Up EVF)",
                            "modality": "label",
                            "short_description": "Fast f/1.8-2.8 Zeiss lens with pop-up OLED viewfinder.",
                            "resale_tier": "PREMIUM",
                            "est_market_value_gbp": 320.0,
                            "visual_cues": [],
                            "ocr_signatures": ["1.8-2.8", "RX100M3", "RX100M4", "RX100M5"],
                            "text_keywords": ["rx100 iii", "rx100 iv", "rx100 v", "rx100m3", "rx100m4", "rx100m5", "mk3", "mark iii"],
                            "negative_keywords": [],
                        },
                        {
                            "variant_slug": "rx100_mk1_mk2",
                            "variant_name": "RX100 I / II (Classic 1-Inch)",
                            "modality": "label",
                            "short_description": "Original RX100 / RX100 II 20.2MP 1-inch pocket camera.",
                            "resale_tier": "PREMIUM",
                            "est_market_value_gbp": 215.0,
                            "visual_cues": [],
                            "ocr_signatures": ["1.8-4.9", "RX100M2"],
                            "text_keywords": ["rx100m2", "rx100 ii", "mk2", "mark ii"],
                            "negative_keywords": ["rx100m3", "rx100m4", "rx100m5"],
                        },
                    ],
                },
                {
                    "variant_slug": "cybershot_t_sliding_series",
                    "variant_name": "DSC-T Sliding-Cover Series",
                    "dimension": "Model",
                    "modality": "visual",
                    "short_description": "Ultra-thin Y2K digicam with vertical sliding front lens shield and internal periscope zoom.",
                    "resale_tier": "HIGH",
                    "est_market_value_gbp": 115.0,
                    "visual_cues": ["ultra flat rectangular metallic camera with sliding front faceplate cover and corner lens"],
                    "ocr_signatures": ["DSC-T", "Vario-Tessar"],
                    "text_keywords": ["dsc-t", "t10", "t20", "t70", "t77", "t90", "t99", "t100", "t200", "t300", "sliding"],
                    "negative_keywords": ["rx100", "dsc-w830", "dsc-hx"],
                    "sub_styles": [],
                },
                {
                    "variant_slug": "cybershot_w_pocket_series",
                    "variant_name": "DSC-W Pocket Series",
                    "dimension": "Model",
                    "modality": "visual",
                    "short_description": "Classic extending-barrel pocket Cyber-shot cameras (W80 / W350 / W810 / W830).",
                    "resale_tier": "STANDARD",
                    "est_market_value_gbp": 80.0,
                    "visual_cues": ["compact silver black or pink pocket camera with circular telescoping center zoom lens"],
                    "ocr_signatures": ["DSC-W", "W830", "W810", "W800", "W350", "W530", "W570", "SteadyShot"],
                    "text_keywords": ["dsc-w", "w830", "w810", "w800", "w570", "w530", "w350", "w320", "w120", "w80", "w55"],
                    "negative_keywords": ["rx100", "dsc-t", "dsc-hx"],
                    "sub_styles": [
                        {
                            "variant_slug": "dsc_w810_w830_20mp",
                            "variant_name": "DSC-W800 / W810 / W830 (20MP)",
                            "modality": "label",
                            "short_description": "Late-era 20.1MP Super HAD CCD pocket series.",
                            "resale_tier": "HIGH",
                            "est_market_value_gbp": 92.0,
                            "visual_cues": [],
                            "ocr_signatures": ["W830", "W810", "W800", "20.1"],
                            "text_keywords": ["w830", "w810", "w800", "dsc-w830", "dsc-w810"],
                            "negative_keywords": [],
                        },
                        {
                            "variant_slug": "dsc_w55_w350_vintage_ccd",
                            "variant_name": "Vintage CCD W55–W350 (Viewfinder)",
                            "modality": "label",
                            "short_description": "2006–2010 Carl Zeiss CCD W-series pocket cameras.",
                            "resale_tier": "STANDARD",
                            "est_market_value_gbp": 75.0,
                            "visual_cues": [],
                            "ocr_signatures": ["W350", "W120", "W80", "W55"],
                            "text_keywords": ["w350", "w320", "w120", "w80", "w55", "w570", "w530"],
                            "negative_keywords": ["w830", "w810", "w800"],
                        },
                    ],
                },
                {
                    "variant_slug": "cybershot_hx_wx_superzoom",
                    "variant_name": "DSC-WX / HX Travel Superzoom",
                    "dimension": "Model",
                    "modality": "visual",
                    "short_description": "Modern CMOS Exmor R travel superzoom models (10x–30x Sony G / Zeiss zoom, Wi-Fi).",
                    "resale_tier": "HIGH",
                    "est_market_value_gbp": 145.0,
                    "visual_cues": ["modern travel zoom camera with mode dial, grip, and 10x 20x or 30x G lens"],
                    "ocr_signatures": ["WX220", "WX350", "WX500", "HX50", "HX60", "HX80", "HX90", "Sony Lens G", "20x", "30x"],
                    "text_keywords": ["wx220", "wx350", "wx500", "hx50", "hx60", "hx80", "hx90", "dsc-wx", "dsc-hx"],
                    "negative_keywords": ["dsc-t", "dsc-w800"],
                    "sub_styles": [],
                },
            ],
        },
    ),
]


class MultiLLMRouter:
    """Manages API credentials, provider load-balancing, cooldowns, and structured JSON Deep Research."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.config: Dict[str, Any] = dict(DEFAULT_AI_CONFIG)
        self._rr_cursor: int = 0
        self._cooldowns: Dict[str, float] = {}  # endpoint_id -> unix timestamp when usable again
        self._telemetry: Dict[str, Dict[str, Any]] = {}
        self._load_config()

    def _parse_key_list(self, val: Any) -> List[str]:
        if isinstance(val, list):
            raw = val
        elif isinstance(val, str):
            raw = re.split(r"[\n,;]+", val)
        else:
            raw = []
        cleaned = []
        for k in raw:
            s = str(k).strip()
            if s and "****" not in s and s not in cleaned:
                cleaned.append(s)
        return cleaned

    def _load_config(self) -> None:
        with self._lock:
            cfg = dict(DEFAULT_AI_CONFIG)
            if os.path.exists(AI_CONFIG_PATH) and os.path.getsize(AI_CONFIG_PATH) > 0:
                try:
                    with open(AI_CONFIG_PATH, "r", encoding="utf-8") as f:
                        saved = json.load(f)
                        if isinstance(saved, dict):
                            cfg.update(saved)
                except Exception as exc:
                    logger.warning("Could not read ai_config.json: %s", exc)

            # Merge environment variables if present
            for env_name, target_key in [
                ("GEMINI_API_KEY", "gemini_api_keys"),
                ("GOOGLE_API_KEY", "gemini_api_keys"),
                ("GROQ_API_KEY", "groq_api_keys"),
                ("OPENROUTER_API_KEY", "openrouter_api_keys"),
                ("MISTRAL_API_KEY", "mistral_api_keys"),
            ]:
                env_val = os.environ.get(env_name, "").strip()
                if env_val:
                    existing = self._parse_key_list(cfg.get(target_key))
                    for k in self._parse_key_list(env_val):
                        if k not in existing:
                            existing.append(k)
                    cfg[target_key] = existing

            for list_field in ("gemini_api_keys", "groq_api_keys", "openrouter_api_keys", "mistral_api_keys"):
                cfg[list_field] = self._parse_key_list(cfg.get(list_field))

            self.config = cfg

    def save_config(self, updates: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            for key, val in updates.items():
                if key not in DEFAULT_AI_CONFIG:
                    continue
                if key.endswith("_api_keys"):
                    # Preserve existing keys if masked preview was passed back unchanged, else replace
                    if val is None:
                        continue
                    if isinstance(val, str) and "****" in val:
                        continue
                    self.config[key] = self._parse_key_list(val)
                elif key == "custom_openai_api_key":
                    if isinstance(val, str) and "****" in val:
                        continue
                    self.config[key] = str(val or "").strip()
                else:
                    self.config[key] = val

            try:
                os.makedirs(os.path.dirname(AI_CONFIG_PATH), exist_ok=True)
                with open(AI_CONFIG_PATH, "w", encoding="utf-8") as f:
                    json.dump(self.config, f, indent=2)
            except Exception as exc:
                logger.warning("Failed to save ai_config.json: %s", exc)

            return self.get_public_status()

    @staticmethod
    def _mask_key(k: str) -> str:
        if len(k) <= 8:
            return "****"
        return f"{k[:4]}****{k[-4:]}"

    def _detect_ollama_models(self) -> List[str]:
        base = (self.config.get("ollama_base_url") or "http://127.0.0.1:11434").rstrip("/")
        try:
            r = httpx.get(f"{base}/api/tags", timeout=1.2)
            if r.status_code == 200:
                data = r.json()
                return [m.get("name", "") for m in data.get("models", []) if m.get("name")]
        except Exception:
            pass
        return []

    def get_public_status(self) -> Dict[str, Any]:
        with self._lock:
            ollama_models = self._detect_ollama_models()
            endpoints = self._build_endpoints(ollama_models=ollama_models)
            now = time.time()

            from .variant_vision import variant_vision_service
            vision_status = variant_vision_service.get_engine_status()

            return {
                "routing_strategy": self.config.get("routing_strategy", "spread"),
                "gemini_model": self.config.get("gemini_model", "gemini-2.5-flash"),
                "enable_google_search_grounding": bool(self.config.get("enable_google_search_grounding", True)),
                "gemini_key_count": len(self.config.get("gemini_api_keys", [])),
                "gemini_keys_masked": [self._mask_key(k) for k in self.config.get("gemini_api_keys", [])],
                "gemini_keys_raw": "\n".join(self.config.get("gemini_api_keys", [])),
                "groq_model": self.config.get("groq_model", "llama-3.3-70b-versatile"),
                "groq_key_count": len(self.config.get("groq_api_keys", [])),
                "groq_keys_masked": [self._mask_key(k) for k in self.config.get("groq_api_keys", [])],
                "groq_keys_raw": "\n".join(self.config.get("groq_api_keys", [])),
                "openrouter_model": self.config.get("openrouter_model", "google/gemma-3-27b-it:free"),
                "openrouter_key_count": len(self.config.get("openrouter_api_keys", [])),
                "openrouter_keys_masked": [self._mask_key(k) for k in self.config.get("openrouter_api_keys", [])],
                "openrouter_keys_raw": "\n".join(self.config.get("openrouter_api_keys", [])),
                "mistral_model": self.config.get("mistral_model", "mistral-small-latest"),
                "mistral_key_count": len(self.config.get("mistral_api_keys", [])),
                "mistral_keys_raw": "\n".join(self.config.get("mistral_api_keys", [])),
                "custom_openai_base_url": self.config.get("custom_openai_base_url", ""),
                "custom_openai_model": self.config.get("custom_openai_model", ""),
                "has_custom_openai_key": bool(self.config.get("custom_openai_api_key")),
                "ollama_base_url": self.config.get("ollama_base_url", "http://127.0.0.1:11434"),
                "ollama_detected_models": ollama_models,
                "active_cloud_endpoints": len(endpoints),
                "endpoints_summary": [
                    {
                        "id": ep["id"],
                        "provider": ep["provider"],
                        "model": ep["model"],
                        "grounded": ep.get("grounded", False),
                        "cooldown_remaining_s": max(0, int(self._cooldowns.get(ep["id"], 0) - now)),
                        "calls": self._telemetry.get(ep["id"], {}).get("calls", 0),
                        "errors": self._telemetry.get(ep["id"], {}).get("errors", 0),
                        "last_latency_ms": self._telemetry.get(ep["id"], {}).get("last_latency_ms"),
                    }
                    for ep in endpoints
                ],
                "local_vision": vision_status,
            }

    def _build_endpoints(self, ollama_models: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        endpoints: List[Dict[str, Any]] = []
        cfg = self.config

        for idx, k in enumerate(cfg.get("gemini_api_keys", [])):
            endpoints.append({
                "id": f"gemini_{idx + 1}",
                "provider": "gemini",
                "api_key": k,
                "model": cfg.get("gemini_model") or "gemini-2.5-flash",
                "grounded": bool(cfg.get("enable_google_search_grounding", True)),
                "tier_priority": 1,
            })

        for idx, k in enumerate(cfg.get("groq_api_keys", [])):
            endpoints.append({
                "id": f"groq_{idx + 1}",
                "provider": "groq",
                "api_key": k,
                "base_url": "https://api.groq.com/openai/v1",
                "model": cfg.get("groq_model") or "llama-3.3-70b-versatile",
                "grounded": False,
                "tier_priority": 2,
            })

        for idx, k in enumerate(cfg.get("openrouter_api_keys", [])):
            endpoints.append({
                "id": f"openrouter_{idx + 1}",
                "provider": "openrouter",
                "api_key": k,
                "base_url": "https://openrouter.ai/api/v1",
                "model": cfg.get("openrouter_model") or "google/gemma-3-27b-it:free",
                "grounded": False,
                "tier_priority": 2,
            })

        for idx, k in enumerate(cfg.get("mistral_api_keys", [])):
            endpoints.append({
                "id": f"mistral_{idx + 1}",
                "provider": "mistral",
                "api_key": k,
                "base_url": "https://api.mistral.ai/v1",
                "model": cfg.get("mistral_model") or "mistral-small-latest",
                "grounded": False,
                "tier_priority": 3,
            })

        if cfg.get("custom_openai_base_url") and cfg.get("custom_openai_model"):
            endpoints.append({
                "id": "custom_openai_1",
                "provider": "custom_openai",
                "api_key": cfg.get("custom_openai_api_key") or "none",
                "base_url": cfg["custom_openai_base_url"].rstrip("/"),
                "model": cfg["custom_openai_model"],
                "grounded": False,
                "tier_priority": 3,
            })

        detected_ollama = ollama_models if ollama_models is not None else self._detect_ollama_models()
        text_ollama = cfg.get("ollama_text_model") or next(
            (m for m in detected_ollama if not any(v in m.lower() for v in ("moondream", "clip", "embed"))),
            "",
        )
        if text_ollama:
            endpoints.append({
                "id": "ollama_local",
                "provider": "ollama",
                "api_key": "ollama",
                "base_url": f"{(cfg.get('ollama_base_url') or 'http://127.0.0.1:11434').rstrip('/')}/v1",
                "model": text_ollama,
                "grounded": False,
                "tier_priority": 0,
            })

        strategy = cfg.get("routing_strategy", "spread")
        if strategy == "gemini_first":
            endpoints.sort(key=lambda e: (0 if e["provider"] == "gemini" else 1, e["tier_priority"]))
        elif strategy == "free_first":
            endpoints.sort(key=lambda e: (
                0 if (e["provider"] in ("ollama", "groq") or ":free" in e["model"]) else 1,
                e["tier_priority"],
            ))
        return endpoints

    def _ordered_candidates(self) -> List[Dict[str, Any]]:
        with self._lock:
            eps = self._build_endpoints()
            if not eps:
                return []
            strategy = self.config.get("routing_strategy", "spread")
            if strategy == "spread" and len(eps) > 1:
                idx = self._rr_cursor % len(eps)
                self._rr_cursor += 1
                eps = eps[idx:] + eps[:idx]

            now = time.time()
            ready = [e for e in eps if self._cooldowns.get(e["id"], 0) <= now]
            cooling = [e for e in eps if self._cooldowns.get(e["id"], 0) > now]
            return ready + cooling

    # -----------------------------------------------------------------------
    # Provider HTTP Invocations
    # -----------------------------------------------------------------------

    def _call_gemini(self, ep: Dict[str, Any], system_prompt: str, user_prompt: str, use_grounding: bool = True) -> str:
        model = ep["model"]
        api_key = ep["api_key"]
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"

        def _make_payload(with_search: bool) -> Dict[str, Any]:
            body: Dict[str, Any] = {
                "system_instruction": {"parts": [{"text": system_prompt}]},
                "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
                "generationConfig": {
                    "temperature": 0.2,
                },
            }
            if with_search:
                body["tools"] = [{"google_search": {}}]
            else:
                body["generationConfig"]["responseMimeType"] = "application/json"
            return body

        try_search = bool(use_grounding and ep.get("grounded"))
        for attempt_with_search in ([True, False] if try_search else [False]):
            resp = httpx.post(url, json=_make_payload(attempt_with_search), timeout=38.0)
            if resp.status_code == 429:
                raise RuntimeError(f"HTTP 429 rate limit on {ep['id']}")
            if resp.status_code >= 400 and attempt_with_search:
                # Some model versions or free tiers disallow search tool alongside certain params; retry pure JSON
                continue
            resp.raise_for_status()
            data = resp.json()
            candidates = data.get("candidates") or []
            if not candidates:
                raise RuntimeError("Gemini returned empty candidates")
            parts = candidates[0].get("content", {}).get("parts", [])
            text = "\n".join(p.get("text", "") for p in parts if p.get("text")).strip()
            if text:
                return text
        raise RuntimeError("Gemini returned empty response")

    def _call_openai_compatible(self, ep: Dict[str, Any], system_prompt: str, user_prompt: str) -> str:
        url = f"{ep['base_url'].rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {ep['api_key']}",
            "Content-Type": "application/json",
        }
        if ep["provider"] == "openrouter":
            headers["HTTP-Referer"] = "http://localhost:8000"
            headers["X-Title"] = "Vinted Tracker Variant Research"

        payload: Dict[str, Any] = {
            "model": ep["model"],
            "temperature": 0.2,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        resp = httpx.post(url, headers=headers, json=payload, timeout=38.0)
        if resp.status_code == 429:
            raise RuntimeError(f"HTTP 429 rate limit on {ep['id']}")
        resp.raise_for_status()
        data = resp.json()
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError(f"{ep['provider']} returned empty choices")
        return str(choices[0].get("message", {}).get("content", "") or "").strip()

    def generate_json(self, system_prompt: str, user_prompt: str, allow_grounding: bool = True) -> Tuple[Optional[Dict[str, Any]], str]:
        """Try configured LLM endpoints in load-balanced order and parse JSON response."""
        candidates = self._ordered_candidates()
        last_err = ""
        for ep in candidates:
            t0 = time.perf_counter()
            ep_id = ep["id"]
            self._telemetry.setdefault(ep_id, {"calls": 0, "errors": 0, "last_latency_ms": None})
            self._telemetry[ep_id]["calls"] += 1
            try:
                if ep["provider"] == "gemini":
                    raw_text = self._call_gemini(ep, system_prompt, user_prompt, use_grounding=allow_grounding)
                else:
                    raw_text = self._call_openai_compatible(ep, system_prompt, user_prompt)

                elapsed = int((time.perf_counter() - t0) * 1000)
                self._telemetry[ep_id]["last_latency_ms"] = elapsed
                parsed = self._extract_json_object(raw_text)
                if parsed:
                    provider_badge = f"{ep['provider'].upper()} ({ep['model']}{'+Search' if ep.get('grounded') and allow_grounding else ''})"
                    return parsed, provider_badge
            except Exception as exc:
                err_str = str(exc)
                last_err = err_str
                self._telemetry[ep_id]["errors"] += 1
                if "429" in err_str or "quota" in err_str.lower() or "rate" in err_str.lower():
                    self._cooldowns[ep_id] = time.time() + 90.0
                logger.warning("LLM endpoint %s failed (%s), failing over...", ep_id, err_str)

        if last_err:
            logger.info("Cloud LLM pool unavailable (%s); falling back to Local Specialist + Vinted Corpus Researcher.", last_err)
        return None, ""

    @staticmethod
    def _extract_json_object(text: str) -> Optional[Dict[str, Any]]:
        if not text:
            return None
        cleaned = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned.strip())
        try:
            obj = json.loads(cleaned)
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass
        # Find outermost {...}
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            try:
                obj = json.loads(text[start : end + 1])
                if isinstance(obj, dict):
                    return obj
            except Exception:
                pass
        return None

    # -----------------------------------------------------------------------
    # Product Variant Deep Research Pipeline
    # -----------------------------------------------------------------------

    def deep_research_product_variants(
        self,
        search_text: str,
        brand: Optional[str] = None,
        sample_listings: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Perform deep hierarchical Model -> Exclusive Sub-Styles research for a fashion or tech query.

        1. Inspects sample real Vinted UK listings (titles + asking prices) so models reflect real market supply.
        2. Queries the load-balanced Cloud LLM Router (with Google Search grounding if Gemini is configured).
        3. Falls back seamlessly to Specialist Domain Catalogues + Live Vinted Model-Cluster Mining when 0 keys are configured.
        """
        samples = sample_listings or []
        sample_lines = []
        prices = [float(x["price"]) for x in samples if float(x.get("price") or 0) > 0]
        median_price = sorted(prices)[len(prices) // 2] if prices else 45.0
        for s in samples[:35]:
            t = str(s.get("title") or "").strip()
            p = float(s.get("price") or 0)
            sz = str(s.get("size") or "").strip()
            if t:
                sample_lines.append(f"- £{p:.0f} | {t}" + (f" ({sz})" if sz else ""))

        system_prompt = (
            "You are an elite secondary-market product archivist, computer-vision engineer, and UK Vinted reseller analyst. "
            "Given a broad fashion, footwear, or tech search query, research the brand's actual product catalogue and break it down into "
            "3 to 6 distinct, product-specific TOP-LEVEL MODELS (`dimension`: 'Model'), where each Model can optionally own "
            "0 to 4 MODEL-EXCLUSIVE SUB-STYLES (`sub_styles`, `dimension`: 'Style').\n\n"
            "CRITICAL TAXONOMY RULES:\n"
            "1. PRODUCT-SPECIFIC MODELS FIRST — DO NOT FORCE CLOTHING INTO GENERIC MATERIAL BUCKETS:\n"
            "   - NEVER create generic fabric catch-alls like 'Cotton', 'Cotton / Pima', 'Polyester', or 'Standard Material'.\n"
            "   - Top-level `variants` MUST be real, named models, heritage releases, reference numbers, or distinct product lines "
            "(e.g. '1996 Retro Nuptse (700)' vs 'Summit Series (800)' vs 'Reversible / Sherpa Nuptse'; "
            "'Cable-Knit Quarter-Zip' vs 'Estate-Rib & Double-Knit' vs 'Merino & Cashmere Fine-Knit' vs 'Polo Bear & Crest'; "
            "'Vintage Workwear Detroit (J01 / J97)' vs 'Carhartt WIP OG Detroit'; "
            "'Canon IXUS 70 / 75 / 80 IS' vs 'IXUS 285 HS / 265 HS').\n"
            "   - Only mention wool or cashmere if that specific product genuinely has a prized wool/cashmere variant — either as a dedicated "
            "luxury knitwear line (e.g. 'Merino & Cashmere Fine-Knit') or as an exclusive `sub_style` inside a specific model "
            "(e.g. 'Wool & Cashmere Cable-Knit' under 'Cable-Knit Quarter-Zip').\n"
            "2. STRICT MODEL-EXCLUSIVE `sub_styles` (ZERO CROSS-MODEL OVERLAP):\n"
            "   - Every entry in a model's `sub_styles` array belongs STRICTLY AND EXCLUSIVELY to that parent model.\n"
            "   - Never put a cut or style onto a model that was not produced in that cut (e.g. 'Cropped / Nuptse Short' and '1996 Gilet / Vest' "
            "belong ONLY inside '1996 Retro Nuptse (700)', NEVER on 'Summit Series (800)').\n"
            "   - If a model has no meaningful sub-variants, set `\"sub_styles\": []`.\n"
            "   - DO NOT FORCE CATEGORIES: a listing with no real evidence stays unclassified. Never invent a vague catch-all model "
            "just so every listing lands somewhere, and never group ordinary items with a rare variant.\n"
            "3. SET `modality` ACCURATELY:\n"
            "   - `'visual'`: silhouette, knit geometry, baffle layout, collar/pocket hardware, or camera body/lens markings visible in photos.\n"
            "   - `'feature'`: a RARER variant defined by ONE distinctive physical feature that ordinary listings lack "
            "(e.g. 'Hooded Henley' vs a normal henley, 'Cropped' or 'Gilet / Vest' cuts, 'Reversible'). Feature variants are only "
            "assigned when the listing explicitly names the feature or the photo clearly shows it — never as a best-guess bucket. "
            "Give them precise `text_keywords` (e.g. 'hooded', 'hood') and `visual_cues` that describe the feature itself.\n"
            "   - `'label'`: luxury fibre composition or bundle kit verified strictly from listing text or neck-tag/care-label OCR "
            "(empty `visual_cues`: `[]`; never guessed from distant photos).\n"
            "4. DISCRIMINATIVE CUES ONLY:\n"
            "   - NEVER include parent brand names or generic search words (e.g. 'polo', 'ralph lauren', 'north face', 'nuptse', 'quarter zip', "
            "'jumper', 'hoodie', 'jacket', 'camera') in `text_keywords` or `ocr_signatures`.\n"
            "   - Put mutually exclusive sibling model terms in `negative_keywords` so models never bleed into one another.\n"
            "5. SCHEMA FOR EACH MODEL (and each item inside `sub_styles`):\n"
            "   - `variant_slug`: unique lowercase snake_case id\n"
            "   - `variant_name`: concise collector-grade name\n"
            "   - `modality`: 'visual', 'feature', or 'label'\n"
            "   - `short_description`: concise physical & UK resale distinction\n"
            "   - `resale_tier`: 'PREMIUM' | 'HIGH' | 'STANDARD' | 'BUDGET'\n"
            "   - `est_market_value_gbp`: realistic UK secondhand sold price in GBP (number)\n"
            "   - `visual_cues`: 2 to 5 concrete visual phrases for CLIP (empty `[]` if `modality` == 'label')\n"
            "   - `ocr_signatures`: exact discriminative text printed on neck tags, sleeves, or camera faceplates\n"
            "   - `text_keywords`: lowercase discriminative title/description substrings\n"
            "   - `negative_keywords`: lowercase mutually exclusive sibling terms\n"
            "   - `sub_styles`: (on top-level models only) list of 0..4 exclusive child style objects with the exact same fields\n"
            "6. Respond ONLY with valid JSON: "
            "{\"product_family\": str, \"domain\": \"fashion\"|\"tech\"|\"footwear\"|\"other\", \"research_notes\": str, \"variants\": [...]}"
        )

        user_prompt = (
            f"Broad Search Term: \"{search_text}\"\n"
            f"Detected Brand: \"{brand or 'Auto'}\"\n"
            f"Observed UK Vinted Median Listing Price: £{median_price:.2f}\n"
        )
        if sample_lines:
            user_prompt += "\nLive Vinted UK Sample Listings:\n" + "\n".join(sample_lines)

        llm_result, provider_used = self.generate_json(system_prompt, user_prompt, allow_grounding=True)
        if llm_result and isinstance(llm_result.get("variants"), list) and len(llm_result["variants"]) >= 2:
            normalized = self._normalize_catalogue(llm_result, search_text, median_price, brand=brand)
            if len(normalized.get("variants") or []) >= 2:
                normalized["provider_used"] = provider_used
                normalized["researched_at"] = datetime.now(timezone.utc).isoformat()
                return normalized

        # Fallback: Specialist Knowledge Base + Live Vinted Corpus Cluster Miner
        local_cat = self._local_deep_research_fallback(search_text, brand, samples, median_price)
        local_cat["researched_at"] = datetime.now(timezone.utc).isoformat()
        return local_cat

    def _normalize_catalogue(
        self,
        raw: Dict[str, Any],
        search_text: str,
        median_price: float,
        brand: Optional[str] = None,
    ) -> Dict[str, Any]:
        # Build generic stop-token set so parent brand/query words never pollute variant cues
        stop_tokens = {
            t for t in re.findall(r"[a-z0-9]+", (search_text or "").lower())
            if not any(ch.isdigit() for ch in t)
        }
        if brand:
            stop_tokens.update(re.findall(r"[a-z0-9]+", brand.lower()))
        stop_tokens.update({
            "the", "and", "with", "for", "mens", "womens", "unisex", "size",
            "polo", "knitted", "jumper", "sweater", "pullover", "top",
            "camera", "digital", "compact",
        })

        banned_generic_top_models = {
            "cotton", "100% cotton", "cotton / pima", "pima cotton", "cotton core",
            "polyester", "synthetic", "acrylic", "nylon", "standard material", "material",
        }

        def _is_generic_cue(phrase: str) -> bool:
            toks = re.findall(r"[a-z0-9]+", phrase.lower())
            if not toks:
                return True
            if any(any(ch.isdigit() for ch in tok) for tok in toks):
                return False
            return all(tok in stop_tokens for tok in toks)

        seen_slugs = set()

        def _norm_entry(
            v: Dict[str, Any],
            idx: int,
            is_sub_style: bool = False,
            parent_slug: str = "",
        ) -> Optional[Dict[str, Any]]:
            name = str(v.get("variant_name") or v.get("name") or f"{'Style' if is_sub_style else 'Model'} {idx + 1}").strip()
            if not is_sub_style and name.lower() in banned_generic_top_models:
                return None

            raw_slug = str(v.get("variant_slug") or name).lower()
            slug = re.sub(r"[^a-z0-9]+", "_", raw_slug).strip("_") or f"variant_{idx + 1}"
            if slug in seen_slugs:
                slug = f"{parent_slug}_{slug}".strip("_") if parent_slug else f"{slug}_{idx + 1}"
            if slug in seen_slugs:
                slug = f"{slug}_{idx + 1}"
            seen_slugs.add(slug)

            dim = "Style" if is_sub_style else "Model"
            modality = str(v.get("modality") or "").strip().lower()
            if modality not in ("visual", "label", "feature"):
                raw_dim = str(v.get("dimension") or "").strip().lower()
                modality = "label" if raw_dim in ("material", "fabric", "composition", "kit", "bundle") else "visual"
            # Variants defined by one distinctive physical feature (hood, cropped hem, sleeveless, reversible)
            # are feature-gated so ordinary listings are never forced into the rarer variant.
            if modality == "visual" and _defining_feature_group(name, search_text):
                modality = "feature"

            tier = str(v.get("resale_tier") or "STANDARD").upper()
            if tier not in ("PREMIUM", "HIGH", "STANDARD", "BUDGET"):
                tier = "STANDARD"

            est_val = v.get("est_market_value_gbp")
            try:
                est_float = round(float(est_val), 2) if est_val is not None else round(median_price, 2)
            except Exception:
                est_float = round(median_price, 2)

            clean_ocr = [
                str(x).strip()
                for x in (v.get("ocr_signatures") or [])
                if str(x).strip() and not _is_generic_cue(str(x))
            ][:14]
            clean_kws = [
                str(x).strip().lower()
                for x in (v.get("text_keywords") or [])
                if str(x).strip() and not _is_generic_cue(str(x))
            ][:20]
            clean_negs = [
                str(x).strip().lower()
                for x in (v.get("negative_keywords") or [])
                if str(x).strip() and not _is_generic_cue(str(x))
            ][:16]
            clean_vcues = (
                [] if modality == "label"
                else [str(x).strip() for x in (v.get("visual_cues") or []) if str(x).strip()][:6]
            )

            out: Dict[str, Any] = {
                "variant_slug": slug,
                "variant_name": name,
                "dimension": dim,
                "modality": modality,
                "short_description": str(v.get("short_description") or v.get("description") or "").strip(),
                "resale_tier": tier,
                "est_market_value_gbp": max(5.0, est_float),
                "visual_cues": clean_vcues,
                "ocr_signatures": clean_ocr,
                "text_keywords": clean_kws,
                "negative_keywords": clean_negs,
            }

            if not is_sub_style:
                raw_subs = v.get("sub_styles") or v.get("styles") or v.get("children") or []
                norm_subs: List[Dict[str, Any]] = []
                if isinstance(raw_subs, list):
                    for s_idx, sv in enumerate(raw_subs[:6]):
                        if isinstance(sv, dict):
                            norm_sv = _norm_entry(sv, s_idx, is_sub_style=True, parent_slug=slug)
                            if norm_sv:
                                norm_subs.append(norm_sv)
                out["sub_styles"] = norm_subs
            return out

        variants_out: List[Dict[str, Any]] = []
        for idx, v in enumerate(raw.get("variants") or []):
            if not isinstance(v, dict):
                continue
            norm_v = _norm_entry(v, idx, is_sub_style=False)
            if norm_v:
                variants_out.append(norm_v)

        return {
            "product_family": str(raw.get("product_family") or search_text),
            "domain": str(raw.get("domain") or "other"),
            "research_notes": str(raw.get("research_notes") or f"Hierarchical model & style catalogue for {search_text}."),
            "variants": variants_out,
        }

    def _local_deep_research_fallback(
        self,
        search_text: str,
        brand: Optional[str],
        samples: List[Dict[str, Any]],
        median_price: float,
    ) -> Dict[str, Any]:
        q_lower = search_text.lower().strip()

        # 1. Check Specialist Curated Catalogues first
        for triggers, template in SPECIALIST_CATALOGUES:
            if any(t in q_lower for t in triggers):
                cloned = json.loads(json.dumps(template))
                norm = self._normalize_catalogue(cloned, search_text, median_price, brand=brand)
                norm["provider_used"] = "Specialist Knowledge Base + Vinted UK Corpus"
                return norm

        # 2. Dynamic Live Vinted Corpus Product-Model & Exclusive Sub-Style Miner
        is_tech = any(w in q_lower for w in (
            "camera", "canon", "cannon", "sony", "nikon", "fuji", "fujifilm", "olympus", "lumix",
            "coolpix", "finepix", "powershot", "3ds", "2ds", "ds", "gameboy", "switch", "airpods",
            "ipod", "walkman", "bose", "wh-", "garmin", "dyson", "playstation", "xbox", "lens",
        ))
        is_knitwear = any(w in q_lower for w in (
            "jumper", "sweater", "cardigan", "knit", "pullover", "quarter zip", "1/4 zip", "half zip",
        ))

        base_tokens = set(re.findall(r"[a-z0-9]+", q_lower))
        if brand:
            base_tokens.update(re.findall(r"[a-z0-9]+", brand.lower()))

        model_counter: Counter[str] = Counter()
        model_prices: Dict[str, List[float]] = {}

        model_regex = re.compile(
            r"\b([a-z]{0,4}\d{2,4}[a-z]{0,3}(?:\s+(?:hs|is|mark\s*ii|mark\s*iii|xl|pro|max|oled|lite|v\d))?)\b",
            re.IGNORECASE,
        )
        # Product-line / construction models (strictly silhouette/design models — NEVER generic Cotton/Material!)
        silhouette_models = [
            ("gore-tex", "GORE-TEX Technical Shell", "visual", "PREMIUM", ["waterproof technical shell jacket with GORE-TEX embroidery"], ["GORE-TEX", "GORETEX"]),
            ("cable", "Cable-Knit Edition", "visual", "HIGH", ["chunky braided twisted rope cable-knit pattern across chest and sleeves"], []),
            ("sherpa", "Sherpa / High-Pile Fleece", "visual", "HIGH", ["fluffy textured high-pile teddy sherpa fleece"], []),
            ("quilted", "Quilted / Padded Edition", "visual", "HIGH", ["diamond or horizontal quilted padded outer shell"], []),
            ("hoodie", "Hooded Pullover / Zip Model", "visual", "STANDARD", ["hooded silhouette with drawstring hood"], []),
            ("crewneck", "Crewneck Sweatshirt Model", "visual", "STANDARD", ["collarless ribbed crew neck pullover without hood"], []),
            ("quarter zip", "Quarter-Zip Funnel Neck", "visual", "HIGH", ["stand collar pullover with short quarter zipper at neck"], []),
            ("full zip", "Full-Zip Jacket Model", "visual", "HIGH", ["full length front zipper jacket"], []),
            ("gilet", "Sleeveless Gilet / Vest Model", "visual", "STANDARD", ["sleeveless padded gilet vest bodywarmer"], []),
        ]

        for s in samples:
            title = str(s.get("title") or "")
            price = float(s.get("price") or 0.0)
            for m in model_regex.finditer(title):
                code = re.sub(r"\s+", " ", m.group(1).strip().upper())
                if code.lower() in base_tokens or re.match(r"^(19\d\d|20\d\d|\d+GB|\d+MM|\d+MP|UK\d+|W\d+|L\d+)$", code):
                    continue
                model_counter[code] += 1
                if price > 0:
                    model_prices.setdefault(code, []).append(price)

        mined_variants: List[Dict[str, Any]] = []
        top_codes = [code for code, cnt in model_counter.most_common(5) if cnt >= 2]
        if top_codes:
            for code in top_codes:
                other_codes = [c.lower() for c in top_codes if c != code]
                p_list = model_prices.get(code) or [median_price]
                avg_p = round(sum(p_list) / len(p_list), 2)
                tier = "PREMIUM" if avg_p >= median_price * 1.25 else ("HIGH" if avg_p >= median_price else "STANDARD")
                sub_styles: List[Dict[str, Any]] = []
                if is_tech:
                    sub_styles.append({
                        "variant_slug": f"{re.sub(r'[^a-z0-9]+', '_', code.lower()).strip('_')}_boxed_kit",
                        "variant_name": "Boxed / Charger Kit",
                        "modality": "label",
                        "short_description": f"Complete {code} bundle with original box, battery, or charger.",
                        "resale_tier": "PREMIUM" if tier == "HIGH" else tier,
                        "est_market_value_gbp": round(max(10.0, avg_p * 1.22), 2),
                        "visual_cues": [],
                        "ocr_signatures": [],
                        "text_keywords": ["boxed", "with box", "with charger", "charger included", "complete"],
                        "negative_keywords": ["no charger", "untested", "spares", "faulty"],
                    })
                mined_variants.append({
                    "variant_slug": re.sub(r"[^a-z0-9]+", "_", code.lower()).strip("_"),
                    "variant_name": f"{code} Model" if len(code) <= 3 else code,
                    "dimension": "Model",
                    "modality": "visual",
                    "short_description": f"Specific {code} model identified from UK Vinted market listings.",
                    "resale_tier": tier,
                    "est_market_value_gbp": max(8.0, avg_p),
                    "visual_cues": [
                        f"{search_text} {code} product photo showing {code} design details and model markings",
                    ],
                    "ocr_signatures": [code, code.replace(" ", "")],
                    "text_keywords": [code.lower(), code.lower().replace(" ", "")],
                    "negative_keywords": other_codes,
                    "sub_styles": sub_styles,
                })
        else:
            for kw, title_label, mod, tier, vcues, ocrs in silhouette_models:
                if kw in q_lower:
                    continue
                matches = [s for s in samples if kw in str(s.get("title") or "").lower()]
                if len(matches) >= 2:
                    p_list = [float(x["price"]) for x in matches if float(x.get("price") or 0) > 0] or [median_price]
                    avg_p = round(sum(p_list) / len(p_list), 2)
                    sub_styles = []
                    if kw == "cable":
                        sub_styles.append({
                            "variant_slug": "cable_wool_cashmere_edition",
                            "variant_name": "Wool & Cashmere Cable",
                            "modality": "label",
                            "short_description": "Luxury wool, merino, lambswool, or cashmere cable-knit edition.",
                            "resale_tier": "PREMIUM",
                            "est_market_value_gbp": round(avg_p * 1.3, 2),
                            "visual_cues": [],
                            "ocr_signatures": ["100% WOOL", "MERINO", "LAMBSWOOL", "CASHMERE"],
                            "text_keywords": ["wool", "merino", "lambswool", "cashmere"],
                            "negative_keywords": ["100% cotton"],
                        })
                    mined_variants.append({
                        "variant_slug": re.sub(r"[^a-z0-9]+", "_", kw),
                        "variant_name": title_label,
                        "dimension": "Model",
                        "modality": mod,
                        "short_description": f"{title_label} model line within {search_text}.",
                        "resale_tier": tier,
                        "est_market_value_gbp": avg_p,
                        "visual_cues": [f"{search_text} {c}" for c in vcues],
                        "ocr_signatures": ocrs,
                        "text_keywords": [kw],
                        "negative_keywords": [],
                        "sub_styles": sub_styles,
                    })

            # Only add a dedicated Merino / Cashmere Fine-Knit model if this is knitwear AND wool/cashmere listings exist
            if is_knitwear:
                wool_matches = [
                    s for s in samples
                    if any(w in str(s.get("title") or "").lower() for w in ("merino", "lambswool", "cashmere"))
                ]
                if len(wool_matches) >= 2:
                    p_list = [float(x["price"]) for x in wool_matches if float(x.get("price") or 0) > 0] or [median_price * 1.25]
                    mined_variants.append({
                        "variant_slug": "merino_cashmere_fine_knit",
                        "variant_name": "Merino & Cashmere Fine-Knit",
                        "dimension": "Model",
                        "modality": "label",
                        "short_description": "Dedicated fine-gauge merino wool, lambswool, or cashmere knitwear model.",
                        "resale_tier": "PREMIUM",
                        "est_market_value_gbp": round(sum(p_list) / len(p_list), 2),
                        "visual_cues": [],
                        "ocr_signatures": ["100% MERINO", "LAMBSWOOL", "CASHMERE", "100% WOOL"],
                        "text_keywords": ["merino", "lambswool", "cashmere"],
                        "negative_keywords": ["cable", "100% cotton", "fleece"],
                        "sub_styles": [],
                    })

        if len(mined_variants) < 2:
            if is_tech:
                mined_variants = [
                    {
                        "variant_slug": "pro_flagship_model",
                        "variant_name": "Flagship / Wi-Fi / High-Zoom Model",
                        "dimension": "Model",
                        "modality": "visual",
                        "short_description": "Higher-tier model specification with Wi-Fi, HS CMOS sensor, OLED, or extended optical zoom.",
                        "resale_tier": "PREMIUM",
                        "est_market_value_gbp": round(median_price * 1.4, 2),
                        "visual_cues": [f"high-end black or silver {search_text} with zoom lens barrel and Wi-Fi HD markings"],
                        "ocr_signatures": ["HS", "Wi-Fi", "FULL HD", "PRO", "XL", "OLED", "12x", "10x"],
                        "text_keywords": ["hs", "wifi", "wi-fi", "pro", "xl", "oled", "mark ii", "mark iii"],
                        "negative_keywords": ["spares", "repairs", "faulty"],
                        "sub_styles": [
                            {
                                "variant_slug": "pro_boxed_kit",
                                "variant_name": "Boxed / Charger Kit",
                                "modality": "label",
                                "short_description": "Complete flagship bundle with original box and charger.",
                                "resale_tier": "PREMIUM",
                                "est_market_value_gbp": round(median_price * 1.6, 2),
                                "visual_cues": [],
                                "ocr_signatures": [],
                                "text_keywords": ["boxed", "with box", "charger included", "with charger", "complete"],
                                "negative_keywords": ["no charger", "untested"],
                            }
                        ],
                    },
                    {
                        "variant_slug": "vintage_classic_model",
                        "variant_name": "Classic / Vintage CCD Model",
                        "dimension": "Model",
                        "modality": "visual",
                        "short_description": "Original compact metallic or vintage CCD sensor release model.",
                        "resale_tier": "HIGH",
                        "est_market_value_gbp": round(median_price * 1.05, 2),
                        "visual_cues": [f"compact metallic retro {search_text} in clean working condition"],
                        "ocr_signatures": ["CCD", "MEGA PIXELS"],
                        "text_keywords": ["ccd", "vintage", "retro"],
                        "negative_keywords": ["faulty", "spares", "wifi", "wi-fi"],
                        "sub_styles": [
                            {
                                "variant_slug": "classic_boxed_kit",
                                "variant_name": "Boxed / Charger Kit",
                                "modality": "label",
                                "short_description": "Vintage CCD model complete with charger and battery.",
                                "resale_tier": "HIGH",
                                "est_market_value_gbp": round(median_price * 1.25, 2),
                                "visual_cues": [],
                                "ocr_signatures": [],
                                "text_keywords": ["boxed", "with box", "charger included", "with charger", "complete"],
                                "negative_keywords": ["no charger", "untested"],
                            }
                        ],
                    },
                ]
            else:
                mined_variants = [
                    {
                        "variant_slug": "vintage_archive_model",
                        "variant_name": "Vintage / Heritage Archive Model",
                        "dimension": "Model",
                        "modality": "visual",
                        "short_description": "Original 90s/00s archive release, heritage label issue, or special edition model.",
                        "resale_tier": "PREMIUM",
                        "est_market_value_gbp": round(median_price * 1.35, 2),
                        "visual_cues": [f"vintage archive heritage {search_text} with classic woven label and heavier wash"],
                        "ocr_signatures": ["MADE IN USA", "ARCHIVE", "VINTAGE"],
                        "text_keywords": ["archive", "vintage", "90s", "y2k", "made in usa", "rare", "og"],
                        "negative_keywords": ["kids", "junior"],
                        "sub_styles": [],
                    },
                    {
                        "variant_slug": "signature_core_model",
                        "variant_name": "Signature Classic Model",
                        "dimension": "Model",
                        "modality": "visual",
                        "short_description": "Mainline signature silhouette with standard brand emblem and classic cut.",
                        "resale_tier": "STANDARD",
                        "est_market_value_gbp": round(median_price * 0.95, 2),
                        "visual_cues": [f"clean mainline classic {search_text} with small chest logo emblem"],
                        "ocr_signatures": [],
                        "text_keywords": ["classic", "original", "regular", "signature"],
                        "negative_keywords": ["kids", "junior", "cropped", "gilet"],
                        "sub_styles": [],
                    },
                    {
                        "variant_slug": "graphic_special_edition",
                        "variant_name": "Graphic / Crest / Collab Edition",
                        "dimension": "Model",
                        "modality": "visual",
                        "short_description": "Statement graphic, oversized crest embroidery, or limited collaboration release.",
                        "resale_tier": "HIGH",
                        "est_market_value_gbp": round(median_price * 1.2, 2),
                        "visual_cues": [f"{search_text} featuring large embroidered crest patch or bold graphic print"],
                        "ocr_signatures": ["LIMITED EDITION"],
                        "text_keywords": ["collab", "limited", "crest", "patch", "embroidered", "graphic", "spellout"],
                        "negative_keywords": ["plain"],
                        "sub_styles": [],
                    },
                ]

        norm_res = self._normalize_catalogue(
            {
                "product_family": search_text.title(),
                "domain": "tech" if is_tech else "fashion",
                "research_notes": (
                    "Generated via Live Vinted UK Model-Cluster Mining. Add a free Gemini, Groq, or OpenRouter API key in "
                    "'AI Models & Keys' to enable live web-grounded Deep Research for arbitrary products."
                ),
                "variants": mined_variants,
            },
            search_text,
            median_price,
            brand=brand,
        )
        norm_res["provider_used"] = "Live Vinted Corpus Miner (Zero-Key Local Mode)"
        return norm_res


llm_router = MultiLLMRouter()

