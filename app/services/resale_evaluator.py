"""AI Resale Profitability & Risk Evaluator for Vinted UK listings.

Evaluates secondary-market resale feasibility by combining:
1. Exact Vinted UK buyer fee & shipping mathematics (Landed Buy Cost).
2. Database-calibrated sold price realization across exact model/style variants.
3. Liquidity & turnover velocity metrics (days-to-sale, sell-through rate).
4. Condition and size demand penalties.
5. Seller trustworthiness and scam risk signals.
6. Composite AI Resale Score (0-100) and actionable buy/pass verdict.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models import ProductVariant, SoldItem, TrackedSearch

logger = logging.getLogger(__name__)

# Typical UK parcel shipping costs for secondhand apparel & compact electronics
DEFAULT_UK_SHIPPING_GBP = 2.95
DEFAULT_PACKAGING_GBP = 0.60
VINTED_BUYER_PROTECTION_FIXED_GBP = 0.70
VINTED_BUYER_PROTECTION_PCT = 0.05

# Condition multiplier on expected sold price relative to 'Very good'
CONDITION_MULTIPLIERS = {
    "new with tags": 1.15,
    "brand new with tags": 1.15,
    "bnwt": 1.15,
    "new without tags": 1.05,
    "brand new without tags": 1.05,
    "bnwot": 1.05,
    "very good": 1.00,
    "very good condition": 1.00,
    "good": 0.88,
    "good condition": 0.88,
    "satisfactory": 0.72,
    "used": 0.85,
}

# Demand tiers for standard UK men's / women's sizing
HIGH_DEMAND_SIZES = {"s", "m", "l", "8", "10", "12", "14", "medium", "large", "small", "uk 8", "uk 10", "uk 12"}
LOW_DEMAND_SIZES = {"xxs", "xxxl", "3xl", "4xl", "5xl", "uk 4", "uk 20", "uk 22"}


def calculate_landed_buy_cost(
    asking_price: float,
    shipping_cost: float = DEFAULT_UK_SHIPPING_GBP,
    packaging_cost: float = DEFAULT_PACKAGING_GBP,
) -> Dict[str, float]:
    """Calculate the true all-in landed cost to purchase an item on Vinted UK."""
    asking = max(0.0, float(asking_price))
    protection_fee = round(VINTED_BUYER_PROTECTION_FIXED_GBP + (asking * VINTED_BUYER_PROTECTION_PCT), 2)
    ship = round(max(0.0, float(shipping_cost)), 2)
    pack = round(max(0.0, float(packaging_cost)), 2)
    total_landed = round(asking + protection_fee + ship + pack, 2)

    return {
        "asking_price": asking,
        "buyer_protection_fee": protection_fee,
        "shipping_cost": ship,
        "packaging_overhead": pack,
        "total_landed_cost": total_landed,
    }


class AIResaleEvaluator:
    """Intelligent resale profitability and deal quality gate."""

    def evaluate_deal(
        self,
        db: Session,
        tracked_search: TrackedSearch,
        deal: Dict[str, Any],
        custom_variant: Optional[ProductVariant] = None,
    ) -> Dict[str, Any]:
        """Produce a comprehensive resale profitability assessment for a sniped deal."""
        asking_price = float(deal.get("price") or 0.0)
        vinted_id = str(deal.get("id") or "")
        title = str(deal.get("title") or "")
        condition = str(deal.get("condition") or "").strip().lower()
        size = str(deal.get("size") or "").strip().lower()
        seller_username = str(deal.get("seller_username") or "")
        variant_ids = deal.get("variant_ids") or []
        variant_id = deal.get("variant_id") or (variant_ids[-1] if variant_ids else None)

        # 1. Landed Cost Math
        cost_breakdown = calculate_landed_buy_cost(asking_price)
        total_landed = cost_breakdown["total_landed_cost"]

        # 2. Historical Market Realization from DB
        market_stats = self._get_market_statistics(
            db=db,
            search_id=tracked_search.id,
            variant_id=variant_id,
            custom_variant=custom_variant,
        )

        base_resale_target = market_stats["median_sold_price"]
        sample_count = market_stats["sold_samples_count"]

        # 3. Condition & Size Adjustments
        cond_mult = CONDITION_MULTIPLIERS.get(condition, 0.95)
        size_mult = 1.0
        if size in HIGH_DEMAND_SIZES:
            size_mult = 1.02
        elif size in LOW_DEMAND_SIZES:
            size_mult = 0.88

        adjusted_resale_target = round(base_resale_target * cond_mult * size_mult, 2)
        # Conservative floor: never project below the 25th percentile of sold items
        if market_stats["p25_sold_price"] > 0:
            adjusted_resale_target = max(
                round(market_stats["p25_sold_price"] * 0.92, 2),
                adjusted_resale_target,
            )

        # 4. Projected Net Profit & ROI
        projected_net_profit = round(adjusted_resale_target - total_landed, 2)
        roi_pct = round((projected_net_profit / total_landed) * 100, 1) if total_landed > 0 else 0.0

        # 5. Liquidity & Sales Pace
        velocity_rating, days_to_sell_est = self._estimate_liquidity(
            sold_past_7d=market_stats["sold_past_7d"],
            sold_past_30d=market_stats["sold_past_30d"],
            active_count=market_stats["active_count"],
        )

        # 6. Seller & Scam Risk Assessment
        seller_risk_level, risk_factors = self._assess_seller_risk(
            asking_price=asking_price,
            market_median=base_resale_target,
            condition=condition,
            seller_username=seller_username,
            title=title,
        )

        # 7. Composite AI Score (0 to 100)
        ai_score, verdict = self._compute_verdict(
            net_profit=projected_net_profit,
            roi_pct=roi_pct,
            velocity_rating=velocity_rating,
            seller_risk_level=seller_risk_level,
            sample_count=sample_count,
        )

        # 8. Human-Readable Reasoning Bullets
        reasoning: List[str] = []
        if projected_net_profit > 0:
            reasoning.append(
                f"Projected net margin £{projected_net_profit:+.2f} ({roi_pct:.0f}% ROI) after Vinted protection & shipping fees."
            )
        else:
            reasoning.append(
                f"Negative margin: Total landed cost £{total_landed:.2f} exceeds realistic resale value £{adjusted_resale_target:.2f}."
            )

        reasoning.append(
            f"Based on {sample_count} verified sold records (median sold: £{base_resale_target:.2f}, 25th percentile: £{market_stats['p25_sold_price']:.2f})."
        )
        reasoning.append(f"Turnover liquidity: {velocity_rating} (est. {days_to_sell_est}).")

        if risk_factors:
            reasoning.extend([f"Risk alert: {rf}" for rf in risk_factors])

        return {
            "vinted_id": vinted_id,
            "title": title,
            "ai_score": ai_score,
            "verdict": verdict,  # STRONG_BUY, VIABLE_BUY, MARGINAL, PASS
            "is_recommended": verdict in ("STRONG_BUY", "VIABLE_BUY"),
            "asking_price": asking_price,
            "total_landed_cost": total_landed,
            "cost_breakdown": cost_breakdown,
            "projected_resale_price": adjusted_resale_target,
            "base_market_median": base_resale_target,
            "projected_net_profit": projected_net_profit,
            "roi_pct": roi_pct,
            "liquidity_rating": velocity_rating,
            "estimated_days_to_sell": days_to_sell_est,
            "seller_risk_level": seller_risk_level,
            "sold_samples_count": sample_count,
            "reasoning": reasoning,
            "evaluated_at": datetime.now(timezone.utc).isoformat(),
        }

    def _get_market_statistics(
        self,
        db: Session,
        search_id: int,
        variant_id: Optional[int],
        custom_variant: Optional[ProductVariant] = None,
    ) -> Dict[str, Any]:
        """Query real sold database items to build statistical price and velocity distributions."""
        now = datetime.now(timezone.utc)
        d7 = now - timedelta(days=7)
        d30 = now - timedelta(days=30)

        # Target variant filter if present
        v_clause = None
        if variant_id:
            from ..routers.variants import item_has_variant_clause
            v_clause = item_has_variant_clause(variant_id)

        q_sold = (
            db.query(SoldItem.price, SoldItem.captured_at)
            .filter(
                SoldItem.tracked_search_id == search_id,
                SoldItem.is_sold == True,
                SoldItem.is_replica.isnot(True),
                SoldItem.price > 0,
            )
        )
        if v_clause is not None:
            q_sold = q_sold.filter(v_clause)

        sold_rows = q_sold.all()
        prices = sorted([float(r[0]) for r in sold_rows if r[0] is not None])

        # Active listings count for liquidity ratios
        q_active = (
            db.query(func.count(SoldItem.id))
            .filter(
                SoldItem.tracked_search_id == search_id,
                SoldItem.is_sold == False,
                SoldItem.is_replica.isnot(True),
            )
        )
        if v_clause is not None:
            q_active = q_active.filter(v_clause)
        active_count = q_active.scalar() or 0

        # If too few variant samples exist, broaden to whole search
        if len(prices) < 4 and v_clause is not None:
            broad_rows = (
                db.query(SoldItem.price, SoldItem.captured_at)
                .filter(
                    SoldItem.tracked_search_id == search_id,
                    SoldItem.is_sold == True,
                    SoldItem.is_replica.isnot(True),
                    SoldItem.price > 0,
                )
                .all()
            )
            broad_prices = sorted([float(r[0]) for r in broad_rows if r[0] is not None])
            if broad_prices:
                prices = broad_prices
                sold_rows = broad_rows

        if not prices:
            fallback_val = 35.0
            if custom_variant and custom_variant.est_market_value_gbp:
                fallback_val = float(custom_variant.est_market_value_gbp)
            return {
                "median_sold_price": fallback_val,
                "p25_sold_price": round(fallback_val * 0.8, 2),
                "p75_sold_price": round(fallback_val * 1.2, 2),
                "sold_samples_count": 0,
                "sold_past_7d": 0,
                "sold_past_30d": 0,
                "active_count": active_count,
            }

        n = len(prices)
        med = prices[n // 2]
        p25 = prices[max(0, int(n * 0.25))]
        p75 = prices[min(n - 1, int(n * 0.75))]

        s_7d = sum(1 for r in sold_rows if r[1] and r[1].replace(tzinfo=timezone.utc) >= d7)
        s_30d = sum(1 for r in sold_rows if r[1] and r[1].replace(tzinfo=timezone.utc) >= d30)

        return {
            "median_sold_price": round(med, 2),
            "p25_sold_price": round(p25, 2),
            "p75_sold_price": round(p75, 2),
            "sold_samples_count": n,
            "sold_past_7d": s_7d,
            "sold_past_30d": s_30d,
            "active_count": active_count,
        }

    def _estimate_liquidity(
        self,
        sold_past_7d: int,
        sold_past_30d: int,
        active_count: int,
    ) -> Tuple[str, str]:
        """Determine velocity rating and estimated turnaround time."""
        if sold_past_7d >= 8 or sold_past_30d >= 25:
            return "⚡ ULTRA FAST", "< 48 hours"
        elif sold_past_7d >= 3 or sold_past_30d >= 12:
            return "🔥 HIGH DEMAND", "2 to 5 days"
        elif sold_past_30d >= 4:
            return "⚖️ MODERATE", "5 to 12 days"
        else:
            return "🐢 SLUGGISH", "> 14 days"

    def _assess_seller_risk(
        self,
        asking_price: float,
        market_median: float,
        condition: str,
        seller_username: str,
        title: str,
    ) -> Tuple[str, List[str]]:
        """Identify red flags such as potential replica dumps, severe damage, or suspicious pricing."""
        risk_factors: List[str] = []
        risk_score = 0

        # Extreme underpricing (often counterfeit or scam bait)
        if market_median >= 40.0 and asking_price <= (market_median * 0.22):
            risk_factors.append(
                f"Severe underpricing (£{asking_price:.2f} is <22% of £{market_median:.2f} market value); inspect authenticity closely."
            )
            risk_score += 2

        if "satisfactory" in condition or "damaged" in condition or "faulty" in title.lower():
            risk_factors.append("Item condition marked as Satisfactory / Damaged; resale price may be heavily discounted.")
            risk_score += 1

        if not seller_username:
            risk_factors.append("Seller profile metadata missing from preview.")
            risk_score += 1

        if risk_score >= 3:
            return "HIGH_RISK", risk_factors
        elif risk_score >= 1:
            return "MEDIUM_RISK", risk_factors
        return "LOW_RISK", risk_factors

    def _compute_verdict(
        self,
        net_profit: float,
        roi_pct: float,
        velocity_rating: str,
        seller_risk_level: str,
        sample_count: int,
    ) -> Tuple[int, str]:
        """Calculate 0-100 score and categorical recommendation."""
        score = 50.0

        # Margin points (-30 to +30)
        if net_profit >= 35.0:
            score += 28
        elif net_profit >= 20.0:
            score += 20
        elif net_profit >= 12.0:
            score += 12
        elif net_profit >= 5.0:
            score += 4
        elif net_profit <= 0.0:
            score -= 35

        # ROI points (-20 to +20)
        if roi_pct >= 80.0:
            score += 18
        elif roi_pct >= 45.0:
            score += 12
        elif roi_pct >= 25.0:
            score += 5
        elif roi_pct < 10.0:
            score -= 15

        # Liquidity bonus / penalty
        if "ULTRA" in velocity_rating:
            score += 10
        elif "HIGH" in velocity_rating:
            score += 6
        elif "SLUGGISH" in velocity_rating:
            score -= 12

        # Risk penalty
        if seller_risk_level == "HIGH_RISK":
            score -= 25
        elif seller_risk_level == "MEDIUM_RISK":
            score -= 8

        # Confidence penalty if sample count is tiny
        if sample_count < 3:
            score -= 10

        final_score = int(max(0, min(100, round(score))))

        if final_score >= 75 and net_profit >= 15.0 and seller_risk_level != "HIGH_RISK":
            verdict = "STRONG_BUY"
        elif final_score >= 55 and net_profit >= 8.0 and seller_risk_level != "HIGH_RISK":
            verdict = "VIABLE_BUY"
        elif final_score >= 38 and net_profit > 0.0:
            verdict = "MARGINAL"
        else:
            verdict = "PASS"

        return final_score, verdict


# Singleton instance
resale_evaluator = AIResaleEvaluator()

