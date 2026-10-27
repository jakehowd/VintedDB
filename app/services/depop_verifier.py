"""Depop UK Demographic Price Disparity Engine & Cross-Listing Draft Generator.

Depop is the dominant secondary marketplace for Gen Z fashion in the UK.
Key Resale Arbitrage Dynamics:
1. Zero Seller Fees: On March 20, 2024, Depop eliminated selling fees in the UK (0% selling fee).
   Sellers only pay standard payment processing fees (2.9% + £0.30 via Depop Payments/Stripe).
2. Demographic Price Inefficiency: Vintage streetwear, Y2K aesthetic, Gorpcore, Carhartt,
   fleeces, and retro knitwear routinely sell for 25%–60% higher on Depop than on eBay or Vinted.
3. Sell-Through Rate (STR): High-velocity aesthetic items achieve > 60% STR within 7 days.
"""

from __future__ import annotations

import logging
import re
import urllib.parse
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Depop UK Fee Structure (Updated for 2024+ UK 0% Selling Fee Model)
DEPOP_SELLING_FEE_PCT = 0.00  # 0% selling fee for UK sellers
DEPOP_PAYMENT_PROCESSING_PCT = 0.029  # 2.9% payment processing
DEPOP_FIXED_ORDER_FEE_GBP = 0.30  # £0.30 fixed payment transaction fee
DEPOP_TRACKED_POSTAGE_OUTBOUND_GBP = 3.20  # Royal Mail Tracked 48 / Evri Post
DEPOP_PACKAGING_BUFFER_GBP = 0.50


