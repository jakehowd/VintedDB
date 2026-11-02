"""Application configuration."""

import os

# Vinted settings — exclusively .co.uk
VINTED_BASE_URL = "https://www.vinted.co.uk"

# How often (in minutes) the background scheduler polls Vinted
POLL_INTERVAL_MINUTES = int(os.environ.get("POLL_INTERVAL_MINUTES", "30"))

# SQLite database path (relative to working directory)
DB_PATH = os.environ.get("DB_PATH", "vinted_tracker.db")
DATABASE_URL = os.environ.get("DATABASE_URL", f"sqlite:///./{DB_PATH}")

# Pre-loaded search terms
DEFAULT_SEARCHES = [
    "North Face Nuptse 700",
    "Ralph Lauren quarter zip",
    "Patagonia Synchilla fleece",
]

# Storage & local vision paths
SCHEDULER_CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "storage", "scheduler_config.json")
VISION_CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "storage", "vision_cache")
VISION_MODELS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "storage", "vision_models")
MAC_OCR_BIN_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "storage", "bin", "mac_vision_ocr")
DEFAULT_PROXIES = os.environ.get("VINTED_PROXIES", os.environ.get("VINTED_PROXY", ""))
DEFAULT_COOKIE = os.environ.get("VINTED_COOKIE", "")


