"""Unit and Integration Tests for Depop UK Disparity, 20% Offer Target & Cross-Listing Studio."""

from app.services.depop_verifier import depop_verifier
from app.services.ebay_verifier import ebay_verifier
from app.services.arbitrage_radar import arbitrage_radar
from app.database import SessionLocal


def test_depop_fee_payout_calculation():
    """Verify UK 2024 0% selling fee + 2.9% + £0.30 payment processing."""
    # Selling for £100
    payout = depop_verifier.calculate_depop_net_payout(100.0)
    assert payout["gross_sold_price"] == 100.0
    assert payout["selling_fee"] == 0.0  # 0% selling fee in UK
    assert payout["payment_processing_fee"] == round(100.0 * 0.029 + 0.30, 2)  # £3.20
    assert payout["outbound_shipping"] == 3.20
    assert payout["packaging_buffer"] == 0.50
    # Total deductions: 3.20 + 3.20 + 0.50 = 6.90 -> Net payout: 93.10
    assert payout["net_payout"] == 93.10


def test_depop_verification_and_arbitrage_advantage():
    """Verify item against Depop demographic benchmarks and compare with eBay."""
    vinted_landed_cost = 25.0
    comp = depop_verifier.verify_depop_arbitrage(
        title="Carhartt Detroit Jacket Canvas Duck",
        vinted_landed_cost=vinted_landed_cost,
        brand="Carhartt",
        condition_multiplier=1.0,
        size_multiplier=1.0,
        colorway_multiplier=1.0,
        ebay_net_profit=60.0,
    )
    assert comp is not None
    assert comp["depop_eligible"] is True
    assert comp["depop_sold_median"] == 155.0
    assert comp["depop_str_pct"] >= 65.0
    assert comp["depop_net_profit"] > 0
    assert comp["is_depop_superior"] is True
    assert "#carhartt" in comp["depop_aesthetic_tags"]
    assert "https://www.depop.com/search/?q=" in comp["depop_search_url"]


def test_cross_listing_package_generation():
    """Verify 1-Click Cross-Listing Draft generation for eBay UK (<= 80 chars) and Depop."""
    package = depop_verifier.generate_cross_listing_package(
        title="Vintage Carhartt Detroit Jacket Canvas J97",
        brand="Carhartt",
        model_name="Carhartt Detroit Jacket (J01 / J97 / EJ001)",
        size="L",
        condition="Very Good",
        colorway="Moss Green (J97 MOS)",
        asking_price=30.0,
        ebay_sold_median=135.0,
        depop_sold_median=155.0,
        photo_url="https://images1.vinted.net/test.jpg",
        vinted_item_id="12345678",
    )
    # Check eBay Draft
    ebay = package["ebay_draft"]
    assert len(ebay["title"]) <= 80
    assert "Carhartt" in ebay["title"]
    assert "Detroit Jacket" in ebay["title"]
    assert "Size L" in ebay["title"]
    assert ebay["recommended_bin_price"] == 135.0
    assert ebay["best_offer_floor_price"] == round(135.0 * 0.85, 2)
    assert "https://www.ebay.co.uk/sl/prelist/suggest?sr=sh&q=" in ebay["prelist_url"]

    # Check Depop Draft
    depop = package["depop_draft"]
    assert "Carhartt" in depop["title"]
    assert depop["recommended_listing_price"] == 155.0
    assert "#carhartt" in depop["description"]
    assert "#detroitjacket" in depop["description"]
    assert len(depop["hashtags"]) >= 3

    # Check 20% Offer Target
    ofr = package["offer_engine"]
    assert ofr["asking_price"] == 30.0
    assert ofr["offer_20_price"] == 24.0
    assert ofr["offer_discount_gbp"] == 6.0
    assert ofr["ebay_profit_at_offer"] > 0
    assert ofr["depop_profit_at_offer"] > ofr["ebay_profit_at_offer"]

    # Check Visual Lens
    lens = package["visual_lens"]
    assert "https://lens.google.com/uploadbyurl?url=" in lens["google_lens_url"]


def test_ebay_verifier_enriches_with_depop_and_offer():
    """Verify ebay_verifier.verify_item returns depop_comp and offer_target."""
    res = ebay_verifier.verify_item(
        title="The North Face Nuptse 700 puffer jacket black",
        price=35.0,
        brand="The North Face",
        condition="Very Good",
        size="M",
    )
    assert res is not None
    assert res["verified"] is True
    assert "depop_comp" in res
    assert res["depop_comp"] is not None
    assert res["depop_comp"]["depop_sold_median"] >= 100.0
    assert "offer_target" in res
    assert res["offer_target"]["offer_20_price"] == 28.0
    assert res["offer_target"]["max_offer_profit"] > res["net_flip_profit"]


def test_radar_generate_draft_with_db():
    """Verify arbitrage_radar.generate_cross_listing_draft works with DB session."""
    db = SessionLocal()
    try:
        draft = arbitrage_radar.generate_cross_listing_draft(
            db=db,
            pick_data={
                "title": "Stussy 8 Ball Mohair Knit Jumper",
                "brand": "Stussy",
                "ebay_verified_model": "Stussy 8-Ball Mohair Knit / Heavy Hoodie",
                "size": "M",
                "condition": "Very Good",
                "price": 25.0,
                "ebay_sold_median": 78.0,
            }
        )
        assert draft is not None
        assert "ebay_draft" in draft
        assert "depop_draft" in draft
        assert len(draft["ebay_draft"]["title"]) <= 80
        assert "#stussy8ball" in draft["depop_draft"]["description"]
    finally:
        db.close()