# Pre-calibrated high-velocity Depop UK resale benchmarks
# Captures Gen Z aesthetic price disparities (Y2K, Gorpcore, Vintage Workwear, Retro Sportswear)
DEPOP_RESALE_BENCHMARKS: List[Dict[str, Any]] = [
    # ── Vintage Workwear & Gorpcore ───────────────────────────────────────────
    {
        "model_name": "Carhartt Detroit Jacket (J01 / J97 / EJ001)",
        "brand": "Carhartt",
        "sector": "outerwear",
        "depop_query": "Carhartt Detroit jacket vintage",
        "depop_sold_median": 155.0,
        "depop_sold_low": 115.0,
        "depop_sold_high": 220.0,
        "depop_str_pct": 74.0,
        "velocity_rating": "⚡ Depop Grail (< 48 hrs)",
        "match_keywords": ["detroit", "j97", "ej001", "j01", "blanket lined"],
        "negative_keywords": ["baby", "kids", "keyring", "socks", "tee", "t-shirt"],
        "aesthetic_tags": ["#carhartt", "#detroitjacket", "#vintagecarhartt", "#workwear", "#y2kworkwear"],
    },
    {
        "model_name": "Carhartt Active Jacket (J130 / Canvas Duck)",
        "brand": "Carhartt",
        "sector": "outerwear",
        "depop_query": "Carhartt Active jacket hooded",
        "depop_sold_median": 105.0,
        "depop_sold_low": 75.0,
        "depop_sold_high": 145.0,
        "depop_str_pct": 68.0,
        "velocity_rating": "🔥 High Demand (2-4 days)",
        "match_keywords": ["active jacket", "active", "j130", "duck jacket", "quilted active"],
        "negative_keywords": ["baby", "kids", "keyring", "socks", "tee"],
        "aesthetic_tags": ["#carhartt", "#activejacket", "#hoodedcanvas", "#skatewear", "#streetwear"],
    },
    {
        "model_name": "Carhartt Double Knee Pants / Baggy Work Trousers",
        "brand": "Carhartt",
        "sector": "outerwear",
        "depop_query": "Carhartt double knee pants baggy",
        "depop_sold_median": 68.0,
        "depop_sold_low": 48.0,
        "depop_sold_high": 95.0,
        "depop_str_pct": 72.0,
        "velocity_rating": "⚡ Rapid Flip (< 48 hrs)",
        "match_keywords": ["double knee", "b01", "work pants", "carpenter", "baggy pants"],
        "negative_keywords": ["shorts", "kids"],
        "aesthetic_tags": ["#carharttdoubleknee", "#baggycargos", "#skaterwear", "#workwear", "#y2kfashion"],
    },
    {
        "model_name": "The North Face 1996 Retro Nuptse 700",
        "brand": "The North Face",
        "sector": "outerwear",
        "depop_query": "North Face Nuptse 700 puffer",
        "depop_sold_median": 135.0,
        "depop_sold_low": 100.0,
        "depop_sold_high": 185.0,
        "depop_str_pct": 78.0,
        "velocity_rating": "⚡ Depop Essential (< 48 hrs)",
        "match_keywords": ["nuptse", "1996", "700", "puffer jacket", "down jacket"],
        "negative_keywords": ["gilet", "vest", "kids", "baby", "replica", "bootleg"],
        "aesthetic_tags": ["#thenorthface", "#nuptse700", "#1996nuptse", "#pufferjacket", "#winterstreetwear"],
    },
    {
        "model_name": "Arc'teryx Beta LT / AR Jacket",
        "brand": "Arc'teryx",
        "sector": "outerwear",
        "depop_query": "Arcteryx Beta LT jacket gorpcore",
        "depop_sold_median": 265.0,
        "depop_sold_low": 195.0,
        "depop_sold_high": 340.0,
        "depop_str_pct": 65.0,
        "velocity_rating": "⚡ Gorpcore Grail (< 48 hrs)",
        "match_keywords": ["beta lt", "beta ar", "beta jacket", "arcteryx beta", "arc'teryx beta"],
        "negative_keywords": ["beanie", "hat", "tee", "vest", "pants"],
        "aesthetic_tags": ["#arcteryx", "#betalt", "#gorpcore", "#goretex", "#techwear"],
    },
    {
        "model_name": "Patagonia Classic Retro-X Deep Pile Fleece",
        "brand": "Patagonia",
        "sector": "outerwear",
        "depop_query": "Patagonia Retro X deep pile fleece",
        "depop_sold_median": 110.0,
        "depop_sold_low": 80.0,
        "depop_sold_high": 150.0,
        "depop_str_pct": 69.0,
        "velocity_rating": "🔥 High Demand (2-4 days)",
        "match_keywords": ["retro-x", "retro x", "classic retro", "deep pile"],
        "negative_keywords": ["vest", "gilet", "kids"],
        "aesthetic_tags": ["#patagonia", "#retrox", "#deeppilefleece", "#vintagefleece", "#gorpcoreaesthetic"],
    },
    {
        "model_name": "Patagonia Synchilla Snap-T Fleece Pullover",
        "brand": "Patagonia",
        "sector": "outerwear",
        "depop_query": "Patagonia Synchilla Snap T fleece vintage",
        "depop_sold_median": 68.0,
        "depop_sold_low": 48.0,
        "depop_sold_high": 95.0,
        "depop_str_pct": 71.0,
        "velocity_rating": "⚡ Rapid Flip (< 48 hrs)",
        "match_keywords": ["synchilla", "snap-t", "snap t", "synchilla pullover"],
        "negative_keywords": ["pants", "shorts", "kids"],
        "aesthetic_tags": ["#patagonia", "#synchilla", "#snapt", "#retrooutdoors", "#vintagefleece"],
    },
    {
        "model_name": "Barbour Waxed Bedale / Beaufort Jacket",
        "brand": "Barbour",
        "sector": "outerwear",
        "depop_query": "Vintage Barbour Beaufort Bedale wax jacket",
        "depop_sold_median": 98.0,
        "depop_sold_low": 70.0,
        "depop_sold_high": 145.0,
        "depop_str_pct": 61.0,
        "velocity_rating": "🔥 High Demand (2-4 days)",
        "match_keywords": ["bedale", "beaufort", "waxed jacket", "wax jacket", "border"],
        "negative_keywords": ["quilted", "lining only", "scarf", "cap", "hat"],
        "aesthetic_tags": ["#barbour", "#beaufort", "#waxjacket", "#countrycore", "#vintageheritage"],
    },

    # ── Cult Streetwear & Y2K ─────────────────────────────────────────────────
    {
        "model_name": "Stussy 8-Ball Mohair Knit / Heavy Hoodie",
        "brand": "Stussy",
        "sector": "streetwear",
        "depop_query": "Stussy 8 ball hoodie mohair knit",
        "depop_sold_median": 92.0,
        "depop_sold_low": 65.0,
        "depop_sold_high": 140.0,
        "depop_str_pct": 76.0,
        "velocity_rating": "⚡ Depop Viral (< 48 hrs)",
        "match_keywords": ["8 ball", "8-ball", "eight ball", "dice", "mohair 8 ball"],
        "negative_keywords": ["beanie", "keychain", "socks", "poster"],
        "aesthetic_tags": ["#stussy", "#stussy8ball", "#mohairknit", "#y2kstreetwear", "#skatehoodie"],
    },
    {
        "model_name": "A Bathing Ape Shark Full Zip Hoodie",
        "brand": "Bape",
        "sector": "streetwear",
        "depop_query": "Bape shark hoodie full zip camo",
        "depop_sold_median": 175.0,
        "depop_sold_low": 125.0,
        "depop_sold_high": 245.0,
        "depop_str_pct": 62.0,
        "velocity_rating": "⚡ Rapid Flip (< 48 hrs)",
        "match_keywords": ["shark hoodie", "shark full zip", "wgm shark", "camo shark", "bape shark"],
        "negative_keywords": ["shorts", "tee", "mask", "sticker"],
        "aesthetic_tags": ["#bape", "#abathingape", "#sharkhoodie", "#harajuku", "#y2karchive"],
    },
    {
        "model_name": "Corteiz RTW Alcatraz / Guerillaz Tracksuit & Hoodie",
        "brand": "Corteiz",
        "sector": "streetwear",
        "depop_query": "Corteiz Alcatraz hoodie tracksuit crtrz",
        "depop_sold_median": 110.0,
        "depop_sold_low": 80.0,
        "depop_sold_high": 160.0,
        "depop_str_pct": 77.0,
        "velocity_rating": "⚡ Instant Flip (< 24 hrs)",
        "match_keywords": ["alcatraz", "corteiz hoodie", "crtrz", "guerillaz", "corteiz tracksuit"],
        "negative_keywords": ["socks", "balaclava", "sticker"],
        "aesthetic_tags": ["#corteiz", "#crtrz", "#alcatraz", "#ukstreetwear", "#trapstar"],
    },
    {
        "model_name": "Supreme Box Logo Crewneck / Hoodie",
        "brand": "Supreme",
        "sector": "streetwear",
        "depop_query": "Supreme box logo hoodie bogo",
        "depop_sold_median": 185.0,
        "depop_sold_low": 135.0,
        "depop_sold_high": 275.0,
        "depop_str_pct": 64.0,
        "velocity_rating": "⚡ Rapid Flip (< 48 hrs)",
        "match_keywords": ["box logo", "bogo", "supreme bogo", "box logo hoodie"],
        "negative_keywords": ["sticker", "beanie", "keychain"],
        "aesthetic_tags": ["#supreme", "#bogo", "#boxlogo", "#hypebeast", "#supremenewyork"],
    },
    {
        "model_name": "Palace Tri-Ferg Heavy Hoodie / Sweatshirt",
        "brand": "Palace",
        "sector": "streetwear",
        "depop_query": "Palace Tri Ferg hoodie skate",
        "depop_sold_median": 88.0,
        "depop_sold_low": 60.0,
        "depop_sold_high": 130.0,
        "depop_str_pct": 67.0,
        "velocity_rating": "🔥 High Demand (2-4 days)",
        "match_keywords": ["tri-ferg", "tri ferg", "palace hoodie", "triferg"],
        "negative_keywords": ["sticker", "cap", "pin", "deck only"],
        "aesthetic_tags": ["#palace", "#palaceskateboards", "#triferg", "#skatewear", "#londonstreetwear"],
    },

    # ── Luxury & Aesthetic Knitwear ───────────────────────────────────────────
    {
        "model_name": "Polo Ralph Lauren Iconic Cable-Knit Jumper",
        "brand": "Polo Ralph Lauren",
        "sector": "knitwear",
        "depop_query": "Ralph Lauren cable knit jumper vintage",
        "depop_sold_median": 58.0,
        "depop_sold_low": 42.0,
        "depop_sold_high": 85.0,
        "depop_str_pct": 82.0,
        "velocity_rating": "⚡ Depop Staple (< 24 hrs)",
        "match_keywords": ["cable knit", "cable-knit", "cable jumper", "cable sweater", "iconic cable"],
        "negative_keywords": ["kids", "baby", "vest", "dress"],
        "aesthetic_tags": ["#ralphlauren", "#cableknit", "#oldmoney", "#preppyaesthetic", "#vintagepolo"],
    },
    {
        "model_name": "Polo Ralph Lauren Cotton Quarter Zip Pullover",
        "brand": "Polo Ralph Lauren",
        "sector": "knitwear",
        "depop_query": "Ralph Lauren quarter zip jumper pullover",
        "depop_sold_median": 48.0,
        "depop_sold_low": 35.0,
        "depop_sold_high": 72.0,
        "depop_str_pct": 79.0,
        "velocity_rating": "⚡ High Turnover (< 48 hrs)",
        "match_keywords": ["quarter zip", "1/4 zip", "quarter-zip", "half zip", "zip neck"],
        "negative_keywords": ["kids", "baby", "fleece"],
        "aesthetic_tags": ["#ralphlauren", "#quarterzip", "#oldmoneyaesthetic", "#preppy", "#vintageknit"],
    },
    {
        "model_name": "Stone Island Compass Patch Crewneck / Knitwear",
        "brand": "Stone Island",
        "sector": "knitwear",
        "depop_query": "Stone Island crewneck jumper sweatshirt patch",
        "depop_sold_median": 140.0,
        "depop_sold_low": 105.0,
        "depop_sold_high": 195.0,
        "depop_str_pct": 66.0,
        "velocity_rating": "⚡ Rapid Flip (< 48 hrs)",
        "match_keywords": ["stone island crewneck", "stone island jumper", "compass patch", "stone island knit"],
        "negative_keywords": ["badge only", "patch only", "cap", "beanie"],
        "aesthetic_tags": ["#stoneisland", "#stoneislanduk", "#compasspatch", "#terracewear", "#casuals"],
    },
    {
        "model_name": "C.P. Company Arm Lens Sweatshirt / Knit",
        "brand": "CP Company",
        "sector": "knitwear",
        "depop_query": "CP Company arm lens sweatshirt goggle",
        "depop_sold_median": 105.0,
        "depop_sold_low": 78.0,
        "depop_sold_high": 150.0,
        "depop_str_pct": 63.0,
        "velocity_rating": "🔥 High Demand (2-4 days)",
        "match_keywords": ["arm lens", "lens sweatshirt", "cp company lens", "goggle knit", "cp company jumper"],
        "negative_keywords": ["lens only", "goggle only", "cap"],
        "aesthetic_tags": ["#cpcompany", "#armlens", "#gogglejacket", "#millemiglia", "#italiancasuals"],
    },

    # ── High-Demand Footwear ──────────────────────────────────────────────────
    {
        "model_name": "Nike Dunk Low Retro (Panda / Grey Fog)",
        "brand": "Nike",
        "sector": "footwear",
        "depop_query": "Nike Dunk Low Retro Panda Grey Fog",
        "depop_sold_median": 75.0,
        "depop_sold_low": 52.0,
        "depop_sold_high": 105.0,
        "depop_str_pct": 73.0,
        "velocity_rating": "⚡ Rapid Flip (< 48 hrs)",
        "match_keywords": ["dunk low", "dunk retro", "panda dunk", "grey fog dunk"],
        "negative_keywords": ["kids", "infant", "toddler", "box only"],
        "aesthetic_tags": ["#nikedunk", "#dunklow", "#pandadunk", "#sneakerhead", "#streetwearkicks"],
    },
    {
        "model_name": "New Balance 990v3 Made in USA",
        "brand": "New Balance",
        "sector": "footwear",
        "depop_query": "New Balance 990v3 Made in USA grey",
        "depop_sold_median": 120.0,
        "depop_sold_low": 85.0,
        "depop_sold_high": 175.0,
        "depop_str_pct": 68.0,
        "velocity_rating": "⚡ Rapid Flip (< 48 hrs)",
        "match_keywords": ["990v3", "990 v3", "m990gy3", "m990v3", "990 version 3", "v3 made in usa"],
        "negative_keywords": ["kids", "infant", "toddler", "v5", "990v5", "v4", "v6", "v1", "v2"],
        "aesthetic_tags": ["#newbalance", "#nb990v3", "#madeinusa", "#dadshoes", "#normcore"],
    },
    {
        "model_name": "New Balance 990v5 Made in USA",
        "brand": "New Balance",
        "sector": "footwear",
        "depop_query": "New Balance 990v5 Made in USA grey",
        "depop_sold_median": 95.0,
        "depop_sold_low": 68.0,
        "depop_sold_high": 135.0,
        "depop_str_pct": 65.0,
        "velocity_rating": "🔥 High Demand (2-4 days)",
        "match_keywords": ["990v5", "990 v5", "m990gl5", "m990v5", "990 version 5"],
        "negative_keywords": ["kids", "infant", "toddler", "v3", "990v3", "v4", "v6"],
        "aesthetic_tags": ["#newbalance", "#nb990v5", "#madeinusa", "#greykicks", "#comfystyle"],
    },
    {
        "model_name": "Adidas Samba OG / Spezial",
        "brand": "Adidas",
        "sector": "footwear",
        "depop_query": "Adidas Samba OG Spezial terrace",
        "depop_sold_median": 68.0,
        "depop_sold_low": 48.0,
        "depop_sold_high": 90.0,
        "depop_str_pct": 81.0,
        "velocity_rating": "⚡ Depop Viral (< 24 hrs)",
        "match_keywords": ["samba og", "samba classic", "adidassamba", "spezial", "handball spezial"],
        "negative_keywords": ["kids", "baby"],
        "aesthetic_tags": ["#adidassamba", "#sambaog", "#blokecore", "#terracestyle", "#itgirlstyle"],
    },
    {
        "model_name": "Salomon XT-6 Advanced / XT-4",
        "brand": "Salomon",
        "sector": "footwear",
        "depop_query": "Salomon XT-6 trail running gorpcore",
        "depop_sold_median": 115.0,
        "depop_sold_low": 85.0,
        "depop_sold_high": 155.0,
        "depop_str_pct": 70.0,
        "velocity_rating": "⚡ Gorpcore Essential (< 48 hrs)",
        "match_keywords": ["xt-6", "xt 6", "salomon xt", "xt-4", "xt 4", "salomon advanced"],
        "negative_keywords": ["kids", "socks"],
        "aesthetic_tags": ["#salomon", "#salomonxt6", "#gorpcore", "#trailrunning", "#technicalfootwear"],
    },

    # ── Retro Sportswear & Y2K Blokecore ───────────────────────────────────────
    {
        "model_name": "Vintage Nike Centre Swoosh Hoodie / Sweatshirt",
        "brand": "Nike",
        "sector": "sportswear",
        "depop_query": "Vintage Nike centre swoosh hoodie middle swoosh",
        "depop_sold_median": 72.0,
        "depop_sold_low": 50.0,
        "depop_sold_high": 110.0,
        "depop_str_pct": 84.0,
        "velocity_rating": "⚡ Depop Holy Grail (< 24 hrs)",
        "match_keywords": ["centre swoosh", "middle swoosh", "center swoosh", "vintage nike hoodie"],
        "negative_keywords": ["pants", "shorts", "kids"],
        "aesthetic_tags": ["#vintagenike", "#centreswoosh", "#y2knike", "#vintagesweatshirt", "#90snike"],
    },
    {
        "model_name": "Vintage 90s Football Shirt (Original Kit)",
        "brand": "Vintage Football",
        "sector": "sportswear",
        "depop_query": "Vintage 90s football shirt retro kit original",
        "depop_sold_median": 78.0,
        "depop_sold_low": 50.0,
        "depop_sold_high": 135.0,
        "depop_str_pct": 79.0,
        "velocity_rating": "⚡ Blokecore Viral (< 24 hrs)",
        "match_keywords": ["football shirt", "football jersey", "soccer jersey", "90s shirt", "vintage kit"],
        "negative_keywords": ["replica remake", "modern reprint", "fans version", "dhgate"],
        "aesthetic_tags": ["#vintagefootballshirt", "#blokecore", "#retrofootball", "#90skit", "#classicfootballshirts"],
    },
]


