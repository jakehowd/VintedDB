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
            "product_family": "The North Face Nuptse Down Outerwear",
            "domain": "fashion",
            "research_notes": "Distinguished by fill-power cuff embroidery (700 vs 800), shoulder yoke contrast panels, baffle geometry, sleeve presence (jacket vs gilet), and fleece reversibility.",
            "variants": [
                {
                    "variant_slug": "1996_retro_nuptse",
                    "variant_name": "1996 Retro Nuptse (700 Fill)",
                    "short_description": "Iconic boxy oversized down puffer with shiny ripstop nylon, contrasting black shoulder yoke, stowable collar hood, and '700' embroidered on sleeve cuff.",
                    "resale_tier": "HIGH",
                    "est_market_value_gbp": 115.0,
                    "visual_cues": [
                        "boxy puffer jacket with wide horizontal down baffles and contrasting black shoulder yoke",
                        "shiny ripstop nylon North Face puffer jacket with stand collar and long sleeves",
                        "700 embroidered text on jacket sleeve cuff",
                    ],
                    "ocr_signatures": ["700", "NF0A3C8D", "NF0A3XEO", "STOW POCKET"],
                    "text_keywords": ["1996", "retro nuptse", "1996 retro", "700", "boxy", "nf0a3c8d"],
                    "negative_keywords": ["gilet", "vest", "bodywarmer", "sleeveless", "summit", "800", "reversible", "sherpa", "cropped", "short"],
                },
                {
                    "variant_slug": "summit_series_nuptse",
                    "variant_name": "Summit Series Nuptse (800 / Pertex)",
                    "short_description": "Technical alpine edition featuring Summit Series sleeve crest, higher 800/850 loft down, Pertex or Gore Windstopper shell, and articulated baffling.",
                    "resale_tier": "PREMIUM",
                    "est_market_value_gbp": 165.0,
                    "visual_cues": [
                        "technical alpine expedition down puffer jacket with Summit Series emblem on sleeve",
                        "high loft mountaineering down jacket with chest zip pocket and matte Pertex shell",
                        "800 fill embroidery on cuff and curved alpine shoulder panels",
                    ],
                    "ocr_signatures": ["SUMMIT", "SUMMIT SERIES", "800", "850", "PERTEX", "WINDSTOPPER"],
                    "text_keywords": ["summit", "summit series", "800", "850", "pertex", "windstopper", "baltoro"],
                    "negative_keywords": ["gilet", "vest", "sleeveless", "sherpa", "reversible", "cropped"],
                },
                {
                    "variant_slug": "reversible_sherpa_nuptse",
                    "variant_name": "Reversible / Sherpa Nuptse",
                    "short_description": "Dual-wear Nuptse or high-pile Sherpa fleece exterior Nuptse combining teddy borg fleece texture with baffled down insulation.",
                    "resale_tier": "HIGH",
                    "est_market_value_gbp": 105.0,
                    "visual_cues": [
                        "fluffy textured sherpa borg teddy fleece puffer jacket with North Face logo",
                        "reversible fleece and nylon quilted down puffer coat",
                    ],
                    "ocr_signatures": ["600", "HEATSEEKER"],
                    "text_keywords": ["reversible", "sherpa", "borg", "teddy", "fleece nuptse", "high pile"],
                    "negative_keywords": ["gilet", "vest", "sleeveless", "summit series"],
                },
                {
                    "variant_slug": "cropped_short_nuptse",
                    "variant_name": "Nuptse Short / Cropped Jacket",
                    "short_description": "Women's high-waisted 2-to-3 baffle cropped Nuptse silhouette sitting above the hip.",
                    "resale_tier": "STANDARD",
                    "est_market_value_gbp": 85.0,
                    "visual_cues": [
                        "short cropped waist-length puffer jacket with only three horizontal down baffles",
                        "womens boxy cropped down puffer jacket with tall collar",
                    ],
                    "ocr_signatures": ["NF0A5GGE", "700"],
                    "text_keywords": ["nuptse short", "cropped nuptse", "cropped", "short jacket", "crop"],
                    "negative_keywords": ["gilet", "vest", "sleeveless", "summit"],
                },
                {
                    "variant_slug": "nuptse_gilet_vest",
                    "variant_name": "Nuptse 700 Gilet / Vest",
                    "short_description": "Sleeveless 1996 Retro or vintage 700-fill Nuptse down bodywarmer vest.",
                    "resale_tier": "STANDARD",
                    "est_market_value_gbp": 75.0,
                    "visual_cues": [
                        "sleeveless down puffer gilet vest bodywarmer with black shoulder yoke and no sleeves",
                        "quilted padded sleeveless vest on hanger",
                    ],
                    "ocr_signatures": ["700", "NF0A3JQQ"],
                    "text_keywords": ["gilet", "vest", "bodywarmer", "body warmer", "sleeveless"],
                    "negative_keywords": ["long sleeve"],
                },
            ],
        },
    ),
    (
        ["ixus", "cannon ixus", "canon ixus", "powershot elph"],
        {
            "product_family": "Canon IXUS / PowerShot ELPH Compact Digital Cameras",
            "domain": "tech",
            "research_notes": "Canon IXUS models look similar at a glance but are precisely separated by optical zoom multiplier on the lens ring (3x, 8x, 10x, 12x), focal length (e.g. 25-300mm vs 28-224mm), optical viewfinder window (vintage CCD models), and Wi-Fi HS badging.",
            "variants": [
                {
                    "variant_slug": "ixus_70_75_80_ccd",
                    "variant_name": "Vintage CCD Series (IXUS 70 / 75 / 80 IS / 85 IS)",
                    "short_description": "Highly coveted 2007–2008 ultra-compact metal CCD digicams featuring an optical viewfinder window on the front (IXUS 70/80), 3x optical zoom (5.8-17.4mm / 6.2-18.6mm), and 7.1MP–10MP CCD sensor.",
                    "resale_tier": "PREMIUM",
                    "est_market_value_gbp": 135.0,
                    "visual_cues": [
                        "boxy flat rectangular vintage silver metallic Canon IXUS compact camera with small optical viewfinder window above lens",
                        "retro minimalist brushed aluminum pocket camera with black ring around lens and 3x zoom",
                    ],
                    "ocr_signatures": [
                        "IXUS 70", "IXUS 75", "IXUS 80", "IXUS 85", "SD1000", "SD750", "SD1100",
                        "7.1 MEGA PIXELS", "8.0 MEGA PIXELS", "10.0 MEGA PIXELS", "5.8-17.4", "6.2-18.6", "3x",
                    ],
                    "text_keywords": [
                        "ixus 70", "ixus 75", "ixus 80", "ixus 85", "sd1000", "sd750", "sd1100",
                        "7.1mp", "ccd", "viewfinder", "ixus 60", "ixus 65",
                    ],
                    "negative_keywords": ["185", "190", "285", "175", "160", "12x", "10x", "8x", "wifi", "wi-fi"],
                },
                {
                    "variant_slug": "ixus_285_hs_wifi",
                    "variant_name": "Flagship Zoom CMOS (IXUS 285 HS / 275 HS / 265 HS)",
                    "short_description": "Modern flagship pocket travel zoom with 12x Optical Zoom (4.5-54.0mm / 25-300mm), 20.2MP HS CMOS sensor, Full HD video, and Wi-Fi / NFC connectivity.",
                    "resale_tier": "PREMIUM",
                    "est_market_value_gbp": 175.0,
                    "visual_cues": [
                        "modern sleek matte black silver or purple Canon IXUS camera with thick protruding 12x zoom lens barrel and Wi-Fi icon",
                        "Canon IXUS HS compact digital camera with 12x Optical Zoom printed on lens ring",
                    ],
                    "ocr_signatures": [
                        "285 HS", "275 HS", "265 HS", "ELPH 360", "12x", "4.5-54.0", "25-300", "Wi-Fi", "FULL HD", "HS",
                    ],
                    "text_keywords": ["ixus 285", "285 hs", "285hs", "ixus 275", "ixus 265", "elph 360", "12x zoom", "wifi", "wi-fi"],
                    "negative_keywords": ["ixus 70", "ixus 75", "ixus 80", "ixus 185", "8x"],
                },
                {
                    "variant_slug": "ixus_185_190_175",
                    "variant_name": "Modern 20MP Series (IXUS 175 / 180 / 185 / 190)",
                    "short_description": "Late-generation 20.0MP CCD compact cameras with 8x or 10x Optical Zoom (5.0-40.0mm / 4.3-43.0mm), Eco button, and rounded slim body.",
                    "resale_tier": "HIGH",
                    "est_market_value_gbp": 110.0,
                    "visual_cues": [
                        "slim rounded silver red or black Canon IXUS camera with 8x or 10x Optical Zoom printed on front lens faceplate",
                        "modern compact point and shoot Canon camera without optical viewfinder window",
                    ],
                    "ocr_signatures": [
                        "IXUS 185", "IXUS 190", "IXUS 180", "IXUS 175", "IXUS 160", "IXUS 165", "IXUS 170",
                        "8x", "10x", "5.0-40.0", "4.3-43.0", "20.0", "HD",
                    ],
                    "text_keywords": [
                        "ixus 185", "ixus 190", "ixus 180", "ixus 175", "ixus 170", "ixus 165", "ixus 160",
                        "elph 180", "elph 190", "elph 200", "20mp", "20.0mp", "8x", "10x",
                    ],
                    "negative_keywords": ["ixus 70", "ixus 80", "ixus 285", "12x"],
                },
                {
                    "variant_slug": "ixus_105_115_130_mid",
                    "variant_name": "2010–2013 Colour Compact Series (IXUS 105 / 115 HS / 130 / 132 / 145)",
                    "short_description": "Slim anodised colourful mid-era 12MP–16MP IXUS models (pink, aqua, silver, black) with 4x–8x IS zoom lenses and curved ergonomic corners.",
                    "resale_tier": "HIGH",
                    "est_market_value_gbp": 88.0,
                    "visual_cues": [
                        "curved anodized pink blue silver or black slim Canon IXUS camera with 4x or 5x zoom ring",
                        "compact metallic Canon camera with IMAGE STABILIZER text on lens",
                    ],
                    "ocr_signatures": [
                        "IXUS 105", "IXUS 115", "IXUS 125", "IXUS 130", "IXUS 132", "IXUS 135", "IXUS 140", "IXUS 145", "IXUS 155",
                        "12.1", "14.1", "16.0", "4x", "5x", "IMAGE STABILIZER",
                    ],
                    "text_keywords": [
                        "ixus 105", "ixus 115", "ixus 125", "ixus 130", "ixus 132", "ixus 135",
                        "ixus 140", "ixus 145", "ixus 150", "ixus 155", "ixus 95", "ixus 100",
                    ],
                    "negative_keywords": ["ixus 285", "ixus 185", "ixus 70"],
                },
                {
                    "variant_slug": "ixus_early_classic_40_50",
                    "variant_name": "Early Y2K CCD Classics (IXUS 30 / 40 / 50 / 55 / v2 / 400 / 500)",
                    "short_description": "Early-2000s stainless-steel Y2K CCD digicams (3.2MP–5.0MP) prized for warm filmic lo-fi sensor rendering and chunky steel chassis.",
                    "resale_tier": "STANDARD",
                    "est_market_value_gbp": 65.0,
                    "visual_cues": [
                        "chunky early 2000s stainless steel pocket digital camera with optical viewfinder and small rear LCD",
                        "retro Y2K Canon Digital IXUS metal camera with AiAF badge",
                    ],
                    "ocr_signatures": [
                        "IXUS 30", "IXUS 40", "IXUS 50", "IXUS 55", "IXUS 400", "IXUS 500", "IXUS II",
                        "AiAF", "3.2", "4.0", "5.0 MEGA PIXELS", "DIGITAL IXUS",
                    ],
                    "text_keywords": [
                        "ixus 30", "ixus 40", "ixus 50", "ixus 55", "ixus 400", "ixus 430", "ixus 500",
                        "ixus ii", "ixus v", "ixus v2", "ixus v3", "ixus i",
                    ],
                    "negative_keywords": ["285", "185", "190", "12x", "wifi"],
                },
            ],
        },
    ),
    (
        ["cyber-shot", "cybershot", "sony dsc"],
        {
            "product_family": "Sony Cyber-shot Compact Digital Cameras",
            "domain": "tech",
            "research_notes": "Sony Cyber-shot models split into sliding-cover T-series, Carl Zeiss W-series pocket compacts, and high-end RX100 / HX travel zoom cameras.",
            "variants": [
                {
                    "variant_slug": "cybershot_rx100_series",
                    "variant_name": "Cyber-shot RX100 Pro 1-Inch Series",
                    "short_description": "Enthusiast 1-inch sensor compact (RX100 I–VII) with matte black anodized aluminum body and Zeiss Vario-Sonnar T* f/1.8 lens.",
                    "resale_tier": "PREMIUM",
                    "est_market_value_gbp": 260.0,
                    "visual_cues": ["matte black pro compact Sony camera with large Zeiss T* lens control ring"],
                    "ocr_signatures": ["RX100", "Vario-Sonnar", "ZEISS", "1.8-4.9", "1.8-2.8"],
                    "text_keywords": ["rx100", "dsc-rx100", "rx100m2", "rx100m3", "rx100m4", "rx100m5"],
                    "negative_keywords": ["dsc-w", "dsc-t", "dsc-s"],
                },
                {
                    "variant_slug": "cybershot_t_sliding_series",
                    "variant_name": "Cyber-shot DSC-T Sliding Cover Series (T10 / T70 / T90 / T200)",
                    "short_description": "Ultra-thin Y2K fashion digicam with vertical sliding front lens shield and internal folded Carl Zeiss periscope zoom.",
                    "resale_tier": "HIGH",
                    "est_market_value_gbp": 115.0,
                    "visual_cues": ["ultra flat rectangular metallic Sony Cyber-shot camera with sliding front faceplate cover and non-protruding corner lens"],
                    "ocr_signatures": ["DSC-T", "Carl Zeiss", "Vario-Tessar", "Cyber-shot"],
                    "text_keywords": ["dsc-t", "t10", "t20", "t70", "t77", "t90", "t99", "t100", "t200", "t300", "sliding"],
                    "negative_keywords": ["rx100", "dsc-w830", "dsc-hx"],
                },
                {
                    "variant_slug": "cybershot_w_pocket_series",
                    "variant_name": "Cyber-shot DSC-W Series (W80 / W120 / W350 / W810 / W830)",
                    "short_description": "Classic extending-barrel pocket Cyber-shot cameras with Zeiss or Sony Lens G optics (4x–8x optical zoom).",
                    "resale_tier": "STANDARD",
                    "est_market_value_gbp": 80.0,
                    "visual_cues": ["compact silver black or pink Sony Cyber-shot camera with circular telescoping center zoom lens"],
                    "ocr_signatures": ["DSC-W", "W830", "W810", "W800", "W350", "W530", "W570", "SteadyShot", "Optical SteadyShot"],
                    "text_keywords": ["dsc-w", "w830", "w810", "w800", "w570", "w530", "w350", "w320", "w120", "w80", "w55"],
                    "negative_keywords": ["rx100", "dsc-t", "dsc-hx"],
                },
                {
                    "variant_slug": "cybershot_hx_wx_superzoom",
                    "variant_name": "Cyber-shot WX / HX High-Zoom HS Series (WX220 / WX350 / HX60 / HX90)",
                    "short_description": "Modern CMOS Exmor R travel superzoom Cyber-shot models (10x–30x Sony G / Zeiss zoom, Wi-Fi/NFC).",
                    "resale_tier": "HIGH",
                    "est_market_value_gbp": 145.0,
                    "visual_cues": ["modern travel zoom Sony camera with mode dial, grip, and 10x 20x or 30x G lens"],
                    "ocr_signatures": ["WX220", "WX350", "WX500", "HX50", "HX60", "HX80", "HX90", "Sony Lens G", "20x", "30x"],
                    "text_keywords": ["wx220", "wx350", "wx500", "hx50", "hx60", "hx80", "hx90", "dsc-wx", "dsc-hx"],
                    "negative_keywords": ["dsc-t", "dsc-w800"],
                },
            ],
        },
    ),
    (
        ["synchilla", "patagonia fleece"],
        {
            "product_family": "Patagonia Fleece Outerwear",
            "domain": "fashion",
            "research_notes": "Separated by Snap-T 4-button placket vs Classic Retro-X windproof deep-pile chest pocket vs Better Sweater knit-face fleece.",
            "variants": [
                {
                    "variant_slug": "synchilla_snap_t_patterned",
                    "variant_name": "Lightweight / Heavyweight Patterned Synchilla Snap-T",
                    "short_description": "Signature 4-snap nylon placket pullover with contrasting nylon chest flap pocket in Aztec, geometric, or wildlife all-over prints.",
                    "resale_tier": "PREMIUM",
                    "est_market_value_gbp": 78.0,
                    "visual_cues": ["patterned colorful geometric tribal print Patagonia fleece pullover with 4 snap button collar and chest flap pocket"],
                    "ocr_signatures": ["SYNCHILLA", "PATAGONIA"],
                    "text_keywords": ["patterned", "aztec", "geometric", "printed", "rare print", "oatmeal", "snap-t", "snap t"],
                    "negative_keywords": ["better sweater", "retro-x", "full zip", "vest", "gilet"],
                },
                {
                    "variant_slug": "synchilla_snap_t_solid",
                    "variant_name": "Solid Block-Colour Synchilla Snap-T Pullover",
                    "short_description": "Classic solid colourway (navy, grey, black, forest green) Snap-T fleece pullover with contrast trim and chest snap pocket.",
                    "resale_tier": "HIGH",
                    "est_market_value_gbp": 55.0,
                    "visual_cues": ["solid single color polar fleece pullover jumper with four snap buttons at neck and contrast nylon chest pocket flap"],
                    "ocr_signatures": ["SYNCHILLA", "PATAGONIA"],
                    "text_keywords": ["synchilla", "snap-t", "snap t", "pullover", "button fleece"],
                    "negative_keywords": ["better sweater", "retro-x", "vest", "gilet", "aztec"],
                },
                {
                    "variant_slug": "classic_retro_x_jacket",
                    "variant_name": "Classic Retro-X Deep-Pile Zip Jacket",
                    "short_description": "Shaggy sherpa deep-pile full-zip windproof fleece jacket with vertical contrasting nylon zippered chest patch pocket.",
                    "resale_tier": "PREMIUM",
                    "est_market_value_gbp": 92.0,
                    "visual_cues": ["shaggy teddy sherpa pile full zip fleece jacket with contrasting vertical zippered nylon patch pocket on left chest"],
                    "ocr_signatures": ["PATAGONIA"],
                    "text_keywords": ["retro-x", "retro x", "retro pile", "sherpa", "deep pile"],
                    "negative_keywords": ["snap-t", "better sweater", "vest", "gilet"],
                },
                {
                    "variant_slug": "better_sweater_quarter_full",
                    "variant_name": "Better Sweater Knit-Face Fleece (1/4 Zip or Full Zip)",
                    "short_description": "Heathered sweater-knit exterior fleece with zip-through stand collar and vertical welt pocket.",
                    "resale_tier": "STANDARD",
                    "est_market_value_gbp": 45.0,
                    "visual_cues": ["heathered marl knitted texture quarter zip or full zip Patagonia sweater"],
                    "ocr_signatures": ["PATAGONIA"],
                    "text_keywords": ["better sweater", "quarter zip", "1/4 zip", "marl", "knit fleece"],
                    "negative_keywords": ["snap-t", "retro-x"],
                },
            ],
        },
    ),
    (
        ["ralph lauren quarter zip", "polo quarter zip", "ralph lauren half zip"],
        {
            "product_family": "Polo Ralph Lauren Quarter-Zip Sweaters",
            "domain": "fashion",
            "research_notes": "Separated by fabric construction: chunky Cable-Knit cotton/wool vs Estate-Rib smooth jersey cotton vs Pima/Merino fine knit vs Pony size.",
            "variants": [
                {
                    "variant_slug": "cable_knit_quarter_zip",
                    "variant_name": "Cable-Knit Quarter-Zip Jumper",
                    "short_description": "Textured braided vertical cable-knit weave cotton or wool quarter-zip with embroidered chest pony and suede/metal zip pull.",
                    "resale_tier": "PREMIUM",
                    "est_market_value_gbp": 58.0,
                    "visual_cues": ["braided vertical rope cable knit textured jumper with quarter zip collar and small polo horse logo"],
                    "ocr_signatures": ["POLO RALPH LAUREN"],
                    "text_keywords": ["cable knit", "cable-knit", "cable", "chunky knit", "knitted"],
                    "negative_keywords": ["sweatshirt", "fleece", "big pony"],
                },
                {
                    "variant_slug": "estate_rib_sweatshirt_qzip",
                    "variant_name": "Estate-Rib / Smooth Jersey Sweatshirt Quarter-Zip",
                    "short_description": "Smooth brushed cotton-blend sweatshirt/jersey quarter-zip pullover with ribbed mock-neck collar and suede zipper tab.",
                    "resale_tier": "STANDARD",
                    "est_market_value_gbp": 36.0,
                    "visual_cues": ["smooth flat cotton jersey sweatshirt quarter zip pullover with small embroidered chest pony"],
                    "ocr_signatures": ["POLO", "ESTATE RIB"],
                    "text_keywords": ["estate rib", "estate-rib", "sweatshirt", "jersey", "cotton quarter zip", "pullover"],
                    "negative_keywords": ["cable knit", "cable-knit", "merino", "cashmere", "wool"],
                },
                {
                    "variant_slug": "merino_pima_fine_knit_qzip",
                    "variant_name": "Merino / Pima / Lambswool Fine-Knit Quarter-Zip",
                    "short_description": "Lightweight luxury fine-gauge Merino wool, Pima cotton, or Lambswool dress quarter-zip jumper.",
                    "resale_tier": "HIGH",
                    "est_market_value_gbp": 48.0,
                    "visual_cues": ["fine gauge smooth luxury wool knit quarter zip sweater with rib cuffs"],
                    "ocr_signatures": ["MERINO", "PIMA", "LAMBSWOOL", "100% WOOL"],
                    "text_keywords": ["merino", "pima", "lambswool", "wool", "cashmere", "fine knit"],
                    "negative_keywords": ["sweatshirt", "fleece", "cable"],
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
            "Given a broad fashion or tech product search query, research and break it down into 3 to 6 distinct, commercially important, "
            "visually distinguishable product variants / exact sub-models traded on Vinted UK.\n\n"
            "CRITICAL REQUIREMENTS:\n"
            "1. Work equally well for FASHION (e.g. 'North Face Nuptse' -> 1996 Retro 700, Summit Series 800, Reversible/Sherpa, Cropped/Short, Nuptse Gilet) "
            "and TECH / CAMERAS / CONSOLES (e.g. 'Canon IXUS' -> Vintage CCD IXUS 70/75/80 IS, Flagship Wi-Fi 12x IXUS 285 HS, Modern 20MP 8x/10x IXUS 185/190, Mid-era IXUS 105/115/130, Early Y2K IXUS 40/50).\n"
            "2. For every variant, provide:\n"
            "   - `variant_slug`: lowercase snake_case identifier\n"
            "   - `variant_name`: clear human-readable title\n"
            "   - `short_description`: concise physical & resale distinction\n"
            "   - `resale_tier`: one of 'PREMIUM', 'HIGH', 'STANDARD', 'BUDGET'\n"
            "   - `est_market_value_gbp`: realistic UK secondhand sold price in GBP (number)\n"
            "   - `visual_cues`: 3 to 5 descriptive visual phrases optimized for CLIP vision-language image matching (describe silhouette, baffles, collar, texture, lens zoom ring, viewfinder window, body finish, sleeve length)\n"
            "   - `ocr_signatures`: exact uppercase/numeric text strings printed or embroidered on the physical product visible in photos (e.g. cuff fill '700', '800', 'SUMMIT', style code 'NF0A3C8D', or camera faceplate/lens text '285 HS', '12x', '25-300', '7.1 MEGA PIXELS', 'Wi-Fi')\n"
            "   - `text_keywords`: lowercase title/description substrings, model numbers, nicknames\n"
            "   - `negative_keywords`: lowercase terms indicating the item belongs to a DIFFERENT variant (e.g. 'gilet' excluded from jacket variant)\n"
            "3. Respond ONLY with a valid JSON object matching schema: "
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
            normalized = self._normalize_catalogue(llm_result, search_text, median_price)
            normalized["provider_used"] = provider_used
            normalized["researched_at"] = datetime.now(timezone.utc).isoformat()
            return normalized

        # Fallback: Specialist Knowledge Base + Live Vinted Corpus Cluster Miner
        local_cat = self._local_deep_research_fallback(search_text, brand, samples, median_price)
        local_cat["researched_at"] = datetime.now(timezone.utc).isoformat()
        return local_cat

    def _normalize_catalogue(self, raw: Dict[str, Any], search_text: str, median_price: float) -> Dict[str, Any]:
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

            tier = str(v.get("resale_tier") or "STANDARD").upper()
            if tier not in ("PREMIUM", "HIGH", "STANDARD", "BUDGET"):
                tier = "STANDARD"

            est_val = v.get("est_market_value_gbp")
            try:
                est_float = round(float(est_val), 2) if est_val is not None else round(median_price, 2)
            except Exception:
                est_float = round(median_price, 2)

            variants_out.append({
                "variant_slug": slug,
                "variant_name": name,
                "short_description": str(v.get("short_description") or v.get("description") or "").strip(),
                "resale_tier": tier,
                "est_market_value_gbp": max(5.0, est_float),
                "visual_cues": [str(x).strip() for x in (v.get("visual_cues") or []) if str(x).strip()][:6],
                "ocr_signatures": [str(x).strip() for x in (v.get("ocr_signatures") or []) if str(x).strip()][:14],
                "text_keywords": [str(x).strip().lower() for x in (v.get("text_keywords") or []) if str(x).strip()][:18],
                "negative_keywords": [str(x).strip().lower() for x in (v.get("negative_keywords") or []) if str(x).strip()][:14],
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
                return self._normalize_catalogue(cloned, search_text, median_price)

        # 2. Dynamic Live Vinted Corpus Model-Code & Sub-Variant Miner
        # Mines alphanumeric model codes (e.g. 'G7X Mark II', 'XT-6', '990v5', '501', '3DS XL') or style modifiers!
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
            ("gore-tex", "GORE-TEX / Waterproof Edition", "PREMIUM", ["waterproof hardshell membrane jacket with GORE-TEX logo embroidery"], ["GORE-TEX", "GORETEX"]),
            ("vintage", "Vintage / OG Archive Issue", "HIGH", ["vintage worn-in classic archive garment with retro woven neck tag"], ["MADE IN USA"]),
            ("hoodie", "Hooded Pullover / Zip Hoodie", "STANDARD", ["hooded fleece sweatshirt with drawstring hood"], []),
            ("crewneck", "Crewneck Sweatshirt", "STANDARD", ["collarless ribbed crew neck pullover sweatshirt without hood"], []),
            ("quarter zip", "Quarter-Zip / Half-Zip Pullover", "HIGH", ["stand collar pullover jumper with short metal quarter zipper at neck"], []),
            ("full zip", "Full-Zip Jacket", "HIGH", ["full length front zipper jacket"], []),
            ("gilet", "Sleeveless Gilet / Bodywarmer", "STANDARD", ["sleeveless padded vest gilet bodywarmer"], []),
            ("sherpa", "Sherpa / High-Pile Fleece", "HIGH", ["fluffy textured high pile teddy sherpa fleece"], []),
            ("leather", "Full-Grain Leather Edition", "PREMIUM", ["smooth grained leather construction"], ["LEATHER"]),
            ("suede", "Suede Edition", "HIGH", ["brushed matte nap suede upper construction"], []),
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
                    "variant_name": f"{brand or search_text.title()} {code}".strip(),
                    "short_description": f"Identified {code} sub-model discovered from live UK Vinted marketplace listings.",
                    "resale_tier": tier,
                    "est_market_value_gbp": max(8.0, avg_p),
                    "visual_cues": [
                        f"{search_text} {code} product photo showing {code} body design and faceplate markings",
                    ],
                    "ocr_signatures": [code, code.replace(" ", "")],
                    "text_keywords": [code.lower(), code.lower().replace(" ", "")],
                    "negative_keywords": other_codes,
                })
        else:
            # Check fashion modifier hits across samples or build sensible archetype variants
            for kw, title_label, tier, vcues, ocrs in fashion_modifiers:
                matches = [s for s in samples if kw in str(s.get("title") or "").lower()]
                if len(matches) >= 2:
                    p_list = [float(x["price"]) for x in matches if float(x.get("price") or 0) > 0] or [median_price]
                    mined_variants.append({
                        "variant_slug": re.sub(r"[^a-z0-9]+", "_", kw),
                        "variant_name": f"{search_text.title()} — {title_label}",
                        "short_description": f"{title_label} variant of {search_text}.",
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
                        "variant_name": f"{search_text.title()} — Pro / High-Spec Wi-Fi / Zoom Edition",
                        "short_description": "Higher-tier specification featuring superior optical zoom, Wi-Fi/HS sensor, or XL/OLED hardware.",
                        "resale_tier": "PREMIUM",
                        "est_market_value_gbp": round(median_price * 1.4, 2),
                        "visual_cues": [f"high-end black or silver {search_text} with large zoom lens barrel and Wi-Fi HD markings"],
                        "ocr_signatures": ["HS", "Wi-Fi", "FULL HD", "PRO", "XL", "OLED", "12x", "10x"],
                        "text_keywords": ["hs", "wifi", "wi-fi", "pro", "xl", "oled", "mark ii", "mark iii"],
                        "negative_keywords": ["spares", "repairs", "faulty"],
                    },
                    {
                        "variant_slug": "vintage_classic_edition",
                        "variant_name": f"{search_text.title()} — Classic / Vintage CCD Edition",
                        "short_description": "Classic compact metallic or original release model.",
                        "resale_tier": "HIGH",
                        "est_market_value_gbp": round(median_price * 1.05, 2),
                        "visual_cues": [f"compact metallic retro {search_text} in clean working condition"],
                        "ocr_signatures": ["CCD", "MEGA PIXELS", "OPTICAL"],
                        "text_keywords": ["ccd", "vintage", "retro", "classic", "silver"],
                        "negative_keywords": ["faulty", "spares"],
                    },
                    {
                        "variant_slug": "boxed_complete_bundle",
                        "variant_name": f"{search_text.title()} — Complete Boxed / Charger Bundle",
                        "short_description": "Complete retail bundle with original box, battery, charger, and pouch.",
                        "resale_tier": "HIGH",
                        "est_market_value_gbp": round(median_price * 1.25, 2),
                        "visual_cues": [f"{search_text} photographed next to original retail box, battery charger, and cables"],
                        "ocr_signatures": [],
                        "text_keywords": ["boxed", "with box", "charger included", "complete", "bundle"],
                        "negative_keywords": ["no charger", "untested"],
                    },
                ]
            else:
                mined_variants = [
                    {
                        "variant_slug": "premium_edition",
                        "variant_name": f"{search_text.title()} — Archive / Technical / Collaboration Edition",
                        "short_description": "High-demand colourway, heavyweight archive cut, GORE-TEX/technical shell, or special edition.",
                        "resale_tier": "PREMIUM",
                        "est_market_value_gbp": round(median_price * 1.35, 2),
                        "visual_cues": [f"premium technical or vintage archive {search_text} with distinct logo detailing"],
                        "ocr_signatures": ["GORE-TEX", "LIMITED", "ARCHIVE"],
                        "text_keywords": ["archive", "vintage", "retro", "gore-tex", "collab", "limited", "rare"],
                        "negative_keywords": ["kids", "junior"],
                    },
                    {
                        "variant_slug": "core_classic_edition",
                        "variant_name": f"{search_text.title()} — Classic Core Edition",
                        "short_description": "Standard mainline retail release in core colourways.",
                        "resale_tier": "STANDARD",
                        "est_market_value_gbp": round(median_price, 2),
                        "visual_cues": [f"classic standard {search_text} garment laid flat"],
                        "ocr_signatures": [],
                        "text_keywords": [w for w in re.findall(r"[a-z0-9]+", q_lower) if len(w) > 2][:4],
                        "negative_keywords": ["gilet", "vest"],
                    },
                ]

        return self._normalize_catalogue(
            {
                "product_family": search_text.title(),
                "domain": "tech" if is_tech else "fashion",
                "research_notes": (
                    "Generated via Live Vinted UK Corpus Mining. Add a free Gemini, Groq, or OpenRouter API key in "
                    "'AI Engine' settings to unlock live web-grounded Deep Research for arbitrary products!"
                ),
                "variants": mined_variants,
                "provider_used": "Live Vinted Corpus Miner (Zero-Key Local Mode)",
            },
            search_text,
            median_price,
        ) | {"provider_used": "Live Vinted Corpus Miner (Zero-Key Local Mode)"}


llm_router = MultiLLMRouter()

