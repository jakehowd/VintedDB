"""Local Multimodal Vision AI & Self-Training Product Variant Classifier.

Runs 100% locally on Apple Silicon / CPU with zero per-item API cost:
1. Local ViT-B/32 Vision + Text Embeddings via ONNX Runtime (`fastembed`)
2. Native Apple M2 Neural Engine Image OCR (`mac_vision_ocr` Swift Vision helper, `pytesseract` fallback)
3. Few-Shot Contrastive Prototype Learner (trains discriminative per-variant centroids from real Vinted images)
4. Online Incremental Feedback Learning when users manually verify/assign variants
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import math
import os
import platform
import re
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
import numpy as np
from PIL import Image
from sqlalchemy.orm import Session

from ..config import MAC_OCR_BIN_PATH, VISION_CACHE_DIR, VISION_MODELS_DIR
from ..models import ProductVariant, SoldItem, TrackedSearch

logger = logging.getLogger(__name__)


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


class LocalVariantVisionService:
    """Singleton managing local ONNX CLIP encoders, Apple Neural OCR, prototype training, and item classification."""

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
                logger.info("✅ Local ONNX CLIP Vision & Text encoders ready!")
                return True
            except Exception as exc:
                self._clip_load_failed = True
                self._clip_error_msg = str(exc)
                logger.warning("Local ONNX CLIP failed to load (%s); using OpenCV + Neural OCR fallback.", exc)
                return False

    def get_engine_status(self) -> Dict[str, Any]:
        mac_ocr_ready = os.path.exists(MAC_OCR_BIN_PATH) and os.access(MAC_OCR_BIN_PATH, os.X_OK)
        clip_cached = any(Path(VISION_MODELS_DIR).glob("*clip*")) if os.path.exists(VISION_MODELS_DIR) else False
        return {
            "vision_backbone": self.VISION_MODEL_NAME,
            "clip_loaded_in_ram": self._vision_encoder is not None,
            "clip_weights_cached": clip_cached or (self._vision_encoder is not None),
            "ocr_engine": "Apple M2 Vision Neural Engine" if mac_ocr_ready else "Tesseract Local OCR",
            "mac_neural_ocr_ready": mac_ocr_ready,
            "total_images_classified": self._total_images_classified,
            "last_inference_ms": self._last_inference_ms,
            "error": self._clip_error_msg or None,
        }

    # -----------------------------------------------------------------------
    # Image Caching, Embeddings & Neural OCR
    # -----------------------------------------------------------------------

    def _download_image_cached(self, url: str) -> Optional[str]:
        if not url or not url.startswith(("http://", "https://")):
            return None
        key = hashlib.sha1(url.encode("utf-8")).hexdigest()
        cached_path = os.path.join(VISION_CACHE_DIR, f"{key}.jpg")
        if os.path.exists(cached_path) and os.path.getsize(cached_path) > 256:
            return cached_path

        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
                "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            }
            resp = httpx.get(url, headers=headers, timeout=8.0, follow_redirects=True)
            if resp.status_code != 200 or len(resp.content) < 256:
                return None

            # Normalize to clean RGB JPEG (handles WebP/AVIF seamlessly for both CLIP & Vision OCR)
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
            try:
                res = subprocess.run(
                    [MAC_OCR_BIN_PATH, *valid],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if res.returncode == 0 and res.stdout.strip():
                    parsed = json.loads(res.stdout.strip())
                    if isinstance(parsed, dict):
                        return {k: [str(x) for x in v] for k, v in parsed.items() if isinstance(v, list)}
            except Exception as exc:
                logger.debug("Apple Vision OCR error: %s", exc)

        # Fallback: pytesseract
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
    # Prototype Training ("Trains Small Local Classifier Per Variant")
    # -----------------------------------------------------------------------

    def _score_text_only_strict(
        self,
        title: str,
        description: str,
        variant: ProductVariant,
    ) -> float:
        blob = f"{title or ''} {description or ''}".lower()
        negatives = variant.parsed_list("negative_keywords")
        for neg in negatives:
            if _word_boundary_match(neg, blob):
                return -5.0

        score = 0.0
        for kw in variant.parsed_list("text_keywords"):
            if _word_boundary_match(kw, blob):
                score += 2.5 if len(kw) >= 4 else 1.5
        for sig in variant.parsed_list("ocr_signatures"):
            if _word_boundary_match(sig, blob):
                score += 3.0
        return score

    def train_search_variants(
        self,
        db: Session,
        tracked_search: TrackedSearch,
        fetch_live_samples: bool = True,
    ) -> Dict[str, Any]:
        """Train/calibrate local visual prototypes for every variant of `tracked_search`.

        1. Encodes each variant's visual cues into 512-D CLIP text space.
        2. Harvests high-confidence reference photos from existing DB items + targeted Vinted UK queries.
        3. Extracts 512-D CLIP image vectors & fuses them into mean-centered contrastive prototypes.
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

        clip_ok = self._ensure_clip_loaded()
        stored_items = (
            db.query(SoldItem)
            .filter(
                SoldItem.tracked_search_id == tracked_search.id,
                SoldItem.is_replica.isnot(True),
                SoldItem.photo_url.isnot(None),
            )
            .limit(400)
            .all()
        )

        # Bucket existing DB items that unambiguously match a single variant by title
        seed_urls_by_vid: Dict[int, List[str]] = {v.id: [] for v in variants}
        for item in stored_items:
            if not item.photo_url:
                continue
            scores = [(v.id, self._score_text_only_strict(item.title or "", "", v)) for v in variants]
            scores.sort(key=lambda x: x[1], reverse=True)
            if scores[0][1] >= 2.5 and (len(scores) == 1 or scores[0][1] - scores[1][1] >= 2.0):
                bucket = seed_urls_by_vid[scores[0][0]]
                if len(bucket) < 10 and item.photo_url not in bucket:
                    bucket.append(item.photo_url)

        # Optionally top up sparse variants with rapid targeted Vinted page-1 reference queries
        if fetch_live_samples:
            from ..scraper import _extract_item, _search_vinted
            for v in variants:
                if len(seed_urls_by_vid[v.id]) >= 5:
                    continue
                kws = v.parsed_list("text_keywords")
                specific_kw = kws[0] if kws else v.variant_name
                query_str = f"{tracked_search.brand or tracked_search.search_text} {specific_kw}".strip()
                try:
                    raw_hits = _search_vinted(query_str, page=1, per_page=14)
                    for raw in (raw_hits or [])[:12]:
                        extracted = _extract_item(raw)
                        if not extracted or not extracted.get("photo_url"):
                            continue
                        if self._score_text_only_strict(extracted.get("title", ""), "", v) >= 0.0:
                            p_url = extracted["photo_url"]
                            if p_url not in seed_urls_by_vid[v.id]:
                                seed_urls_by_vid[v.id].append(p_url)
                            if len(seed_urls_by_vid[v.id]) >= 8:
                                break
                except Exception as exc:
                    logger.debug("Live seed harvest skipped for '%s': %s", query_str, exc)

        raw_centroids: Dict[int, np.ndarray] = {}
        sample_counts: Dict[int, int] = {}

        for v in variants:
            cues = v.parsed_list("visual_cues") or [f"{tracked_search.search_text} {v.variant_name}"]
            text_prompts = [f"a Vinted listing photo of {c}" for c in cues] + [
                f"{v.variant_name}: {v.short_description or ''}"
            ]
            text_mat = self.embed_texts(text_prompts) if clip_ok else None
            text_vec = _l2_normalize(np.mean(text_mat, axis=0)) if text_mat is not None else None

            local_paths: List[str] = []
            for u in seed_urls_by_vid.get(v.id, [])[:8]:
                p = self._download_image_cached(u)
                if p:
                    local_paths.append(p)

            img_mat = self.embed_images(local_paths) if (clip_ok and local_paths) else None
            sample_counts[v.id] = int(img_mat.shape[0]) if img_mat is not None else 0

            if img_mat is not None and img_mat.shape[0] > 0:
                mean_img = _l2_normalize(np.mean(img_mat, axis=0))
                # Outlier rejection if >= 4 samples
                if img_mat.shape[0] >= 4:
                    sims = img_mat @ mean_img
                    keep = img_mat[sims >= np.percentile(sims, 20)]
                    if keep.shape[0] > 0:
                        mean_img = _l2_normalize(np.mean(keep, axis=0))
                if text_vec is not None:
                    fused = _l2_normalize(0.70 * mean_img + 0.30 * text_vec)
                else:
                    fused = mean_img
                raw_centroids[v.id] = fused
            elif text_vec is not None:
                raw_centroids[v.id] = text_vec

        # Compute family mean vector & contrastive prototypes
        if raw_centroids:
            family_mean = _l2_normalize(np.mean(list(raw_centroids.values()), axis=0))
            for v in variants:
                c_vec = raw_centroids.get(v.id)
                if c_vec is None:
                    continue
                contrast_vec = _l2_normalize(c_vec - 0.55 * family_mean)
                payload = {
                    "raw": [round(float(x), 6) for x in c_vec.tolist()],
                    "contrast": [round(float(x), 6) for x in contrast_vec.tolist()],
                    "family_mean": [round(float(x), 6) for x in family_mean.tolist()],
                }
                v.prototype_embedding = json.dumps(payload)
                v.training_sample_count = sample_counts.get(v.id, 0)
                v.updated_at = datetime.now(timezone.utc)

        # Update catalogue summary metadata on TrackedSearch
        cat_meta = {}
        if tracked_search.catalogue_summary:
            try:
                cat_meta = json.loads(tracked_search.catalogue_summary)
            except Exception:
                cat_meta = {}
        total_photos = sum(sample_counts.values())
        cat_meta.update({
            "local_model_trained": bool(raw_centroids),
            "vision_backbone": self.VISION_MODEL_NAME if clip_ok else "OpenCV+NeuralOCR",
            "ocr_engine": "Apple M2 Neural Engine" if os.path.exists(MAC_OCR_BIN_PATH) else "Tesseract",
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
            "total_reference_photos": total_photos,
            "per_variant_samples": {v.variant_name: sample_counts.get(v.id, 0) for v in variants},
            "duration_ms": cat_meta["training_duration_ms"],
        }

    # -----------------------------------------------------------------------
    # Multimodal Item Classification (Vision CLIP + Neural OCR + Text Cues)
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

        return urls[:2]  # Inspect up to 2 photos per listing for fast throughput

    def classify_item_variant(
        self,
        db: Session,
        tracked_search: TrackedSearch,
        item_data: Dict[str, Any],
        variants: Optional[List[ProductVariant]] = None,
        allow_vision: bool = True,
    ) -> Optional[Dict[str, Any]]:
        """Classify a single Vinted listing into its exact ProductVariant using Photos + OCR + Text."""
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
        title = str(item_data.get("title") or "").strip()
        desc = str(item_data.get("description") or "").strip()
        photo_url = item_data.get("photo_url")
        text_blob = f"{title} {desc}".lower()

        # 1. Text Keyword & Negative Disqualifier Signal
        text_scores: Dict[int, float] = {v.id: 0.0 for v in active_variants}
        text_hits: Dict[int, List[str]] = {v.id: [] for v in active_variants}
        disqualified: set[int] = set()

        for v in active_variants:
            for neg in v.parsed_list("negative_keywords"):
                if _word_boundary_match(neg, text_blob):
                    text_scores[v.id] -= 4.5
                    disqualified.add(v.id)
            for kw in v.parsed_list("text_keywords"):
                if _word_boundary_match(kw, text_blob):
                    weight = 3.2 if any(ch.isdigit() for ch in kw) or len(kw) >= 6 else 2.0
                    text_scores[v.id] += weight
                    text_hits[v.id].append(kw)
            for sig in v.parsed_list("ocr_signatures"):
                if _word_boundary_match(sig, text_blob) and sig.lower() not in text_hits[v.id]:
                    text_scores[v.id] += 2.6
                    text_hits[v.id].append(sig)

        # 2. Local Image Download -> Apple M2 Neural OCR + CLIP Prototype Vision Scoring
        ocr_scores: Dict[int, float] = {v.id: 0.0 for v in active_variants}
        ocr_hits: Dict[int, List[str]] = {v.id: [] for v in active_variants}
        vision_probs: Dict[int, float] = {v.id: 1.0 / len(active_variants) for v in active_variants}
        has_visual_signal = False
        recognized_ocr_text = ""

        if allow_vision and photo_url:
            photo_urls = self._extract_extra_photo_urls(item_data, photo_url)
            local_paths = [p for p in (self._download_image_cached(u) for u in photo_urls) if p]

            if local_paths:
                self._total_images_classified += len(local_paths)

                # 2a. Apple M2 Neural Engine OCR on listing photo(s)
                ocr_map = self.run_batch_ocr(local_paths)
                all_ocr_lines: List[str] = []
                for p in local_paths:
                    all_ocr_lines.extend(ocr_map.get(p, []))
                recognized_ocr_text = " | ".join(all_ocr_lines)
                ocr_lower = recognized_ocr_text.lower()

                if ocr_lower:
                    for v in active_variants:
                        for sig in v.parsed_list("ocr_signatures"):
                            if _word_boundary_match(sig, ocr_lower):
                                w = 4.2 if (any(c.isdigit() for c in sig) or len(sig) >= 4) else 2.5
                                ocr_scores[v.id] += w
                                ocr_hits[v.id].append(sig)
                        for kw in v.parsed_list("text_keywords"):
                            if len(kw) >= 3 and _word_boundary_match(kw, ocr_lower) and kw not in [x.lower() for x in ocr_hits[v.id]]:
                                ocr_scores[v.id] += 2.4
                                ocr_hits[v.id].append(kw)
                        for neg in v.parsed_list("negative_keywords"):
                            if len(neg) >= 3 and _word_boundary_match(neg, ocr_lower):
                                ocr_scores[v.id] -= 3.5

                # 2b. CLIP Vision Prototype Cosine Matching
                protos_ready = all(bool(v.prototype_embedding) for v in active_variants)
                if protos_ready and self._ensure_clip_loaded():
                    img_vecs = self.embed_images(local_paths)
                    if img_vecs is not None and img_vecs.shape[0] > 0:
                        mean_item_vec = _l2_normalize(np.mean(img_vecs, axis=0))
                        logits: List[float] = []
                        v_order: List[int] = []
                        for v in active_variants:
                            try:
                                pdata = json.loads(v.prototype_embedding)
                                f_mean = np.array(pdata["family_mean"], dtype=np.float32)
                                c_proto = np.array(pdata["contrast"], dtype=np.float32)
                                r_proto = np.array(pdata["raw"], dtype=np.float32)

                                item_contrast = _l2_normalize(mean_item_vec - 0.55 * f_mean)
                                sim_contrast = float(np.dot(item_contrast, c_proto))
                                sim_raw = float(np.dot(mean_item_vec, r_proto))
                                combined_sim = 0.65 * sim_contrast + 0.35 * sim_raw
                                logits.append(combined_sim * 14.0)  # Temperature scaling τ ≈ 1/14
                                v_order.append(v.id)
                            except Exception:
                                pass

                        if len(logits) == len(active_variants):
                            max_l = max(logits)
                            exps = [math.exp(l - max_l) for l in logits]
                            sum_e = sum(exps) or 1.0
                            for vid, e_val in zip(v_order, exps):
                                vision_probs[vid] = e_val / sum_e
                            has_visual_signal = True

        # 3. Multimodal Fusion
        combined_logits: Dict[int, float] = {}
        for v in active_variants:
            vis_logit = math.log(max(vision_probs[v.id], 1e-4)) * (1.8 if has_visual_signal else 0.0)
            combined_logits[v.id] = text_scores[v.id] + ocr_scores[v.id] + vis_logit

        max_c = max(combined_logits.values())
        exp_scores = {vid: math.exp(sc - max_c) for vid, sc in combined_logits.items()}
        total_exp = sum(exp_scores.values()) or 1.0
        final_probs = {vid: exp_scores[vid] / total_exp for vid in combined_logits}

        ranked = sorted(active_variants, key=lambda v: final_probs[v.id], reverse=True)
        winner = ranked[0]
        win_prob = final_probs[winner.id]
        runner_up_prob = final_probs[ranked[1].id] if len(ranked) > 1 else 0.0
        margin = win_prob - runner_up_prob

        self._last_inference_ms = int((time.perf_counter() - t0) * 1000)

        # Require meaningful positive evidence or strong visual prototype margin
        has_ocr = len(ocr_hits[winner.id]) > 0
        has_txt = len(text_hits[winner.id]) > 0
        if not has_ocr and not has_txt and (not has_visual_signal or margin < 0.08 or winner.id in disqualified):
            return None

        # Calibrate human-readable confidence & evidence string
        confidence = round(min(0.99, max(0.42, win_prob)), 2)
        evidence_parts: List[str] = []
        methods: List[str] = []

        if has_ocr:
            methods.append("ocr")
            evidence_parts.append(f"Photo OCR read '{', '.join(ocr_hits[winner.id][:3])}'")
        if has_visual_signal:
            methods.append("vision")
            vis_pct = int(round(vision_probs[winner.id] * 100))
            evidence_parts.append(f"Local ViT Vision match ({vis_pct}%)")
        if has_txt:
            methods.append("text")
            evidence_parts.append(f"Listing cues '{', '.join(text_hits[winner.id][:3])}'")

        method_tag = "+".join(methods) if methods else "vision"
        evidence_str = " • ".join(evidence_parts)

        return {
            "variant_id": winner.id,
            "variant_slug": winner.variant_slug,
            "variant_name": winner.variant_name,
            "resale_tier": winner.resale_tier,
            "confidence": confidence,
            "method": method_tag,
            "evidence": evidence_str,
            "est_market_value_gbp": winner.est_market_value_gbp,
            "target_max_price": winner.target_max_price,
            "is_sniping_enabled": bool(winner.is_sniping_enabled),
        }

    def record_manual_feedback(self, db: Session, item: SoldItem, target_variant: Optional[ProductVariant]) -> None:
        """Assign manual variant to `item` and incrementally update local visual prototype weights."""
        if target_variant is None:
            item.variant_id = None
            item.variant_name = None
            item.variant_confidence = 0.0
            item.variant_method = "manual"
            item.variant_evidence = "Cleared manually"
            db.commit()
            return

        item.variant_id = target_variant.id
        item.variant_name = target_variant.variant_name
        item.variant_confidence = 1.0
        item.variant_method = "manual"
        item.variant_evidence = "Verified by user (online prototype updated)"

        # Online learning: blend this item's CLIP photo vector into target_variant's centroid
        if item.photo_url and self._ensure_clip_loaded():
            local_p = self._download_image_cached(item.photo_url)
            if local_p:
                vecs = self.embed_images([local_p])
                if vecs is not None and vecs.shape[0] > 0:
                    x_vec = vecs[0]
                    siblings = (
                        db.query(ProductVariant)
                        .filter(ProductVariant.tracked_search_id == item.tracked_search_id)
                        .all()
                    )
                    raw_map: Dict[int, np.ndarray] = {}
                    for sv in siblings:
                        if sv.prototype_embedding:
                            try:
                                raw_map[sv.id] = np.array(json.loads(sv.prototype_embedding)["raw"], dtype=np.float32)
                            except Exception:
                                pass
                    if target_variant.id in raw_map:
                        raw_map[target_variant.id] = _l2_normalize(0.82 * raw_map[target_variant.id] + 0.18 * x_vec)
                    else:
                        raw_map[target_variant.id] = x_vec
                    target_variant.training_sample_count = int(target_variant.training_sample_count or 0) + 1

                    if raw_map:
                        f_mean = _l2_normalize(np.mean(list(raw_map.values()), axis=0))
                        for sv in siblings:
                            if sv.id in raw_map:
                                c_vec = raw_map[sv.id]
                                contrast_vec = _l2_normalize(c_vec - 0.55 * f_mean)
                                sv.prototype_embedding = json.dumps({
                                    "raw": [round(float(x), 6) for x in c_vec.tolist()],
                                    "contrast": [round(float(x), 6) for x in contrast_vec.tolist()],
                                    "family_mean": [round(float(x), 6) for x in f_mean.tolist()],
                                })

        db.commit()


variant_vision_service = LocalVariantVisionService()