class DepopPriceDisparityEngine:
    """Calculates Depop secondary market prices, demographic premiums, and generates cross-listing drafts."""

    def __init__(self) -> None:
        self.benchmarks = DEPOP_RESALE_BENCHMARKS

    def match_benchmark(
        self,
        title: str,
        brand: Optional[str] = None,
        override_model: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Match item to the best Depop benchmark."""
        clean_title = (title or "").lower()
        clean_brand = (brand or "").lower().strip()

        if override_model:
            override_lower = override_model.lower()
            for b in self.benchmarks:
                if b["model_name"].lower() in override_lower or override_lower in b["model_name"].lower():
                    return b

        for b in self.benchmarks:
            b_brand = b["brand"].lower()
            if b_brand not in clean_title and clean_brand not in b_brand and b_brand not in clean_brand:
                continue

            if any(nk in clean_title for nk in b.get("negative_keywords", [])):
                continue

            if any(mk in clean_title for mk in b["match_keywords"]):
                return b

        return None

    def calculate_depop_net_payout(self, gross_sold_price: float) -> Dict[str, float]:
        """Calculate net realized cash payout after selling on Depop UK.
        
        Depop UK 2024 model:
        - 0% selling fee
        - 2.9% + £0.30 payment processing fee
        - Tracked postage: £3.20 (Tracked 48 / Evri)
        - Packaging buffer: £0.50
        """
        p = max(0.0, float(gross_sold_price))
        selling_fee = round(p * DEPOP_SELLING_FEE_PCT, 2)
        payment_fee = round((p * DEPOP_PAYMENT_PROCESSING_PCT) + DEPOP_FIXED_ORDER_FEE_GBP, 2)
        outbound_shipping = DEPOP_TRACKED_POSTAGE_OUTBOUND_GBP
        packaging_buffer = DEPOP_PACKAGING_BUFFER_GBP

        total_deductions = round(selling_fee + payment_fee + outbound_shipping + packaging_buffer, 2)
        net_payout = round(max(0.0, p - total_deductions), 2)

        return {
            "gross_sold_price": p,
            "selling_fee": selling_fee,
            "payment_processing_fee": payment_fee,
            "outbound_shipping": outbound_shipping,
            "packaging_buffer": packaging_buffer,
            "total_deductions": total_deductions,
            "net_payout": net_payout,
        }

    def verify_depop_arbitrage(
        self,
        title: str,
        vinted_landed_cost: float,
        brand: Optional[str] = None,
        condition_multiplier: float = 1.0,
        size_multiplier: float = 1.0,
        colorway_multiplier: float = 1.0,
        override_benchmark_model: Optional[str] = None,
        ebay_net_profit: float = 0.0,
    ) -> Optional[Dict[str, Any]]:
        """Verify candidate on Depop, calculate net payout, STR, and comparison with eBay."""
        bench = self.match_benchmark(title, brand, override_model=override_benchmark_model)
        if not bench:
            return None

        base_median = float(bench["depop_sold_median"])
        base_low = float(bench["depop_sold_low"])
        base_high = float(bench["depop_sold_high"])

        # Combined realization
        combined_mult = round(condition_multiplier * size_multiplier * colorway_multiplier, 3)
        depop_realized_price = round(base_median * combined_mult, 2)
        depop_low = round(base_low * combined_mult, 2)
        depop_high = round(base_high * combined_mult, 2)

        payout_info = self.calculate_depop_net_payout(depop_realized_price)
        depop_net_payout = payout_info["net_payout"]

        depop_net_profit = round(depop_net_payout - vinted_landed_cost, 2)
        depop_roi_pct = round((depop_net_profit / vinted_landed_cost) * 100, 1) if vinted_landed_cost > 0 else 0.0

        # Disparity compared to eBay
        profit_delta_vs_ebay = round(depop_net_profit - ebay_net_profit, 2)
        is_depop_superior = profit_delta_vs_ebay > 0.0

        encoded_q = urllib.parse.quote_plus(bench["depop_query"])
        depop_search_url = f"https://www.depop.com/search/?q={encoded_q}"

        return {
            "depop_eligible": True,
            "depop_model_name": bench["model_name"],
            "depop_query": bench["depop_query"],
            "depop_sold_median": depop_realized_price,
            "depop_sold_low": depop_low,
            "depop_sold_high": depop_high,
            "depop_sold_range_str": f"£{depop_low:.0f} - £{depop_high:.0f}",
            "depop_str_pct": bench["depop_str_pct"],
            "depop_velocity_rating": bench["velocity_rating"],
            "depop_aesthetic_tags": bench["aesthetic_tags"],
            "depop_search_url": depop_search_url,
            "depop_net_payout": depop_net_payout,
            "depop_fees_breakdown": payout_info,
            "depop_net_profit": depop_net_profit,
            "depop_roi_pct": depop_roi_pct,
            "profit_delta_vs_ebay": profit_delta_vs_ebay,
            "is_depop_superior": is_depop_superior,
            "best_resale_platform": "Depop" if is_depop_superior else "eBay UK",
            "advantage_badge": (
                f"🔥 Depop Advantage (+£{profit_delta_vs_ebay:.2f} vs eBay)"
                if is_depop_superior and profit_delta_vs_ebay >= 5.0
                else "Solid Cross-Market Spread"
            ),
        }

    def generate_cross_listing_package(
        self,
        title: str,
        brand: str,
        model_name: str,
        size: str,
        condition: str,
        colorway: str,
        asking_price: float,
        ebay_sold_median: float,
        depop_sold_median: Optional[float] = None,
        photo_url: str = "",
        vinted_item_id: str = "",
    ) -> Dict[str, Any]:
        """Generate a complete 1-Click Cross-Listing Draft package for eBay UK and Depop."""
        clean_brand = brand or "Vintage"
        clean_model = re.sub(r"\(.*?\)", "", model_name or title).strip()
        clean_size = (size or "Regular").strip()
        clean_color = (colorway or "Classic").strip()
        if clean_color == "Standard / Classic":
            clean_color = "Original"

        # 1. eBay Draft Generator (Strict 80-character maximum)
        ebay_tokens = []
        if not clean_model.lower().startswith(clean_brand.lower()):
            ebay_tokens.append(clean_brand)
        ebay_tokens.append(clean_model)

        if clean_color and clean_color.lower() not in clean_model.lower():
            ebay_tokens.append(clean_color)
        if clean_size and clean_size.lower() not in ("regular", "one size", "os"):
            ebay_tokens.append(f"Size {clean_size}")
        ebay_tokens.append("Authentic")

        ebay_raw_title = " ".join(ebay_tokens)
        ebay_raw_title = re.sub(r"\s+", " ", ebay_raw_title).strip()
        if len(ebay_raw_title) > 80:
            ebay_raw_title = ebay_raw_title[:80].rsplit(" ", 1)[0]

        ebay_recommended_bin = round(ebay_sold_median, 2)
        ebay_best_offer_floor = round(ebay_sold_median * 0.85, 2)

        # Pre-fill URL into eBay's listing creation tool
        ebay_prelist_url = f"https://www.ebay.co.uk/sl/prelist/suggest?sr=sh&q={urllib.parse.quote_plus(ebay_raw_title)}"

        # 2. Depop Draft Generator
        effective_depop_price = depop_sold_median or round(ebay_sold_median * 1.15, 2)
        depop_brand_prefix = f"Vintage {clean_brand}" if not clean_model.lower().startswith(clean_brand.lower()) else "Vintage"
        depop_hook = f"{depop_brand_prefix} {clean_model} in {clean_color} ({clean_size})"

        bench_match = self.match_benchmark(title, brand=brand, override_model=model_name)
        tags = bench_match["aesthetic_tags"] if bench_match else ["#vintage", "#streetwear", "#y2k", "#fashion", "#grail"]
        tags_str = " ".join(tags)

        depop_description = (
            f"⚡ {depop_hook}\n\n"
            f"• Brand: {clean_brand}\n"
            f"• Condition: {condition or 'Very Good'}\n"
            f"• Tagged Size: {clean_size}\n"
            f"• Pit to Pit / Measurements: Available on request\n"
            f"• Colorway: {clean_color}\n\n"
            f"📦 Fast Royal Mail Tracked UK Dispatch\n"
            f"💬 Instant buy is ON. Send offers via the offer button!\n\n"
            f"{tags_str}"
        )

        # 3. 20% Vinted Lowball / Offer Target Calculation
        offer_20_price = round(asking_price * 0.80, 2)
        offer_vinted_protection = round(0.70 + (offer_20_price * 0.05), 2)
        offer_landed_cost = round(offer_20_price + offer_vinted_protection + 2.95 + 0.60, 2)

        # eBay margin at 20% offer
        ebay_payout = ebay_sold_median * (1 - 0.128 - 0.0035) - 0.30 - 3.50 - 0.50
        ebay_profit_at_offer = round(ebay_payout - offer_landed_cost, 2)
        ebay_roi_at_offer = round((ebay_profit_at_offer / offer_landed_cost) * 100, 1) if offer_landed_cost > 0 else 0.0

        # Depop margin at 20% offer
        depop_payout = effective_depop_price * (1 - 0.029) - 0.30 - 3.20 - 0.50
        depop_profit_at_offer = round(depop_payout - offer_landed_cost, 2)
        depop_roi_at_offer = round((depop_profit_at_offer / offer_landed_cost) * 100, 1) if offer_landed_cost > 0 else 0.0

        # 4. Visual Reverse-Image Search Links
        # Google Lens allows inspecting unmarked/vague pieces against web catalogues
        google_lens_url = ""
        if photo_url and photo_url.startswith("http"):
            google_lens_url = f"https://lens.google.com/uploadbyurl?url={urllib.parse.quote_plus(photo_url)}"

        return {
            "vinted_item_id": vinted_item_id,
            "original_vinted_title": title,
            "vinted_asking_price": asking_price,
            "photo_url": photo_url,
            "offer_engine": {
                "asking_price": asking_price,
                "offer_20_price": offer_20_price,
                "offer_discount_gbp": round(asking_price - offer_20_price, 2),
                "offer_landed_cost": offer_landed_cost,
                "ebay_profit_at_offer": ebay_profit_at_offer,
                "ebay_roi_at_offer": ebay_roi_at_offer,
                "depop_profit_at_offer": depop_profit_at_offer,
                "depop_roi_at_offer": depop_roi_at_offer,
                "offer_instruction": f"Send lowball offer of £{offer_20_price:.2f} on Vinted (max -20% instant offer allowance).",
            },
            "ebay_draft": {
                "title": ebay_raw_title,
                "title_char_count": len(ebay_raw_title),
                "recommended_bin_price": ebay_recommended_bin,
                "best_offer_floor_price": ebay_best_offer_floor,
                "prelist_url": ebay_prelist_url,
                "item_specifics": {
                    "Brand": clean_brand,
                    "Model": clean_model,
                    "Size": clean_size,
                    "Department": "Men's / Unisex",
                    "Colour": clean_color,
                    "Condition": condition or "Pre-owned / Very Good",
                },
            },
            "depop_draft": {
                "title": depop_hook,
                "recommended_listing_price": effective_depop_price,
                "description": depop_description,
                "hashtags": tags,
                "create_listing_url": "https://www.depop.com/products/create/",
            },
            "visual_lens": {
                "google_lens_url": google_lens_url,
                "instruction": "Open Google Lens to instantly reverse-match photo against sold archival pieces and tags.",
            },
        }


# Singleton instance
depop_verifier = DepopPriceDisparityEngine()
