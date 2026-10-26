"""Local Multimodal Vision AI & Faceted Multi-Category Product Variant Classifier.

Runs 100% locally on Apple Silicon / CPU with zero per-item API cost:
1. Local ViT-B/32 Vision + Text Embeddings via ONNX Runtime (`fastembed`)
2. Native Apple M2 Neural Engine Image OCR (`mac_vision_ocr` Swift Vision helper, `pytesseract` fallback)
3. Per-Dimension Contrastive Prototype Learner (separates visual facets cleanly, e.g. Cable-Knit vs Smooth)
4. Multi-Category Assignment (combines orthogonal facets like Style='Cable-Knit' + Material='Wool / Merino')
5. Online Incremental Feedback Learning when users manually edit tags
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import re
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

import httpx
import numpy as np
from PIL import Image
from sqlalchemy.orm import Session

from ..config import MAC_OCR_BIN_PATH, VISION_CACHE_DIR, VISION_MODELS_DIR
from ..models import ProductVariant, SoldItem, TrackedSearch

logger = logging.getLogger(__name__)

TIER_RANK = {"PREMIUM": 4, "HIGH": 3, "STANDARD": 2, "BUDGET": 1}


def _l2_normalize(vec: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vec))
    if norm < 1e-9:
        return vec
    return vec / norm


def _word_boundary_match(needle: str, haystack_lower: str) -> bool:
    """Strict alphanumeric boundary match so 'ixus 70' never matches 'ixus 700' and '80' never matches '180'."""
    n = needle.strip().lower()
    if not n:
        return False
    pattern = r"(?<![a-z0-9])" + re.escape(n) + r"(?![a-z0-9])"
    return bool(re.search(pattern, haystack_lower))


def _build_stop_tokens(tracked_search: TrackedSearch) -> set[str]:
    """Tokens from the parent search/brand that should never act as discriminative variant cues."""
    tokens = {
        t for t in re.findall(r"[a-z0-9]+", (tracked_search.search_text or "").lower())
        if not any(ch.isdigit() for ch in t)
    }
    if tracked_search.brand:
        tokens.update(re.findall(r"[a-z0-9]+", tracked_search.brand.lower()))
    tokens.update({
        "the", "and", "with", "for", "mens", "womens", "unisex", "size",
        "polo", "knitted", "jumper", "sweater", "pullover", "top",
        "camera", "digital", "compact",
    })
    return tokens


def _is_generic_cue(phrase: str, stop_tokens: set[str]) -> bool:
    toks = re.findall(r"[a-z0-9]+", (phrase or "").lower())
    if not toks:
        return True
    if any(any(ch.isdigit() for ch in tok) for tok in toks):
        return False
    return all(tok in stop_tokens for tok in toks)


class LocalVariantVisionService:
    """Singleton managing local ONNX CLIP encoders, Apple Neural OCR, per-dimension prototypes, and multi-tag classification."""

    VISION_MODEL_NAME = "Qdrant/clip-ViT-B-32-vision"
    TEXT_MODEL_NAME = "Qdrant/clip-ViT-B-32-text"

    def __init__(self) -> None:
        self._model_lock = threading.RLock()
        self._vision_encoder = None
        self._text_encoder = None
        self._clip_load_failed = False
        self._clip_error_msg: str = ""
        self._total_images_classified: int = 0
        self._last_inference_ms: Optional[int] = None

        os.makedirs(VISION_CACHE_DIR, exist_ok=True)
        os.makedirs(VISION_MODELS_DIR, exist_ok=True)
        self._ensure_mac_ocr_binary()

    # -----------------------------------------------------------------------
    # Engine Initialization & Telemetry
    # -----------------------------------------------------------------------

    def _ensure_mac_ocr_binary(self) -> bool:
        """Ensure native macOS Vision Neural Engine OCR helper is compiled."""
        if platform.system() != "Darwin":
            return False
        if os.path.exists(MAC_OCR_BIN_PATH) and os.access(MAC_OCR_BIN_PATH, os.X_OK):
            return True
        swift_src = Path(__file__).resolve().parent / "mac_vision_ocr.swift"
        if not swift_src.exists():
            return False
        try:
            os.makedirs(os.path.dirname(MAC_OCR_BIN_PATH), exist_ok=True)
            res = subprocess.run(
                ["/usr/bin/swiftc", "-O", str(swift_src), "-o", MAC_OCR_BIN_PATH],
                capture_output=True,
                text=True,
                timeout=35,
            )
            return res.returncode == 0 and os.path.exists(MAC_OCR_BIN_PATH)
        except Exception as exc:
            logger.debug("Could not compile mac_vision_ocr: %s", exc)
            return False

    def _ensure_clip_loaded(self) -> bool:
        if self._vision_encoder is not None and self._text_encoder is not None:
            return True
        if self._clip_load_failed:
            return False
        with self._model_lock:
            if self._vision_encoder is not None and self._text_encoder is not None:
                return True
            try:
                from fastembed import ImageEmbedding, TextEmbedding

                logger.info("Loading local ONNX CLIP Vision & Text encoders (%s)...", self.VISION_MODEL_NAME)
                self._vision_encoder = ImageEmbedding(
                    model_name=self.VISION_MODEL_NAME,
                    cache_dir=VISION_MODELS_DIR,
                )
                self._text_encoder = TextEmbedding(
                    model_name=self.TEXT_MODEL_NAME,
                    cache_dir=VISION_MODELS_DIR,
                )
                logger.info("Local ONNX CLIP Vision & Text encoders ready.")
                return True
            except Exception as exc:
                self._clip_load_failed = True
                self._clip_error_msg = str(exc)
                logger.warning("Local ONNX CLIP failed to load (%s); using Neural OCR + text fallback.", exc)
                return False

    def get_engine_status(self) -> Dict[str, Any]:
        mac_ocr_ready = os.path.exists(MAC_OCR_BIN_PATH) and os.access(MAC_OCR_BIN_PATH, os.X_OK)
        clip_cached = any(Path(VISION_MODELS_DIR).glob("*clip*")) if os.path.exists(VISION_MODELS_DIR) else False
        return {
            "vision_backbone": self.VISION_MODEL_NAME,
            "clip_loaded_in_ram": self._vision_encoder is not None,
            "clip_weights_cached": clip_cached or (self._vision_encoder is not None),
            "ocr_engine": "Apple Vision Neural OCR" if mac_ocr_ready else "Local OCR",
            "mac_neural_ocr_ready": mac_ocr_ready,
            "total_images_classified": self._total_images_classified,
            "last_inference_ms": self._last_inference_ms,
            "error": self._clip_error_msg or None,
        }

    # -----------------------------------------------------------------------
    # Image Caching, Embeddings & Neural OCR
    # -----------------------------------------------------------------------

    def get_cached_image_path(self, url: str) -> Optional[str]:
        if not url or not url.startswith(("http://", "https://")):
            return None
        key = hashlib.sha1(url.encode("utf-8")).hexdigest()
        cached_path = os.path.join(VISION_CACHE_DIR, f"{key}.jpg")
        if os.path.exists(cached_path) and os.path.getsize(cached_path) > 256:
            return cached_path
        return None

    def _download_image_cached(self, url: str) -> Optional[str]:
        if not url or not url.startswith(("http://", "https://")):
            return None
        existing = self.get_cached_image_path(url)
        if existing:
            return existing

        key = hashlib.sha1(url.encode("utf-8")).hexdigest()
        cached_path = os.path.join(VISION_CACHE_DIR, f"{key}.jpg")
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
                "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            }
            resp = httpx.get(url, headers=headers, timeout=7.5, follow_redirects=True)
            if resp.status_code != 200 or len(resp.content) < 256:
                return None

            from io import BytesIO
            img = Image.open(BytesIO(resp.content)).convert("RGB")
            img.thumbnail((768, 768))
            img.save(cached_path, format="JPEG", quality=88)
            return cached_path
        except Exception as exc:
            logger.debug("Image download failed for %s: %s", url, exc)
            return None

    def embed_texts(self, texts: List[str]) -> Optional[np.ndarray]:
        if not texts or not self._ensure_clip_loaded():
            return None
        with self._model_lock:
            try:
                vecs = list(self._text_encoder.embed(texts))
                arr = np.array(vecs, dtype=np.float32)
                norms = np.linalg.norm(arr, axis=1, keepdims=True) + 1e-9
                return arr / norms
            except Exception as exc:
                logger.debug("CLIP text embed error: %s", exc)
                return None

    def embed_images(self, image_paths: List[str]) -> Optional[np.ndarray]:
        valid = [p for p in image_paths if p and os.path.exists(p)]
        if not valid or not self._ensure_clip_loaded():
            return None
        with self._model_lock:
            try:
                vecs = list(self._vision_encoder.embed(valid))
                arr = np.array(vecs, dtype=np.float32)
                norms = np.linalg.norm(arr, axis=1, keepdims=True) + 1e-9
                return arr / norms
            except Exception as exc:
                logger.debug("CLIP image embed error: %s", exc)
                return None

    def run_batch_ocr(self, image_paths: List[str]) -> Dict[str, List[str]]:
        """Run fast local OCR across one or more local image files."""
        valid = [p for p in image_paths if p and os.path.exists(p)]
        if not valid:
            return {}

        if os.path.exists(MAC_OCR_BIN_PATH) and os.access(MAC_OCR_BIN_PATH, os.X_OK):
            out_all: Dict[str, List[str]] = {}
            # Process in chunks of 32 images so subprocess timeout stays crisp
            for i in range(0, len(valid), 32):
                chunk = valid[i : i + 32]
                try:
                    res = subprocess.run(
                        [MAC_OCR_BIN_PATH, *chunk],
                        capture_output=True,
                        text=True,
                        timeout=18,
                    )
                    if res.returncode == 0 and res.stdout.strip():
                        parsed = json.loads(res.stdout.strip())
                        if isinstance(parsed, dict):
                            for k, v in parsed.items():
                                if isinstance(v, list):
                                    out_all[k] = [str(x) for x in v]
                except Exception as exc:
                    logger.debug("Apple Vision OCR chunk error: %s", exc)
            return out_all

        out: Dict[str, List[str]] = {}
        try:
            import pytesseract
            for p in valid:
                txt = pytesseract.image_to_string(Image.open(p))
                lines = [ln.strip() for ln in txt.splitlines() if ln.strip()]
                out[p] = lines
        except Exception:
            pass
        return out

    # -----------------------------------------------------------------------
    # Per-Dimension Prototype Training
    # -----------------------------------------------------------------------

    def _score_seed_match(
        self,
        title: str,
        variant: ProductVariant,
        stop_tokens: set[str],
    ) -> float:
        blob = (title or "").lower()
        for neg in variant.parsed_list("negative_keywords"):
            if not _is_generic_cue(neg, stop_tokens) and _word_boundary_match(neg, blob):
                return -5.0

        score = 0.0
        for kw in variant.parsed_list("text_keywords"):
            if not _is_generic_cue(kw, stop_tokens) and _word_boundary_match(kw, blob):
                score += 2.5 if len(kw) >= 4 else 1.5
        for sig in variant.parsed_list("ocr_signatures"):
            if any(c.isdigit() for c in sig) and not _is_generic_cue(sig, stop_tokens) and _word_boundary_match(sig, blob):
                score += 2.8
        return score

    def train_search_variants(
        self,
        db: Session,
        tracked_search: TrackedSearch,
        fetch_live_samples: bool = True,
    ) -> Dict[str, Any]:
        """Train/calibrate local visual prototypes per `dimension` for `tracked_search`.

        - `visual` variants receive mean-centered contrastive CLIP image+text prototypes within their dimension.
        - `label` variants (e.g. Material: Wool / Cashmere / Cotton) rely on verified text & tag OCR cues rather than guessing fabric from distant photos.
        """
        t0 = time.perf_counter()
        variants: List[ProductVariant] = (
            db.query(ProductVariant)
            .filter(ProductVariant.tracked_search_id == tracked_search.id)
            .order_by(ProductVariant.id)
            .all()
        )
        if not variants:
            return {"ok": False, "detail": "No variants exist for this search yet."}

        stop_tokens = _build_stop_tokens(tracked_search)
        clip_ok = self._ensure_clip_loaded()

        stored_items = (
            db.query(SoldItem)
            .filter(
                SoldItem.tracked_search_id == tracked_search.id,
                SoldItem.is_replica.isnot(True),
                SoldItem.photo_url.isnot(None),
            )
            .limit(800)
            .all()
        )

        # Group visual variants by dimension so orthogonal tags (e.g. Cable-Knit + Wool) don't block seed harvesting
        dims_visual: Dict[str, List[ProductVariant]] = {}
        for v in variants:
            if (v.modality or "visual") == "visual":
                dim_key = (v.dimension or "Style").strip() or "Style"
                dims_visual.setdefault(dim_key, []).append(v)
            else:
                # Clear visual prototype on label-only variants so they never hallucinate from photos
                v.prototype_embedding = None
                v.training_sample_count = 0

        seed_urls_by_vid: Dict[int, List[str]] = {v.id: [] for v in variants}
        for dim_key, dim_vars in dims_visual.items():
            for item in stored_items:
                if not item.photo_url:
                    continue
                scores = [(v.id, self._score_seed_match(item.title or "", v, stop_tokens)) for v in dim_vars]
                scores.sort(key=lambda x: x[1], reverse=True)
                if scores[0][1] >= 2.5 and (len(scores) == 1 or scores[0][1] - scores[1][1] >= 1.5):
                    bucket = seed_urls_by_vid[scores[0][0]]
                    if len(bucket) < 12 and item.photo_url not in bucket:
                        bucket.append(item.photo_url)

        # Top up sparse visual variants with targeted Vinted page-1 reference queries if allowed
        if fetch_live_samples:
            from ..scraper import _extract_item, _search_vinted
            for dim_key, dim_vars in dims_visual.items():
                for v in dim_vars:
                    if len(seed_urls_by_vid[v.id]) >= 6:
                        continue
                    kws = [k for k in v.parsed_list("text_keywords") if not _is_generic_cue(k, stop_tokens)]
                    specific_kw = kws[0] if kws else v.variant_name
                    query_str = f"{tracked_search.brand or tracked_search.search_text} {specific_kw}".strip()
                    try:
                        raw_hits = _search_vinted(query_str, page=1, per_page=14)
                        for raw in (raw_hits or [])[:12]:
                            extracted = _extract_item(raw)
                            if not extracted or not extracted.get("photo_url"):
                                continue
                            if self._score_seed_match(extracted.get("title", ""), v, stop_tokens) >= 0.0:
                                p_url = extracted["photo_url"]
                                if p_url not in seed_urls_by_vid[v.id]:
                                    seed_urls_by_vid[v.id].append(p_url)
                                if len(seed_urls_by_vid[v.id]) >= 8:
                                    break
                    except Exception as exc:
                        logger.debug("Live seed harvest skipped for '%s': %s", query_str, exc)

        raw_centroids: Dict[int, np.ndarray] = {}
        sample_counts: Dict[int, int] = {v.id: 0 for v in variants}

        for dim_key, dim_vars in dims_visual.items():
            for v in dim_vars:
                cues = v.parsed_list("visual_cues") or [f"{tracked_search.search_text} {v.variant_name}"]
                text_prompts = [f"a Vinted listing photo of {c}" for c in cues] + [
                    f"{v.variant_name}: {v.short_description or ''}"
                ]
                text_mat = self.embed_texts(text_prompts) if clip_ok else None
                text_vec = _l2_normalize(np.mean(text_mat, axis=0)) if text_mat is not None else None

                local_paths: List[str] = []
                for u in seed_urls_by_vid.get(v.id, [])[:10]:
                    p = self._download_image_cached(u)
                    if p:
                        local_paths.append(p)

                img_mat = self.embed_images(local_paths) if (clip_ok and local_paths) else None
                sample_counts[v.id] = int(img_mat.shape[0]) if img_mat is not None else 0

                if img_mat is not None and img_mat.shape[0] > 0:
                    mean_img = _l2_normalize(np.mean(img_mat, axis=0))
                    if img_mat.shape[0] >= 4:
                        sims = img_mat @ mean_img
                        keep = img_mat[sims >= np.percentile(sims, 20)]
                        if keep.shape[0] > 0:
                            mean_img = _l2_normalize(np.mean(keep, axis=0))
                    if text_vec is not None:
                        fused = _l2_normalize(0.68 * mean_img + 0.32 * text_vec)
                    else:
                        fused = mean_img
                    raw_centroids[v.id] = fused
                elif text_vec is not None:
                    raw_centroids[v.id] = text_vec

        # Compute per-dimension mean & contrastive prototypes
        if raw_centroids:
            global_visual_mean = _l2_normalize(np.mean(list(raw_centroids.values()), axis=0))
            for dim_key, dim_vars in dims_visual.items():
                dim_vecs = [raw_centroids[v.id] for v in dim_vars if v.id in raw_centroids]
                if not dim_vecs:
                    continue
                dim_mean = (
                    _l2_normalize(np.mean(dim_vecs, axis=0))
                    if len(dim_vecs) >= 2
                    else global_visual_mean
                )
                for v in dim_vars:
                    c_vec = raw_centroids.get(v.id)
                    if c_vec is None:
                        continue
                    contrast_vec = _l2_normalize(c_vec - 0.75 * dim_mean)
                    payload = {
                        "raw": [round(float(x), 6) for x in c_vec.tolist()],
                        "contrast": [round(float(x), 6) for x in contrast_vec.tolist()],
                        "family_mean": [round(float(x), 6) for x in dim_mean.tolist()],
                        "dimension": dim_key,
                    }
                    v.prototype_embedding = json.dumps(payload)
                    v.training_sample_count = sample_counts.get(v.id, 0)
                    v.updated_at = datetime.now(timezone.utc)

        cat_meta = {}
        if tracked_search.catalogue_summary:
            try:
                cat_meta = json.loads(tracked_search.catalogue_summary)
            except Exception:
                cat_meta = {}
        total_photos = sum(sample_counts.values())
        cat_meta.update({
            "local_model_trained": True,
            "vision_backbone": self.VISION_MODEL_NAME if clip_ok else "NeuralOCR+Text",
            "ocr_engine": "Apple Vision Neural OCR" if os.path.exists(MAC_OCR_BIN_PATH) else "Local OCR",
            "total_training_photos": total_photos,
            "trained_at": datetime.now(timezone.utc).isoformat(),
            "training_duration_ms": int((time.perf_counter() - t0) * 1000),
        })
        tracked_search.catalogue_summary = json.dumps(cat_meta)
        tracked_search.variants_enabled = True
        db.commit()

        return {
            "ok": True,
            "variants_trained": len(raw_centroids),
            "label_variants_ready": sum(1 for v in variants if (v.modality or "visual") == "label"),
            "total_reference_photos": total_photos,
            "per_variant_samples": {v.variant_name: sample_counts.get(v.id, 0) for v in variants},
            "duration_ms": cat_meta["training_duration_ms"],
        }

    # -----------------------------------------------------------------------
    # Faceted Multi-Category Item Classification
    # -----------------------------------------------------------------------

    @staticmethod
    def _extract_extra_photo_urls(item_data: Dict[str, Any], primary_url: Optional[str]) -> List[str]:
        urls: List[str] = []
        if primary_url:
            urls.append(primary_url)

        for key in ("photos", "raw_photo"):
            val = item_data.get(key)
            if isinstance(val, list):
                for entry in val[:3]:
                    u = entry.get("full_size_url") or entry.get("url") if isinstance(entry, dict) else (entry if isinstance(entry, str) else None)
                    if u and u not in urls:
                        urls.append(u)
            elif isinstance(val, dict):
                u = val.get("full_size_url") or val.get("url")
                if u and u not in urls:
                    urls.append(u)

        raw_item = item_data.get("raw_item")
        if isinstance(raw_item, dict) and isinstance(raw_item.get("photos"), list):
            for entry in raw_item["photos"][:3]:
                if isinstance(entry, dict):
                    u = entry.get("full_size_url") or entry.get("url")
                    if u and u not in urls:
                        urls.append(u)

        return urls[:2]

    def classify_item_variant(
        self,
        db: Session,
        tracked_search: TrackedSearch,
        item_data: Dict[str, Any],
        variants: Optional[List[ProductVariant]] = None,
        allow_vision: bool = True,
        precomputed_img_vec: Optional[np.ndarray] = None,
        precomputed_ocr_text: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Classify a single Vinted listing across all orthogonal facet `dimension`s.

        An item can receive multiple tags simultaneously (e.g. `Cable-Knit` in `Style` + `Wool / Merino` in `Material`).
        """
        if not getattr(tracked_search, "variants_enabled", False):
            return None

        active_variants = variants if variants is not None else (
            db.query(ProductVariant)
            .filter(ProductVariant.tracked_search_id == tracked_search.id)
            .order_by(ProductVariant.id)
            .all()
        )
        if not active_variants:
            return None

        t0 = time.perf_counter()
        stop_tokens = _build_stop_tokens(tracked_search)

        title = str(item_data.get("title") or "").strip()
        desc = str(item_data.get("description") or "").strip()
        photo_url = item_data.get("photo_url")
        text_blob = f"{title} {desc}".lower()

        # 1. Text Keyword & Negative Disqualifier Scoring
        text_scores: Dict[int, float] = {v.id: 0.0 for v in active_variants}
        text_hits: Dict[int, List[str]] = {v.id: [] for v in active_variants}
        disqualified: set[int] = set()

        for v in active_variants:
            for neg in v.parsed_list("negative_keywords"):
                if not _is_generic_cue(neg, stop_tokens) and _word_boundary_match(neg, text_blob):
                    text_scores[v.id] -= 5.0
                    disqualified.add(v.id)
            for kw in v.parsed_list("text_keywords"):
                if not _is_generic_cue(kw, stop_tokens) and _word_boundary_match(kw, text_blob):
                    weight = 3.2 if any(ch.isdigit() for ch in kw) or len(kw) >= 5 else 2.2
                    text_scores[v.id] += weight
                    text_hits[v.id].append(kw)
            # Only match alphanumeric model-code OCR signatures against text_blob (never brand strings!)
            for sig in v.parsed_list("ocr_signatures"):
                if any(ch.isdigit() for ch in sig) and not _is_generic_cue(sig, stop_tokens):
                    if _word_boundary_match(sig, text_blob) and sig.lower() not in [h.lower() for h in text_hits[v.id]]:
                        text_scores[v.id] += 2.8
                        text_hits[v.id].append(sig)

        # 2. Local Photo OCR + CLIP Image Embedding
        ocr_scores: Dict[int, float] = {v.id: 0.0 for v in active_variants}
        ocr_hits: Dict[int, List[str]] = {v.id: [] for v in active_variants}
        mean_item_vec: Optional[np.ndarray] = precomputed_img_vec
        ocr_lower = (precomputed_ocr_text or "").lower()

        if allow_vision and photo_url and (mean_item_vec is None and precomputed_ocr_text is None):
            photo_urls = self._extract_extra_photo_urls(item_data, photo_url)
            local_paths = [p for p in (self._download_image_cached(u) for u in photo_urls) if p]
            if local_paths:
                self._total_images_classified += len(local_paths)
                ocr_map = self.run_batch_ocr(local_paths)
                all_ocr_lines: List[str] = []
                for p in local_paths:
                    all_ocr_lines.extend(ocr_map.get(p, []))
                ocr_lower = " | ".join(all_ocr_lines).lower()

                has_any_visual_proto = any(
                    (v.modality or "visual") == "visual" and bool(v.prototype_embedding)
                    for v in active_variants
                )
                if has_any_visual_proto and self._ensure_clip_loaded():
                    img_vecs = self.embed_images(local_paths)
                    if img_vecs is not None and img_vecs.shape[0] > 0:
                        mean_item_vec = _l2_normalize(np.mean(img_vecs, axis=0))

        if ocr_lower:
            for v in active_variants:
                for sig in v.parsed_list("ocr_signatures"):
                    if not _is_generic_cue(sig, stop_tokens) and _word_boundary_match(sig, ocr_lower):
                        w = 4.2 if (any(c.isdigit() for c in sig) or len(sig) >= 4) else 2.5
                        ocr_scores[v.id] += w
                        ocr_hits[v.id].append(sig)
                for kw in v.parsed_list("text_keywords"):
                    if len(kw) >= 4 and not _is_generic_cue(kw, stop_tokens) and _word_boundary_match(kw, ocr_lower):
                        if kw not in [x.lower() for x in ocr_hits[v.id]]:
                            ocr_scores[v.id] += 2.4
                            ocr_hits[v.id].append(kw)
                for neg in v.parsed_list("negative_keywords"):
                    if len(neg) >= 4 and not _is_generic_cue(neg, stop_tokens) and _word_boundary_match(neg, ocr_lower):
                        ocr_scores[v.id] -= 4.0
                        disqualified.add(v.id)

        # 3. Evaluate Each Orthogonal Dimension Independently
        dims_ordered: Dict[str, List[ProductVariant]] = {}
        for v in active_variants:
            dim_key = (v.dimension or "Style").strip() or "Style"
            dims_ordered.setdefault(dim_key, []).append(v)

        selected_variants: List[ProductVariant] = []
        per_variant_conf: List[float] = []
        methods_used: set[str] = set()
        evidence_parts: List[str] = []

        for dim_key, dim_vars in dims_ordered.items():
            is_multi_label_dim = all((v.modality or "visual") == "label" for v in dim_vars)

            if is_multi_label_dim:
                # Multi-label dimension (e.g. Material: Wool + Cashmere blend can co-occur)
                matched_in_dim: List[Tuple[float, ProductVariant]] = []
                for v in dim_vars:
                    if v.id in disqualified:
                        continue
                    cue_sc = text_scores[v.id] + ocr_scores[v.id]
                    if cue_sc >= 2.0:
                        matched_in_dim.append((cue_sc, v))
                matched_in_dim.sort(key=lambda x: x[0], reverse=True)
                for cue_sc, v in matched_in_dim[:2]:
                    selected_variants.append(v)
                    per_variant_conf.append(min(0.98, 0.72 + 0.05 * cue_sc))
                    if ocr_hits[v.id]:
                        methods_used.add("ocr")
                        evidence_parts.append(f"Tag OCR '{', '.join(ocr_hits[v.id][:2])}'")
                    if text_hits[v.id]:
                        methods_used.add("text")
                        evidence_parts.append(f"Matched '{', '.join(text_hits[v.id][:2])}'")
            else:
                # Mutually-exclusive visual/structural dimension (e.g. Style, Edition, Cut, Series)
                # Precompute CLIP prototype scores for visual variants in this dimension if image available
                vis_metrics: Dict[int, Tuple[float, float, float]] = {}  # vid -> (combined, sim_contrast, sim_raw)
                visual_candidates = [
                    v for v in dim_vars
                    if (v.modality or "visual") == "visual" and bool(v.prototype_embedding)
                ]
                if mean_item_vec is not None and len(visual_candidates) >= 2:
                    for v in visual_candidates:
                        try:
                            pdata = json.loads(v.prototype_embedding)
                            d_mean = np.array(pdata["family_mean"], dtype=np.float32)
                            c_proto = np.array(pdata["contrast"], dtype=np.float32)
                            r_proto = np.array(pdata["raw"], dtype=np.float32)

                            item_contrast = _l2_normalize(mean_item_vec - 0.75 * d_mean)
                            sim_c = float(np.dot(item_contrast, c_proto))
                            sim_r = float(np.dot(mean_item_vec, r_proto))
                            comb = 0.72 * sim_c + 0.28 * sim_r
                            vis_metrics[v.id] = (comb, sim_c, sim_r)
                        except Exception:
                            pass

                # 3a. Check explicit text / OCR cues first
                cue_candidates: List[Tuple[float, ProductVariant]] = []
                for v in dim_vars:
                    if v.id in disqualified:
                        continue
                    cue_sc = text_scores[v.id] + ocr_scores[v.id]
                    if cue_sc >= 2.0:
                        tie_break = vis_metrics[v.id][0] if v.id in vis_metrics else 0.0
                        cue_candidates.append((cue_sc + 0.4 * tie_break, v))

                if cue_candidates:
                    cue_candidates.sort(key=lambda x: x[0], reverse=True)
                    winner = cue_candidates[0][1]
                    selected_variants.append(winner)
                    per_variant_conf.append(min(0.99, 0.78 + 0.04 * cue_candidates[0][0]))
                    if ocr_hits[winner.id]:
                        methods_used.add("ocr")
                        evidence_parts.append(f"Photo OCR '{', '.join(ocr_hits[winner.id][:2])}'")
                    if text_hits[winner.id]:
                        methods_used.add("text")
                        evidence_parts.append(f"Matched '{', '.join(text_hits[winner.id][:2])}'")
                    if winner.id in vis_metrics and vis_metrics[winner.id][1] > 0.10:
                        methods_used.add("vision")
                elif vis_metrics and len(vis_metrics) >= 2:
                    # 3b. Pure Contrastive Vision fallback — strict margin prevents smooth jumpers entering Cable-Knit!
                    ranked_vis = sorted(
                        [v for v in visual_candidates if v.id in vis_metrics],
                        key=lambda v: vis_metrics[v.id][0],
                        reverse=True,
                    )
                    best_v = ranked_vis[0]
                    second_v = ranked_vis[1]
                    best_comb, best_sim_c, best_sim_r = vis_metrics[best_v.id]
                    _, sec_sim_c, _ = vis_metrics[second_v.id]
                    contrast_gap = best_sim_c - sec_sim_c

                    if (
                        best_v.id not in disqualified
                        and best_sim_c >= 0.18
                        and contrast_gap >= 0.28
                        and best_sim_r >= 0.21
                    ):
                        selected_variants.append(best_v)
                        vis_conf = min(0.94, max(0.58, 0.55 + 0.45 * min(1.0, contrast_gap)))
                        per_variant_conf.append(vis_conf)
                        methods_used.add("vision")
                        evidence_parts.append(f"Photo texture/silhouette match ({int(round(vis_conf * 100))}%)")

        self._last_inference_ms = int((time.perf_counter() - t0) * 1000)

        if not selected_variants:
            return None

        primary = selected_variants[0]
        v_ids = [v.id for v in selected_variants]
        v_names = [v.variant_name for v in selected_variants]
        combined_conf = round(sum(per_variant_conf) / len(per_variant_conf), 2)
        best_tier = max(
            (v.resale_tier or "STANDARD" for v in selected_variants),
            key=lambda t: TIER_RANK.get(t, 2),
        )
        est_vals = [float(v.est_market_value_gbp) for v in selected_variants if v.est_market_value_gbp]
        est_market_val = round(max(est_vals), 2) if est_vals else None
        max_prices = [float(v.target_max_price) for v in selected_variants if v.target_max_price is not None]
        target_max_price = min(max_prices) if max_prices else None

        ordered_methods = [m for m in ("ocr", "vision", "text") if m in methods_used]
        method_tag = "+".join(ordered_methods) if ordered_methods else "text"

        return {
            "variant_id": primary.id,
            "variant_ids": v_ids,
            "variant_slug": primary.variant_slug,
            "variant_name": " · ".join(v_names),
            "variant_names": v_names,
            "resale_tier": best_tier,
            "confidence": combined_conf,
            "method": method_tag,
            "evidence": " • ".join(dict.fromkeys(evidence_parts)),
            "est_market_value_gbp": est_market_val,
            "target_max_price": target_max_price,
            "is_sniping_enabled": all(bool(v.is_sniping_enabled) for v in selected_variants),
        }

    def record_manual_feedback(
        self,
        db: Session,
        item: SoldItem,
        target_variants: Optional[Union[ProductVariant, Sequence[ProductVariant]]],
    ) -> None:
        """Assign manual multi-category variant(s) to `item` and incrementally update visual prototypes."""
        if target_variants is None:
            v_list: List[ProductVariant] = []
        elif isinstance(target_variants, ProductVariant):
            v_list = [target_variants]
        else:
            v_list = [v for v in target_variants if v is not None]

        if not v_list:
            item.variant_id = None
            item.variant_ids_json = None
            item.variant_name = None
            item.variant_names_json = None
            item.variant_confidence = 0.0
            item.variant_method = "manual"
            item.variant_evidence = "Cleared manually"
            db.commit()
            return

        v_ids = [v.id for v in v_list]
        v_names = [v.variant_name for v in v_list]
        item.variant_id = v_ids[0]
        item.variant_ids_json = json.dumps(v_ids, separators=(",", ":"))
        item.variant_name = " · ".join(v_names)
        item.variant_names_json = json.dumps(v_names)
        item.variant_confidence = 1.0
        item.variant_method = "manual"
        item.variant_evidence = "Verified manually"

        # Online learning: blend item's photo vector into any selected visual variant's prototype within its dimension
        visual_targets = [v for v in v_list if (v.modality or "visual") == "visual"]
        if visual_targets and item.photo_url and self._ensure_clip_loaded():
            local_p = self._download_image_cached(item.photo_url)
            if local_p:
                vecs = self.embed_images([local_p])
                if vecs is not None and vecs.shape[0] > 0:
                    x_vec = vecs[0]
                    all_siblings = (
                        db.query(ProductVariant)
                        .filter(ProductVariant.tracked_search_id == item.tracked_search_id)
                        .all()
                    )
                    for tv in visual_targets:
                        dim_key = (tv.dimension or "Style").strip() or "Style"
                        dim_siblings = [
                            sv for sv in all_siblings
                            if (sv.modality or "visual") == "visual"
                            and ((sv.dimension or "Style").strip() or "Style") == dim_key
                        ]
                        raw_map: Dict[int, np.ndarray] = {}
                        for sv in dim_siblings:
                            if sv.prototype_embedding:
                                try:
                                    raw_map[sv.id] = np.array(json.loads(sv.prototype_embedding)["raw"], dtype=np.float32)
                                except Exception:
                                    pass
                        if tv.id in raw_map:
                            raw_map[tv.id] = _l2_normalize(0.82 * raw_map[tv.id] + 0.18 * x_vec)
                        else:
                            raw_map[tv.id] = x_vec
                        tv.training_sample_count = int(tv.training_sample_count or 0) + 1

                        if raw_map:
                            d_mean = _l2_normalize(np.mean(list(raw_map.values()), axis=0))
                            for sv in dim_siblings:
                                if sv.id in raw_map:
                                    c_vec = raw_map[sv.id]
                                    contrast_vec = _l2_normalize(c_vec - 0.75 * d_mean)
                                    sv.prototype_embedding = json.dumps({
                                        "raw": [round(float(x), 6) for x in c_vec.tolist()],
                                        "contrast": [round(float(x), 6) for x in contrast_vec.tolist()],
                                        "family_mean": [round(float(x), 6) for x in d_mean.tolist()],
                                        "dimension": dim_key,
                                    })

        db.commit()


variant_vision_service = LocalVariantVisionService()
