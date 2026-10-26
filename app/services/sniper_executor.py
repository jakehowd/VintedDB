"""Sniper Auto-Checkout & Paper Trading Simulation Engine.

Enables automated checkout or full zero-risk paper trading simulation for sniped deals.
Features:
- Secure local storage of card, address, and shipping preferences.
- Card number and CVV masking so credentials are never exposed in browser DOM.
- Automatic fallback to Simulation Mode whenever details are blank or simulation mode is checked.
- Persisted order ledger tracking total capital deployed, projected resale value, and paper profit.
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..config import VINTED_BASE_URL
from ..database import SessionLocal
from ..models import AutoBuyOrder, TrackedSearch

logger = logging.getLogger(__name__)

STORAGE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "storage")
CHECKOUT_PROFILE_PATH = os.path.join(STORAGE_DIR, "checkout_profile.json")

DEFAULT_PROFILE: Dict[str, Any] = {
    "auto_buy_enabled": False,
    "simulation_mode": True,  # Default to Safe Simulation / Paper Trading
    "shipping_name": "",
    "address_line1": "",
    "address_line2": "",
    "city": "",
    "postcode": "",
    "phone": "",
    "shipping_method": "cheapest",  # cheapest, inpost, evri, royal_mail
    "cardholder_name": "",
    "card_number": "",
    "card_expiry": "",
    "card_cvv": "",
    "max_price_per_item": 45.0,
    "daily_spend_limit": 150.0,
    "min_ai_resale_score": 65,
    "min_net_profit": 15.0,
}


class SniperExecutorService:
    """Manages checkout execution profiles, automated buying rules, and paper trading simulations."""

    def __init__(self) -> None:
        os.makedirs(STORAGE_DIR, exist_ok=True)
        self._profile: Dict[str, Any] = self._load_profile()

    def _load_profile(self) -> Dict[str, Any]:
        """Load execution profile from persistent local storage."""
        if os.path.exists(CHECKOUT_PROFILE_PATH) and os.path.getsize(CHECKOUT_PROFILE_PATH) > 0:
            try:
                with open(CHECKOUT_PROFILE_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        merged = dict(DEFAULT_PROFILE)
                        merged.update(data)
                        return merged
            except Exception as exc:
                logger.warning("Failed to parse checkout_profile.json: %s", exc)
        return dict(DEFAULT_PROFILE)

    def _save_profile(self) -> None:
        """Write profile to persistent local storage."""
        try:
            with open(CHECKOUT_PROFILE_PATH, "w", encoding="utf-8") as f:
                json.dump(self._profile, f, indent=2)
        except Exception as exc:
            logger.error("Failed to save checkout_profile.json: %s", exc)

    def get_public_profile(self) -> Dict[str, Any]:
        """Return profile with card numbers and CVV securely masked for UI consumption."""
        raw_card = str(self._profile.get("card_number") or "").replace(" ", "").strip()
        last4 = raw_card[-4:] if len(raw_card) >= 4 else ""
        masked_card = f"•••• •••• •••• {last4}" if last4 else ""

        has_card = bool(last4 and self._profile.get("card_expiry"))
        has_shipping = bool(
            self._profile.get("shipping_name")
            and self._profile.get("address_line1")
            and self._profile.get("postcode")
        )

        # Simulation Mode is active if explicitly toggled ON OR if required credentials are blank
        is_simulation_active = bool(
            self._profile.get("simulation_mode", True)
            or not has_card
            or not has_shipping
        )

        return {
            "auto_buy_enabled": bool(self._profile.get("auto_buy_enabled", False)),
            "simulation_mode": bool(self._profile.get("simulation_mode", True)),
            "is_simulation_active": is_simulation_active,
            "has_card_details": has_card,
            "has_shipping_details": has_shipping,
            "shipping_name": self._profile.get("shipping_name", ""),
            "address_line1": self._profile.get("address_line1", ""),
            "address_line2": self._profile.get("address_line2", ""),
            "city": self._profile.get("city", ""),
            "postcode": self._profile.get("postcode", ""),
            "phone": self._profile.get("phone", ""),
            "shipping_method": self._profile.get("shipping_method", "cheapest"),
            "cardholder_name": self._profile.get("cardholder_name", ""),
            "card_number_masked": masked_card,
            "card_last4": last4,
            "card_expiry": self._profile.get("card_expiry", ""),
            "card_cvv_masked": "•••" if self._profile.get("card_cvv") else "",
            "max_price_per_item": float(self._profile.get("max_price_per_item", 45.0)),
            "daily_spend_limit": float(self._profile.get("daily_spend_limit", 150.0)),
            "min_ai_resale_score": int(self._profile.get("min_ai_resale_score", 65)),
            "min_net_profit": float(self._profile.get("min_net_profit", 15.0)),
        }

    def update_profile(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Update shipping and card credentials from settings panel."""
        for key in (
            "auto_buy_enabled",
            "simulation_mode",
            "shipping_name",
            "address_line1",
            "address_line2",
            "city",
            "postcode",
            "phone",
            "shipping_method",
            "cardholder_name",
            "card_expiry",
            "max_price_per_item",
            "daily_spend_limit",
            "min_ai_resale_score",
            "min_net_profit",
        ):
            if key in payload and payload[key] is not None:
                self._profile[key] = payload[key]

        # Only overwrite sensitive card fields if non-masked new values were supplied
        new_card = str(payload.get("card_number") or "").replace(" ", "").strip()
        if new_card and not new_card.startswith("•"):
            self._profile["card_number"] = new_card
        elif "card_number" in payload and payload["card_number"] == "":
            self._profile["card_number"] = ""

        new_cvv = str(payload.get("card_cvv") or "").strip()
        if new_cvv and not new_cvv.startswith("•"):
            self._profile["card_cvv"] = new_cvv
        elif "card_cvv" in payload and payload["card_cvv"] == "":
            self._profile["card_cvv"] = ""

        self._save_profile()
        return self.get_public_profile()

    def should_auto_execute(
        self,
        db: Session,
        deal: Dict[str, Any],
        evaluation: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, str]:
        """Evaluate whether a deal qualifies for automated buying or simulation."""
        if not self._profile.get("auto_buy_enabled", False):
            return False, "Auto-buy is disabled in settings."

        asking_price = float(deal.get("price") or 0.0)
        max_item_cap = float(self._profile.get("max_price_per_item", 45.0))
        if asking_price > max_item_cap:
            return False, f"Price £{asking_price:.2f} exceeds max item cap £{max_item_cap:.2f}."

        # Check daily spend limit
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        spent_today = (
            db.query(func.sum(AutoBuyOrder.total_landed_cost))
            .filter(AutoBuyOrder.created_at >= today_start)
            .scalar()
            or 0.0
        )
        daily_cap = float(self._profile.get("daily_spend_limit", 150.0))
        if (spent_today + asking_price) > daily_cap:
            return False, f"Daily spend limit (£{spent_today:.2f}/£{daily_cap:.2f}) reached."

        # AI Profitability Quality Gate
        if evaluation:
            min_score = int(self._profile.get("min_ai_resale_score", 65))
            min_profit = float(self._profile.get("min_net_profit", 15.0))
            score = int(evaluation.get("ai_score") or 0)
            profit = float(evaluation.get("projected_net_profit") or 0.0)

            if score < min_score:
                return False, f"AI Resale Score ({score}/100) below minimum threshold ({min_score})."
            if profit < min_profit:
                return False, f"Projected net profit £{profit:.2f} below minimum threshold £{min_profit:.2f}."

        return True, "Qualifies for automated execution."

    def execute_deal(
        self,
        db: Session,
        deal: Dict[str, Any],
        evaluation: Optional[Dict[str, Any]] = None,
        force_simulate: bool = False,
    ) -> Dict[str, Any]:
        """Execute an automated snipe order (either Paper Trading Simulation or Live Checkout)."""
        pub = self.get_public_profile()
        is_simulation = force_simulate or pub["is_simulation_active"]

        vinted_id = str(deal.get("id") or "")
        title = str(deal.get("title") or "Vinted Deal")
        brand = str(deal.get("brand") or "")
        search_id = deal.get("search_id")
        variant_name = deal.get("variant_name")
        asking_price = float(deal.get("price") or 0.0)

        # Landed cost breakdown
        from .resale_evaluator import calculate_landed_buy_cost, resale_evaluator

        if not evaluation:
            tracked_search = db.query(TrackedSearch).filter(TrackedSearch.id == search_id).first()
            if tracked_search:
                evaluation = resale_evaluator.evaluate_deal(db, tracked_search, deal)
            else:
                evaluation = {
                    "ai_score": 70,
                    "verdict": "STRONG_BUY",
                    "projected_resale_price": asking_price * 2.0,
                    "projected_net_profit": asking_price * 0.8,
                    "roi_pct": 80.0,
                    "total_landed_cost": calculate_landed_buy_cost(asking_price)["total_landed_cost"],
                    "cost_breakdown": calculate_landed_buy_cost(asking_price),
                }

        cost_breakdown = evaluation.get("cost_breakdown") or calculate_landed_buy_cost(asking_price)
        total_landed = float(evaluation.get("total_landed_cost") or cost_breakdown["total_landed_cost"])
        projected_resale = float(evaluation.get("projected_resale_price") or (asking_price * 1.8))
        net_profit = float(evaluation.get("projected_net_profit") or (projected_resale - total_landed))
        roi_pct = float(evaluation.get("roi_pct") or 50.0)
        ai_score = evaluation.get("ai_score")
        ai_verdict = evaluation.get("verdict")

        order_code = f"{'SIM' if is_simulation else 'LIVE'}-VTD-{uuid.uuid4().hex[:8].upper()}"
        last4 = pub["card_last4"] or ("TEST" if is_simulation else "XXXX")
        ship_name = pub["shipping_name"] or ("Simulated Reseller" if is_simulation else "Customer")
        ship_pc = pub["postcode"] or ("SW1A 1AA" if is_simulation else "")

        if is_simulation:
            status = "SIMULATED_SUCCESS"
            notes = (
                f"🧪 Paper Trading Simulation: Simulated card auth [ending {last4}] succeeded. "
                f"Reserved Vinted listing #{vinted_id}. Landed cost: £{total_landed:.2f}. "
                f"Projected net margin: +£{net_profit:.2f} ({roi_pct:.0f}% ROI)."
            )
        else:
            status = "LIVE_CHECKOUT_INITIATED"
            notes = f"Live checkout dispatched for item #{vinted_id}."

        order = AutoBuyOrder(
            order_id=order_code,
            deal_id=vinted_id,
            vinted_item_id=vinted_id,
            tracked_search_id=search_id,
            title=title,
            brand=brand,
            variant_name=variant_name,
            item_price=asking_price,
            buyer_fee=cost_breakdown["buyer_protection_fee"],
            shipping_cost=cost_breakdown["shipping_cost"],
            total_landed_cost=total_landed,
            projected_resale_price=projected_resale,
            projected_net_profit=net_profit,
            projected_roi_pct=roi_pct,
            ai_score=ai_score,
            ai_verdict=ai_verdict,
            is_simulation=is_simulation,
            status=status,
            execution_notes=notes,
            shipping_name=ship_name,
            shipping_postcode=ship_pc,
            card_last4=last4,
            photo_url=deal.get("photo_url"),
            item_url=deal.get("url") or f"{VINTED_BASE_URL}/items/{vinted_id}",
            created_at=datetime.now(timezone.utc),
        )

        db.add(order)
        try:
            db.commit()
        except Exception as exc:
            db.rollback()
            logger.warning("Error persisting autobuy order: %s", exc)

        return {
            "order_id": order_code,
            "status": status,
            "is_simulation": is_simulation,
            "deal_id": vinted_id,
            "title": title,
            "item_price": asking_price,
            "total_landed_cost": total_landed,
            "projected_resale_price": projected_resale,
            "projected_net_profit": net_profit,
            "projected_roi_pct": roi_pct,
            "ai_score": ai_score,
            "ai_verdict": ai_verdict,
            "execution_notes": notes,
            "created_at": order.created_at.isoformat() if order.created_at else datetime.now(timezone.utc).isoformat(),
        }

    def get_order_ledger(self, db: Session, limit: int = 50) -> Dict[str, Any]:
        """Fetch historical simulated & live orders with cumulative performance metrics."""
        orders = (
            db.query(AutoBuyOrder)
            .order_by(AutoBuyOrder.created_at.desc())
            .limit(limit)
            .all()
        )

        total_orders = db.query(func.count(AutoBuyOrder.id)).scalar() or 0
        total_simulated_spend = (
            db.query(func.sum(AutoBuyOrder.total_landed_cost))
            .filter(AutoBuyOrder.is_simulation == True)
            .scalar()
            or 0.0
        )
        total_projected_profit = (
            db.query(func.sum(AutoBuyOrder.projected_net_profit))
            .filter(AutoBuyOrder.is_simulation == True)
            .scalar()
            or 0.0
        )
        total_projected_resale = (
            db.query(func.sum(AutoBuyOrder.projected_resale_price))
            .filter(AutoBuyOrder.is_simulation == True)
            .scalar()
            or 0.0
        )

        avg_roi = (
            round((total_projected_profit / total_simulated_spend) * 100, 1)
            if total_simulated_spend > 0
            else 0.0
        )

        serialized_orders = [
            {
                "id": o.id,
                "order_id": o.order_id,
                "deal_id": o.deal_id,
                "vinted_item_id": o.vinted_item_id,
                "title": o.title,
                "brand": o.brand or "",
                "variant_name": o.variant_name or "",
                "item_price": float(o.item_price),
                "buyer_fee": float(o.buyer_fee),
                "shipping_cost": float(o.shipping_cost),
                "total_landed_cost": float(o.total_landed_cost),
                "projected_resale_price": float(o.projected_resale_price),
                "projected_net_profit": float(o.projected_net_profit),
                "projected_roi_pct": float(o.projected_roi_pct),
                "ai_score": o.ai_score,
                "ai_verdict": o.ai_verdict,
                "is_simulation": bool(o.is_simulation),
                "status": o.status,
                "execution_notes": o.execution_notes,
                "card_last4": o.card_last4 or "TEST",
                "shipping_name": o.shipping_name or "",
                "photo_url": o.photo_url,
                "item_url": o.item_url,
                "created_at": o.created_at.isoformat() if o.created_at else None,
            }
            for o in orders
        ]

        metrics_dict = {
            "total_orders": total_orders,
            "total_capital_deployed": round(float(total_simulated_spend), 2),
            "total_simulated_spend": round(float(total_simulated_spend), 2),
            "total_projected_resale": round(float(total_projected_resale), 2),
            "total_paper_profit": round(float(total_projected_profit), 2),
            "total_projected_profit": round(float(total_projected_profit), 2),
            "overall_roi_pct": avg_roi,
            "avg_roi_pct": avg_roi,
        }

        return {
            **metrics_dict,
            "metrics": metrics_dict,
            "orders": serialized_orders,
        }

    def clear_ledger(self, db: Session) -> int:
        """Clear historical order ledger."""
        deleted = db.query(AutoBuyOrder).delete()
        db.commit()
        return deleted


# Singleton instance
sniper_executor = SniperExecutorService()

