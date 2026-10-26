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

SPECIALIST_CATALOGUES: List[Tuple[List[str], Dict[str, Any]]] = [
    (
        ["nuptse", "north face puffer", "tnf 700"],
        {
            "product_family": "The North Face Nuptse Outerwear",
            "domain": "fashion",
            "research_notes": "Multi-category facets: Edition (1996 Retro 700 vs Summit Series 800 vs Reversible/Sherpa) combined with Cut (Jacket vs Cropped vs Gilet).",
            "variants": [
                {
                    "variant_slug": "1996_retro_nuptse",
                    "variant_name": "1996 Retro (700)",
                    "dimension": "Edition",
                    "modality": "visual",
                    "short_description": "Boxy shiny ripstop nylon puffer with contrasting black shoulder yoke and '700' cuff embroidery.",
                    "resale_tier": "HIGH",
                    "est_market_value_gbp": 115.0,
                    "visual_cues": [
                        "boxy puffer with wide horizontal down baffles and contrasting black shoulder yoke",
                        "shiny ripstop nylon down puffer with 700 embroidered on sleeve cuff",
                    ],
                    "ocr_signatures": ["700", "NF0A3C8D", "NF0A3XEO", "STOW POCKET"],
                    "text_keywords": ["1996", "retro nuptse", "1996 retro", "700 fill", "nf0a3c8d"],
                    "negative_keywords": ["summit", "summit series", "800", "850", "reversible", "sherpa", "borg"],
                },
                {
                    "variant_slug": "summit_series_nuptse",
                    "variant_name": "Summit Series (800)",
                    "dimension": "Edition",
                    "modality": "visual",
                    "short_description": "Alpine edition with Summit Series sleeve crest, 800/850 fill down, and Pertex/Windstopper shell.",
                    "resale_tier": "PREMIUM",
                    "est_market_value_gbp": 165.0,
                    "visual_cues": [
                        "technical alpine expedition down jacket with Summit Series crest on sleeve and chest zip pocket",
                        "matte Pertex mountaineering down jacket with 800 fill embroidery",
                    ],
                    "ocr_signatures": ["SUMMIT", "SUMMIT SERIES", "800", "850", "PERTEX", "WINDSTOPPER"],
                    "text_keywords": ["summit", "summit series", "800", "850", "pertex", "windstopper", "baltoro"],
                    "negative_keywords": ["1996 retro", "sherpa", "reversible"],
                },
                {
                    "variant_slug": "reversible_sherpa_nuptse",
                    "variant_name": "Reversible / Sherpa",
                    "dimension": "Edition",
                    "modality": "visual",
                    "short_description": "High-pile teddy/Sherpa fleece Nuptse or reversible nylon-to-fleece construction.",
                    "resale_tier": "HIGH",
                    "est_market_value_gbp": 120.0,
                    "visual_cues": [
                        "fluffy textured sherpa borg teddy fleece puffer jacket",
                        "reversible fleece and nylon quilted down puffer coat",
                    ],
                    "ocr_signatures": ["HEATSEEKER"],
                    "text_keywords": ["reversible", "sherpa", "borg", "teddy", "high pile"],
                    "negative_keywords": ["summit series", "800"],
                },
                {
                    "variant_slug": "nuptse_full_jacket",
                    "variant_name": "Standard Jacket",
                    "dimension": "Cut",
                    "modality": "visual",
                    "short_description": "Full waist/hip-length long-sleeve puffer jacket.",
                    "resale_tier": "HIGH",
                    "est_market_value_gbp": 110.0,
                    "visual_cues": [
                        "mens regular hip-length long sleeve down puffer coat with 4 or 5 horizontal baffles",
                    ],
                    "ocr_signatures": ["NF0A3C8D"],
                    "text_keywords": ["puffer jacket", "down jacket"],
                    "negative_keywords": ["gilet", "vest", "bodywarmer", "body warmer", "sleeveless", "cropped", "nuptse short", "crop"],
                },
                {
                    "variant_slug": "cropped_short_nuptse",
                    "variant_name": "Cropped / Short",
                    "dimension": "Cut",
                    "modality": "visual",
                    "short_description": "Women's high-waisted 2-to-3 baffle cropped Nuptse silhouette.",
                    "resale_tier": "STANDARD",
                    "est_market_value_gbp": 85.0,
                    "visual_cues": [
                        "short cropped waist-length womens puffer jacket with only two or three horizontal baffles",
                    ],
                    "ocr_signatures": ["NF0A5GGE"],
                    "text_keywords": ["nuptse short", "cropped nuptse", "cropped", "crop"],
                    "negative_keywords": ["gilet", "vest", "bodywarmer", "sleeveless"],
                },
                {
                    "variant_slug": "nuptse_gilet_vest",
                    "variant_name": "Gilet / Vest",
                    "dimension": "Cut",
                    "modality": "visual",
                    "short_description": "Sleeveless down puffer bodywarmer gilet.",
                    "resale_tier": "STANDARD",
                    "est_market_value_gbp": 70.0,
                    "visual_cues": [
                        "sleeveless down puffer gilet vest bodywarmer with black shoulder yoke and no sleeves",
                    ],
                    "ocr_signatures": ["NF0A3JQQ"],
                    "text_keywords": ["gilet", "vest", "bodywarmer", "body warmer", "sleeveless"],
                    "negative_keywords": ["long sleeve"],
                },
            ],
        },
    ),
    (
        ["ixus", "cannon ixus", "canon ixus", "powershot elph"],
        {
            "product_family": "Canon IXUS / PowerShot ELPH Cameras",
            "domain": "tech",
            "research_notes": "Multi-category facets: Model Series (vintage CCD IXUS 70/75/80 vs 12x Wi-Fi 285 HS vs 20MP 185/190) + Bundle completeness.",
            "variants": [
                {
                    "variant_slug": "ixus_70_75_80_ccd",
                    "variant_name": "IXUS 70 / 75 / 80 (CCD)",
                    "dimension": "Series",
                    "modality": "visual",
                    "short_description": "2007–2008 metal CCD digicams with optical viewfinder window and 3x zoom (5.8-17.4mm).",
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
                },
                {
                    "variant_slug": "ixus_285_hs_wifi",
                    "variant_name": "IXUS 285 / 275 / 265 HS",
                    "dimension": "Series",
                    "modality": "visual",
                    "short_description": "Flagship pocket travel zoom with 12x Optical Zoom (25-300mm), 20.2MP HS CMOS, and Wi-Fi.",
                    "resale_tier": "PREMIUM",
                    "est_market_value_gbp": 175.0,
                    "visual_cues": [
                        "modern sleek matte black silver or purple compact camera with thick 12x zoom lens barrel and Wi-Fi badge",
                    ],
                    "ocr_signatures": [
                        "285 HS", "275 HS", "265 HS", "ELPH 360", "12x", "4.5-54.0", "25-300", "Wi-Fi", "FULL HD",
                    ],
                    "text_keywords": ["ixus 285", "285 hs", "285hs", "ixus 275", "ixus 265", "elph 360", "12x zoom", "wifi", "wi-fi"],
                    "negative_keywords": ["ixus 70", "ixus 75", "ixus 80", "ixus 185", "8x"],
                },
                {
                    "variant_slug": "ixus_185_190_175",
                    "variant_name": "IXUS 175 / 185 / 190 (20MP)",
                    "dimension": "Series",
                    "modality": "visual",
                    "short_description": "Late-gen 20.0MP compact cameras with 8x or 10x Optical Zoom and slim rounded shell.",
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
                },
                {
                    "variant_slug": "ixus_105_115_130_mid",
                    "variant_name": "IXUS 105–155 Mid-Era",
                    "dimension": "Series",
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
                },
                {
                    "variant_slug": "ixus_early_classic_40_50",
                    "variant_name": "Early Y2K IXUS 30–55 / 400",
                    "dimension": "Series",
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
                },
                {
                    "variant_slug": "ixus_boxed_bundle",
                    "variant_name": "Boxed / Charger Kit",
                    "dimension": "Kit",
                    "modality": "label",
                    "short_description": "Includes original charger, battery, SD card, or retail box.",
                    "resale_tier": "HIGH",
                    "est_market_value_gbp": 125.0,
                    "visual_cues": [],
                    "ocr_signatures": ["CHARGER", "CB-2L"],
                    "text_keywords": ["boxed", "with box", "charger", "with charger", "sd card", "complete"],
                    "negative_keywords": ["no charger", "untested", "spares", "faulty"],
                },
            ],
        },
    ),
    (
        ["cyber-shot", "cybershot", "sony dsc"],
        {
            "product_family": "Sony Cyber-shot Compact Cameras",
            "domain": "tech",
            "research_notes": "Multi-category facets: Cyber-shot Series (RX100 Pro vs T-Series Sliding vs W-Series vs WX/HX Superzoom) + Kit.",
            "variants": [
                {
                    "variant_slug": "cybershot_rx100_series",
                    "variant_name": "RX100 Pro 1-Inch",
                    "dimension": "Series",
                    "modality": "visual",
                    "short_description": "Enthusiast 1-inch sensor compact with matte black body and Zeiss Vario-Sonnar T* f/1.8 lens.",
                    "resale_tier": "PREMIUM",
                    "est_market_value_gbp": 260.0,
                    "visual_cues": ["matte black pro compact camera with large Zeiss T* lens control ring"],
                    "ocr_signatures": ["RX100", "Vario-Sonnar", "ZEISS", "1.8-4.9", "1.8-2.8"],
                    "text_keywords": ["rx100", "dsc-rx100", "rx100m2", "rx100m3", "rx100m4", "rx100m5"],
                    "negative_keywords": ["dsc-w", "dsc-t", "dsc-s"],
                },
                {
                    "variant_slug": "cybershot_t_sliding_series",
                    "variant_name": "DSC-T Sliding Cover",
                    "dimension": "Series",
                    "modality": "visual",
                    "short_description": "Ultra-thin Y2K digicam with vertical sliding front lens shield and internal folded periscope zoom.",
                    "resale_tier": "HIGH",
                    "est_market_value_gbp": 115.0,
                    "visual_cues": ["ultra flat rectangular metallic camera with sliding front faceplate cover and corner lens"],
                    "ocr_signatures": ["DSC-T", "Vario-Tessar"],
                    "text_keywords": ["dsc-t", "t10", "t20", "t70", "t77", "t90", "t99", "t100", "t200", "t300", "sliding"],
                    "negative_keywords": ["rx100", "dsc-w830", "dsc-hx"],
                },
                {
                    "variant_slug": "cybershot_w_pocket_series",
                    "variant_name": "DSC-W Pocket Series",
                    "dimension": "Series",
                    "modality": "visual",
                    "short_description": "Classic extending-barrel pocket Cyber-shot cameras (W80 / W350 / W810 / W830).",
                    "resale_tier": "STANDARD",
                    "est_market_value_gbp": 80.0,
                    "visual_cues": ["compact silver black or pink pocket camera with circular telescoping center zoom lens"],
                    "ocr_signatures": ["DSC-W", "W830", "W810", "W800", "W350", "W530", "W570", "SteadyShot"],
                    "text_keywords": ["dsc-w", "w830", "w810", "w800", "w570", "w530", "w350", "w320", "w120", "w80", "w55"],
                    "negative_keywords": ["rx100", "dsc-t", "dsc-hx"],
                },
                {
                    "variant_slug": "cybershot_hx_wx_superzoom",
                    "variant_name": "WX / HX High-Zoom",
                    "dimension": "Series",
                    "modality": "visual",
                    "short_description": "Modern CMOS Exmor R travel superzoom models (10x–30x Sony G / Zeiss zoom, Wi-Fi).",
                    "resale_tier": "HIGH",
                    "est_market_value_gbp": 145.0,
                    "visual_cues": ["modern travel zoom camera with mode dial, grip, and 10x 20x or 30x G lens"],
                    "ocr_signatures": ["WX220", "WX350", "WX500", "HX50", "HX60", "HX80", "HX90", "Sony Lens G", "20x", "30x"],
                    "text_keywords": ["wx220", "wx350", "wx500", "hx50", "hx60", "hx80", "hx90", "dsc-wx", "dsc-hx"],
                    "negative_keywords": ["dsc-t", "dsc-w800"],
                },
            ],
        },
    ),
    (
        ["synchilla", "patagonia fleece", "patagonia gilet"],
        {
            "product_family": "Patagonia Fleece",
            "domain": "fashion",
            "research_notes": "Multi-category facets: Model (Snap-T vs Retro-X Sherpa vs Better Sweater) + Pattern (Patterned/Aztec vs Solid).",
            "variants": [
                {
                    "variant_slug": "patagonia_snap_t",
                    "variant_name": "Synchilla Snap-T",
                    "dimension": "Model",
                    "modality": "visual",
                    "short_description": "4-snap nylon placket fleece pullover with contrasting chest flap pocket.",
                    "resale_tier": "HIGH",
                    "est_market_value_gbp": 60.0,
                    "visual_cues": ["polar fleece pullover jumper with four snap buttons at neck and nylon chest flap pocket"],
                    "ocr_signatures": [],
                    "text_keywords": ["snap-t", "snap t", "4 button", "button neck", "oatmeal"],
                    "negative_keywords": ["better sweater", "retro-x", "retro x", "full zip"],
                },
                {
                    "variant_slug": "classic_retro_x",
                    "variant_name": "Retro-X / Deep-Pile Sherpa",
                    "dimension": "Model",
                    "modality": "visual",
                    "short_description": "Shaggy sherpa deep-pile fleece with contrasting vertical zippered nylon chest patch pocket.",
                    "resale_tier": "PREMIUM",
                    "est_market_value_gbp": 88.0,
                    "visual_cues": ["shaggy teddy sherpa pile zip fleece with contrasting vertical zippered nylon patch pocket on chest"],
                    "ocr_signatures": [],
                    "text_keywords": ["retro-x", "retro x", "retro pile", "sherpa", "deep pile"],
                    "negative_keywords": ["snap-t", "better sweater"],
                },
                {
                    "variant_slug": "better_sweater",
                    "variant_name": "Better Sweater Knit-Face",
                    "dimension": "Model",
                    "modality": "visual",
                    "short_description": "Heathered marl sweater-knit exterior fleece with zip stand collar.",
                    "resale_tier": "STANDARD",
                    "est_market_value_gbp": 42.0,
                    "visual_cues": ["heathered marl knitted texture zip fleece sweater"],
                    "ocr_signatures": [],
                    "text_keywords": ["better sweater", "marl", "knit fleece"],
                    "negative_keywords": ["snap-t", "retro-x"],
                },
                {
                    "variant_slug": "patterned_aztec_print",
                    "variant_name": "Patterned / Aztec Print",
                    "dimension": "Pattern",
                    "modality": "visual",
                    "short_description": "Collectible geometric, Aztec, floral, or wildlife all-over print.",
                    "resale_tier": "PREMIUM",
                    "est_market_value_gbp": 78.0,
                    "visual_cues": ["colorful patterned geometric tribal aztec or floral all-over print fleece"],
                    "ocr_signatures": [],
                    "text_keywords": ["patterned", "aztec", "geometric", "printed", "rare print", "abstract", "tribal"],
                    "negative_keywords": ["solid", "plain"],
                },
            ],
        },
    ),
    (
        ["ralph lauren quarter zip", "polo quarter zip", "ralph lauren half zip", "ralph lauren jumper", "ralph lauren knit"],
        {
            "product_family": "Polo Ralph Lauren Quarter-Zip Knitwear",
            "domain": "fashion",
            "research_notes": "Multi-category combinable facets: Knit/Style (Cable-Knit vs Smooth/Estate-Rib vs Waffle/Fleece) + Material (Wool/Merino/Lambswool vs Cashmere vs Cotton/Pima). Items like Cable-Knit Wool receive BOTH tags.",
            "variants": [
                {
                    "variant_slug": "cable_knit",
                    "variant_name": "Cable-Knit",
                    "dimension": "Style",
                    "modality": "visual",
                    "short_description": "Raised braided vertical rope cable-knit weave (available in cotton, wool, or cashmere).",
                    "resale_tier": "PREMIUM",
                    "est_market_value_gbp": 58.0,
                    "visual_cues": [
                        "a close-up photo of a chunky braided cable-knit sweater jumper with raised twisted rope vertical cable stitches",
                        "textured vertical cable knit weave jumper with raised rope braids across torso and sleeves",
                    ],
                    "ocr_signatures": [],
                    "text_keywords": ["cable knit", "cable-knit", "cable", "braided", "rope knit", "chunky cable"],
                    "negative_keywords": ["estate rib", "estate-rib", "sweatshirt", "fleece", "polar fleece", "waffle", "interlock", "ribbed", "fair isle", "argyle"],
                },
                {
                    "variant_slug": "smooth_estate_rib",
                    "variant_name": "Smooth / Estate-Rib",
                    "dimension": "Style",
                    "modality": "visual",
                    "short_description": "Flat smooth jersey / Estate-Rib / ribbed pullover without braided cable ridges.",
                    "resale_tier": "STANDARD",
                    "est_market_value_gbp": 34.0,
                    "visual_cues": [
                        "a photo of a smooth flat cotton jersey quarter-zip sweatshirt pullover with plain smooth fabric and no cable texture",
                        "flat plain smooth or vertical ribbed knit pullover sweater without any twisted rope cable braids",
                    ],
                    "ocr_signatures": ["ESTATE RIB", "ESTATE-RIB"],
                    "text_keywords": ["estate rib", "estate-rib", "sweatshirt", "jersey", "interlock", "fine knit", "smooth", "ribbed"],
                    "negative_keywords": ["cable", "cable-knit", "cable knit", "waffle", "polar fleece", "sherpa", "corduroy"],
                },
                {
                    "variant_slug": "waffle_fleece_bear",
                    "variant_name": "Waffle / Fleece / Graphic",
                    "dimension": "Style",
                    "modality": "label",
                    "short_description": "Textured waffle thermal, polar fleece, Fair Isle, or Polo Bear / Crest graphic edition.",
                    "resale_tier": "HIGH",
                    "est_market_value_gbp": 48.0,
                    "visual_cues": [
                        "plush polar fleece or waffle thermal grid pullover or sweater with large Polo Bear graphic",
                    ],
                    "ocr_signatures": ["POLO SPORT", "HI TECH"],
                    "text_keywords": ["waffle", "fleece", "polar fleece", "polo bear", "aztec", "corduroy", "velour", "big pony", "crest", "fair isle", "argyle", "jacquard"],
                    "negative_keywords": ["cable", "cable-knit", "estate rib"],
                },
                {
                    "variant_slug": "wool_merino_lambswool",
                    "variant_name": "Wool / Merino / Lambswool",
                    "dimension": "Material",
                    "modality": "label",
                    "short_description": "100% Wool, Italian Merino, or Lambswool yarn (combines with Cable-Knit or Smooth).",
                    "resale_tier": "HIGH",
                    "est_market_value_gbp": 48.0,
                    "visual_cues": [],
                    "ocr_signatures": ["100% WOOL", "MERINO", "LAMBSWOOL", "PURE NEW WOOL", "ITALIAN YARN"],
                    "text_keywords": ["wool", "merino", "lambswool", "lambs wool", "pure new wool"],
                    "negative_keywords": ["100% cotton"],
                },
                {
                    "variant_slug": "cashmere_blend",
                    "variant_name": "Cashmere",
                    "dimension": "Material",
                    "modality": "label",
                    "short_description": "Pure cashmere or luxury wool/cotton-cashmere blend.",
                    "resale_tier": "PREMIUM",
                    "est_market_value_gbp": 65.0,
                    "visual_cues": [],
                    "ocr_signatures": ["CASHMERE"],
                    "text_keywords": ["cashmere"],
                    "negative_keywords": [],
                },
                {
                    "variant_slug": "cotton_pima",
                    "variant_name": "Cotton / Pima",
                    "dimension": "Material",
                    "modality": "label",
                    "short_description": "100% Cotton or luxury soft Pima cotton construction.",
                    "resale_tier": "STANDARD",
                    "est_market_value_gbp": 36.0,
                    "visual_cues": [],
                    "ocr_signatures": ["PIMA COTTON", "100% COTTON", "SUPIMA"],
                    "text_keywords": ["pima", "cotton", "supima"],
                    "negative_keywords": ["100% wool", "100% merino", "pure wool"],
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
        """Perform deep variant research for a broad fashion or tech search query.

        1. Inspects sample real Vinted UK listings (titles + asking prices) so variants reflect real market supply.
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
            "You are an elite secondary-market product taxonomist, computer-vision prompt engineer, and UK Vinted reseller analyst. "
            "Given a broad fashion or tech product search query, break it down into 4 to 8 orthogonal, combinable product variants "
            "grouped across 1 or 2 clean facets (`dimension`s) so items can carry MULTIPLE tags when appropriate (e.g. Style='Cable-Knit' + Material='Wool / Merino').\n\n"
            "CRITICAL REQUIREMENTS:\n"
            "1. Use clean `dimension` names so orthogonal attributes combine naturally:\n"
            "   - FASHION examples: `dimension`: 'Style' (e.g. Cable-Knit vs Smooth / Estate-Rib, or 1996 Retro 700 vs Summit 800) + `dimension`: 'Material' (e.g. Wool / Merino, Cashmere, Cotton / Pima) or `dimension`: 'Cut' (e.g. Cropped / Short, Gilet / Vest).\n"
            "   - TECH / CAMERAS examples: `dimension`: 'Series' (e.g. Vintage CCD IXUS 70/75/80, Flagship Wi-Fi IXUS 285 HS, Modern 20MP IXUS 185/190) + `dimension`: 'Kit' (e.g. Boxed / Complete Charger Kit).\n"
            "2. Set `modality` for each variant:\n"
            "   - `'visual'`: silhouette, knit texture, baffle geometry, sleeve cut, or camera body/lens markings visible from product photos (mutually exclusive within its `dimension`).\n"
            "   - `'label'`: fabric composition or bundle accessories verified strictly from listing text or neck/care tag OCR (e.g. Material or Kit; never guessed from distant garment photos, and blends like Wool + Cashmere can co-occur).\n"
            "3. NEVER include the parent brand name or generic words from the search query itself (e.g. 'polo', 'ralph lauren', 'quarter zip', 'jumper', 'knitted', 'camera') in `text_keywords` or `ocr_signatures`. Every cue MUST uniquely distinguish that specific variant from sibling variants.\n"
            "4. NEVER put terms belonging to a DIFFERENT `dimension` inside `negative_keywords` (e.g. do NOT put 'cable' in Wool's `negative_keywords`, because 'Cable-Knit' + 'Wool' co-occur!). Only put mutually exclusive sibling terms from the SAME `dimension`.\n"
            "5. For every variant, provide:\n"
            "   - `variant_slug`: lowercase snake_case identifier\n"
            "   - `variant_name`: concise, clean human-readable chip label (e.g. 'Cable-Knit', 'Wool / Merino', '1996 Retro (700)')\n"
            "   - `dimension`: facet group name ('Style', 'Material', 'Edition', 'Cut', 'Series', 'Kit')\n"
            "   - `modality`: 'visual' or 'label'\n"
            "   - `short_description`: concise physical & resale distinction\n"
            "   - `resale_tier`: one of 'PREMIUM', 'HIGH', 'STANDARD', 'BUDGET'\n"
            "   - `est_market_value_gbp`: realistic UK secondhand sold price in GBP (number)\n"
            "   - `visual_cues`: 2 to 5 specific visual phrases for CLIP image matching (empty list [] if `modality` is 'label')\n"
            "   - `ocr_signatures`: exact discriminative text printed on tags/faceplates (e.g. '100% WOOL', 'CASHMERE', '700', 'SUMMIT', '285 HS', '7.1 MEGA PIXELS') — NEVER brand names!\n"
            "   - `text_keywords`: lowercase discriminative title/description substrings\n"
            "   - `negative_keywords`: lowercase mutually exclusive terms within the SAME `dimension`\n"
            "6. Respond ONLY with a valid JSON object matching schema: "
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
        domain = str(raw.get("domain") or "other").lower()
        default_dim = "Series" if domain == "tech" else "Style"

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

        def _is_generic_cue(phrase: str) -> bool:
            toks = re.findall(r"[a-z0-9]+", phrase.lower())
            if not toks:
                return True
            if any(any(ch.isdigit() for ch in tok) for tok in toks):
                return False
            return all(tok in stop_tokens for tok in toks)

        variants_out = []
        seen_slugs = set()
        for idx, v in enumerate(raw.get("variants") or []):
            if not isinstance(v, dict):
                continue
            name = str(v.get("variant_name") or v.get("name") or f"Variant {idx + 1}").strip()
            slug = re.sub(r"[^a-z0-9]+", "_", str(v.get("variant_slug") or name).lower()).strip("_") or f"variant_{idx + 1}"
            if slug in seen_slugs:
                slug = f"{slug}_{idx + 1}"
            seen_slugs.add(slug)

            dim = str(v.get("dimension") or default_dim).strip().title() or default_dim
            modality = str(v.get("modality") or "").strip().lower()
            if modality not in ("visual", "label"):
                modality = "label" if dim.lower() in ("material", "fabric", "composition", "kit", "bundle") else "visual"

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
            ][:18]
            clean_negs = [
                str(x).strip().lower()
                for x in (v.get("negative_keywords") or [])
                if str(x).strip() and not _is_generic_cue(str(x))
            ][:14]
            clean_vcues = (
                [] if modality == "label"
                else [str(x).strip() for x in (v.get("visual_cues") or []) if str(x).strip()][:6]
            )

            variants_out.append({
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
            })

        return {
            "product_family": str(raw.get("product_family") or search_text),
            "domain": str(raw.get("domain") or "other"),
            "research_notes": str(raw.get("research_notes") or f"Variant breakdown for {search_text}."),
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
                cloned["provider_used"] = "Specialist Knowledge Base + Vinted UK Corpus"
                return self._normalize_catalogue(cloned, search_text, median_price, brand=brand) | {
                    "provider_used": "Specialist Knowledge Base + Vinted UK Corpus"
                }

        # 2. Dynamic Live Vinted Corpus Model-Code & Sub-Variant Miner
        is_tech = any(w in q_lower for w in (
            "camera", "canon", "cannon", "sony", "nikon", "fuji", "fujifilm", "olympus", "lumix",
            "coolpix", "finepix", "powershot", "3ds", "2ds", "ds", "gameboy", "switch", "airpods",
            "ipod", "walkman", "bose", "wh-", "garmin", "dyson",
        ))

        base_tokens = set(re.findall(r"[a-z0-9]+", q_lower))
        if brand:
            base_tokens.update(re.findall(r"[a-z0-9]+", brand.lower()))

        model_counter: Counter[str] = Counter()
        model_prices: Dict[str, List[float]] = {}

        model_regex = re.compile(r"\b([a-z]{0,4}\d{2,4}[a-z]{0,3}(?:\s+(?:hs|is|mark\s*ii|mark\s*iii|xl|pro|max|oled|lite|v\d))?)\b", re.IGNORECASE)
        fashion_modifiers = [
            ("cable", "Cable-Knit", "Style", "visual", "HIGH", ["chunky braided twisted rope cable-knit pattern across chest and sleeves"], []),
            ("gore-tex", "GORE-TEX Waterproof", "Style", "visual", "PREMIUM", ["waterproof technical shell jacket with GORE-TEX embroidery"], ["GORE-TEX", "GORETEX"]),
            ("hoodie", "Hooded", "Style", "visual", "STANDARD", ["hooded pullover or zip hoodie with drawstring hood"], []),
            ("crewneck", "Crewneck", "Style", "visual", "STANDARD", ["collarless ribbed crew neck pullover without hood"], []),
            ("quarter zip", "Quarter-Zip", "Style", "visual", "HIGH", ["stand collar pullover with short quarter zipper at neck"], []),
            ("full zip", "Full-Zip", "Style", "visual", "HIGH", ["full length front zipper jacket"], []),
            ("gilet", "Gilet / Vest", "Cut", "visual", "STANDARD", ["sleeveless padded gilet vest bodywarmer"], []),
            ("cropped", "Cropped", "Cut", "visual", "HIGH", ["short waist-length boxy cropped silhouette"], []),
            ("sherpa", "Sherpa / Pile", "Style", "visual", "HIGH", ["fluffy textured high-pile teddy sherpa fleece"], []),
            ("cashmere", "Cashmere", "Material", "label", "PREMIUM", [], ["100% CASHMERE", "CASHMERE"]),
            ("wool", "Wool / Merino", "Material", "label", "HIGH", [], ["100% WOOL", "MERINO", "LAMBSWOOL"]),
            ("cotton", "Cotton", "Material", "label", "STANDARD", [], ["100% COTTON", "PIMA COTTON"]),
            ("leather", "Leather", "Material", "label", "PREMIUM", [], ["100% LEATHER", "GENUINE LEATHER"]),
            ("suede", "Suede", "Material", "label", "HIGH", [], ["SUEDE"]),
        ]

        for s in samples:
            title = str(s.get("title") or "")
            price = float(s.get("price") or 0.0)
            for m in model_regex.finditer(title):
                code = re.sub(r"\s+", " ", m.group(1).strip().upper())
                if code.lower() in base_tokens or re.match(r"^(19\d\d|20\d\d|\d+GB|\d+MM|\d+MP|UK\d+)$", code):
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
                mined_variants.append({
                    "variant_slug": re.sub(r"[^a-z0-9]+", "_", code.lower()).strip("_"),
                    "variant_name": code,
                    "dimension": "Series" if is_tech else "Edition",
                    "modality": "visual",
                    "short_description": f"Identified {code} sub-model discovered from live UK Vinted listings.",
                    "resale_tier": tier,
                    "est_market_value_gbp": max(8.0, avg_p),
                    "visual_cues": [
                        f"{search_text} {code} product photo showing {code} design and markings",
                    ],
                    "ocr_signatures": [code, code.replace(" ", "")],
                    "text_keywords": [code.lower(), code.lower().replace(" ", "")],
                    "negative_keywords": other_codes,
                })
            if is_tech:
                mined_variants.append({
                    "variant_slug": "boxed_charger_kit",
                    "variant_name": "Boxed / Charger Kit",
                    "dimension": "Kit",
                    "modality": "label",
                    "short_description": "Complete bundle including charger, battery, or original box.",
                    "resale_tier": "HIGH",
                    "est_market_value_gbp": round(median_price * 1.2, 2),
                    "visual_cues": [],
                    "ocr_signatures": [],
                    "text_keywords": ["boxed", "with box", "with charger", "charger included", "complete"],
                    "negative_keywords": ["no charger", "untested", "spares", "faulty"],
                })
        else:
            for kw, title_label, dim, mod, tier, vcues, ocrs in fashion_modifiers:
                if kw in q_lower:
                    continue
                matches = [s for s in samples if kw in str(s.get("title") or "").lower()]
                if len(matches) >= 2:
                    p_list = [float(x["price"]) for x in matches if float(x.get("price") or 0) > 0] or [median_price]
                    mined_variants.append({
                        "variant_slug": re.sub(r"[^a-z0-9]+", "_", kw),
                        "variant_name": title_label,
                        "dimension": dim,
                        "modality": mod,
                        "short_description": f"{title_label} {search_text}.",
                        "resale_tier": tier,
                        "est_market_value_gbp": round(sum(p_list) / len(p_list), 2),
                        "visual_cues": [f"{search_text} {c}" for c in vcues],
                        "ocr_signatures": ocrs,
                        "text_keywords": [kw],
                        "negative_keywords": [],
                    })

        if len(mined_variants) < 2:
            if is_tech:
                mined_variants = [
                    {
                        "variant_slug": "pro_flagship_edition",
                        "variant_name": "Pro / Wi-Fi / High-Zoom",
                        "dimension": "Series",
                        "modality": "visual",
                        "short_description": "Higher-tier specification with Wi-Fi, HS sensor, XL/OLED, or long optical zoom.",
                        "resale_tier": "PREMIUM",
                        "est_market_value_gbp": round(median_price * 1.4, 2),
                        "visual_cues": [f"high-end black or silver {search_text} with large zoom lens barrel and Wi-Fi HD markings"],
                        "ocr_signatures": ["HS", "Wi-Fi", "FULL HD", "PRO", "XL", "OLED", "12x", "10x"],
                        "text_keywords": ["hs", "wifi", "wi-fi", "pro", "xl", "oled", "mark ii", "mark iii"],
                        "negative_keywords": ["spares", "repairs", "faulty"],
                    },
                    {
                        "variant_slug": "vintage_classic_edition",
                        "variant_name": "Classic / Vintage CCD",
                        "dimension": "Series",
                        "modality": "visual",
                        "short_description": "Classic compact metallic or original release model.",
                        "resale_tier": "HIGH",
                        "est_market_value_gbp": round(median_price * 1.05, 2),
                        "visual_cues": [f"compact metallic retro {search_text} in clean working condition"],
                        "ocr_signatures": ["CCD", "MEGA PIXELS"],
                        "text_keywords": ["ccd", "vintage", "retro"],
                        "negative_keywords": ["faulty", "spares", "wifi", "wi-fi"],
                    },
                    {
                        "variant_slug": "boxed_complete_bundle",
                        "variant_name": "Boxed / Charger Kit",
                        "dimension": "Kit",
                        "modality": "label",
                        "short_description": "Complete bundle with original box, battery, and charger.",
                        "resale_tier": "HIGH",
                        "est_market_value_gbp": round(median_price * 1.25, 2),
                        "visual_cues": [],
                        "ocr_signatures": [],
                        "text_keywords": ["boxed", "with box", "charger included", "with charger", "complete"],
                        "negative_keywords": ["no charger", "untested"],
                    },
                ]
            else:
                mined_variants = [
                    {
                        "variant_slug": "archive_special_edition",
                        "variant_name": "Archive / Special Edition",
                        "dimension": "Style",
                        "modality": "visual",
                        "short_description": "Vintage archive issue, GORE-TEX technical shell, or special edition.",
                        "resale_tier": "PREMIUM",
                        "est_market_value_gbp": round(median_price * 1.35, 2),
                        "visual_cues": [f"vintage archive or technical {search_text} with distinct woven badge detailing"],
                        "ocr_signatures": ["GORE-TEX", "LIMITED", "MADE IN USA"],
                        "text_keywords": ["archive", "vintage", "retro", "gore-tex", "collab", "limited", "rare"],
                        "negative_keywords": ["kids", "junior"],
                    },
                    {
                        "variant_slug": "wool_cashmere_blend",
                        "variant_name": "Wool / Cashmere",
                        "dimension": "Material",
                        "modality": "label",
                        "short_description": "Premium natural wool, merino, lambswool, or cashmere composition.",
                        "resale_tier": "HIGH",
                        "est_market_value_gbp": round(median_price * 1.2, 2),
                        "visual_cues": [],
                        "ocr_signatures": ["100% WOOL", "MERINO", "LAMBSWOOL", "CASHMERE"],
                        "text_keywords": ["wool", "merino", "lambswool", "cashmere"],
                        "negative_keywords": ["acrylic", "100% cotton"],
                    },
                    {
                        "variant_slug": "cotton_core",
                        "variant_name": "Cotton",
                        "dimension": "Material",
                        "modality": "label",
                        "short_description": "Breathable 100% cotton or Pima cotton weave.",
                        "resale_tier": "STANDARD",
                        "est_market_value_gbp": round(median_price * 0.95, 2),
                        "visual_cues": [],
                        "ocr_signatures": ["100% COTTON", "PIMA COTTON"],
                        "text_keywords": ["cotton", "pima"],
                        "negative_keywords": ["100% wool", "100% cashmere"],
                    },
                ]

        return self._normalize_catalogue(
            {
                "product_family": search_text.title(),
                "domain": "tech" if is_tech else "fashion",
                "research_notes": (
                    "Generated via Live Vinted UK Corpus Mining. Add a free Gemini, Groq, or OpenRouter API key in "
                    "'AI Models & Keys' to enable live web-grounded Deep Research for arbitrary products."
                ),
                "variants": mined_variants,
            },
            search_text,
            median_price,
            brand=brand,
        ) | {"provider_used": "Live Vinted Corpus Miner (Zero-Key Local Mode)"}


llm_router = MultiLLMRouter()

