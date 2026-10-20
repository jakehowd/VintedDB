"""Item Classification & Anti-Junk Filtering Service for Vinted Searches."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set, Tuple
from rapidfuzz import fuzz

# ---------------------------------------------------------------------------
# Base Contradiction Sets (Shared across granular Vinted subcategories)
# ---------------------------------------------------------------------------

_SHOE_WORDS = [
    "sneaker", "sneakers", "trainer", "trainers", "shoe", "shoes", "boot", "boots",
    "bootie", "booties", "snowboots", "snow boot", "snow boots", "snowboot",
    "ankle boot", "ankle boots", "wellingtons", "wellies", "loafer", "loafers",
    "sandal", "sandals", "slider", "sliders", "slides", "slide", "flip flop", "flip flops",
    "heel", "heels", "stiletto", "stilettos", "slipper", "slippers", "cleat", "cleats",
    "kicks", "footwear", "clog", "clogs", "mule", "mules", "brogue", "brogues",
    "oxfords", "oxford shoes", "derby", "creepers", "espadrilles", "moccasin", "moccasins",
    "plimsoll", "plimsolls", "canvas shoes", "canvas trainers", "slip-on", "slip on",
]

_BOTTOMS_WORDS = [
    "trousers", "trouser", "pants", "track pants", "joggers", "jogger", "sweatpants",
    "chinos", "chino", "jeans", "jean", "leggings", "shorts", "skirt", "skirts",
    "culottes", "cargo", "cargos", "cargo pants", "cargo trousers",
]

_TOPS_SWEATERS_WORDS = [
    "t-shirt", "t-shirts", "tshirt", "tshirts", "tee", "tees", "polo shirt", "polo shirts",
    "tank top", "tank tops", "camisole", "crop top", "crop tops",
    "blouse", "blouses", "hoodie", "hoodies", "sweatshirt", "sweatshirts",
    "sweater", "sweaters", "jumper", "jumpers", "cardigan", "cardigans",
]

_OUTERWEAR_WORDS = [
    "jacket", "jackets", "coat", "coats", "puffer", "puffers", "parka", "parkas",
    "windbreaker", "windbreakers", "windcheater", "windcheaters", "anorak", "anoraks",
    "bomber", "bombers", "trench coat", "overcoat", "peacoat", "duffle coat",
    "gilet", "gilets", "gillet", "gillets", "body warmer", "bodywarmer", "bodywarmers",
    "shacket", "shackets",
]

_DRESSES_WORDS = [
    "dress", "dresses", "gown", "gowns", "romper", "playsuit", "jumpsuit",
    "bikini", "bikinis", "swimsuit", "swimsuits",
]

_BAGS_ACC_WORDS = [
    "backpack", "backpacks", "rucksack", "rucksacks", "handbag", "handbags",
    "tote bag", "tote bags", "shoulder bag", "crossbody bag", "bum bag",
    "duffel bag", "holdall", "wallet", "wallets", "purse", "purses", "cardholder",
    "sunglasses", "watch", "watches", "wristwatch", "belt", "belts", "necklace",
    "bracelet", "ring", "earrings", "beanie", "beanies", "snapback", "baseball cap",
    "bucket hat", "balaclava", "scarf", "scarves", "gloves",
    "sock", "socks", "underwear", "boxers", "briefs", "bra",
]


# ---------------------------------------------------------------------------
# Canonical Vinted UK Categories Taxonomy (Hierarchical: Department -> Group -> Subcategory)
# ---------------------------------------------------------------------------

VINTED_CATEGORIES: Dict[str, Dict[str, Any]] = {
    # =========================================================================
    # 1. CLOTHING -> OUTERWEAR (Vinted Catalog IDs: Men 1206, Women 1037)
    # =========================================================================
    "coats_jackets": {
        "id": "coats_jackets",
        "name": "All Outerwear (Coats & Jackets)",
        "short_name": "All Outerwear",
        "parent": "Outerwear",
        "group_id": "outerwear",
        "department": "Clothing",
        "vinted_path": "Clothing › Outerwear",
        "vinted_catalog_ids": [1206, 1037],
        "is_group_all": True,
        "keywords": [
            "jacket", "coat", "puffer", "nuptse", "parka", "windbreaker", "anorak", "bomber",
            "trench", "blouson", "gortex", "gore-tex", "beta", "alpha", "atom", "down jacket",
            "synchilla", "fleece", "gilet", "vest", "body warmer", "bodywarmer", "denali",
            "harrington", "varsity jacket", "detroit", "shacket", "overcoat", "peacoat",
            "raincoat", "mac", "duffle", "biker jacket", "leather jacket", "quilted jacket",
        ],
        "contradicting_words": _SHOE_WORDS + _BOTTOMS_WORDS + _BAGS_ACC_WORDS + [
            "t-shirt", "tshirt", "tee", "polo shirt", "tank top", "dress", "bikini", "swimsuit",
        ],
    },
    "outerwear_puffer_jackets": {
        "id": "outerwear_puffer_jackets",
        "name": "Puffer & Quilted Jackets",
        "short_name": "Puffer jackets",
        "parent": "Outerwear",
        "group_id": "outerwear",
        "department": "Clothing",
        "vinted_path": "Clothing › Outerwear › Jackets › Puffer jackets",
        "vinted_catalog_ids": [2536, 2614, 2537, 2596],
        "is_group_all": False,
        "keywords": [
            "puffer", "nuptse", "700", "down jacket", "padded jacket", "bubble jacket",
            "quilted jacket", "quilted coat", "microlight", "maya", "bady", "macmillan",
            "wyndham", "hybridge", "belay", "thorium", "cerium", "jacket", "coat",
        ],
        "contradicting_words": _SHOE_WORDS + _BOTTOMS_WORDS + _BAGS_ACC_WORDS + _TOPS_SWEATERS_WORDS + [
            "gilet", "body warmer", "bodywarmer", "sleeveless", "vest", "trench coat",
            "denim jacket", "leather jacket", "blazer", "suit", "dress",
        ],
    },
    "outerwear_fleece_jackets": {
        "id": "outerwear_fleece_jackets",
        "name": "Fleece Jackets & Sherpas",
        "short_name": "Fleece jackets",
        "parent": "Outerwear",
        "group_id": "outerwear",
        "department": "Clothing",
        "vinted_path": "Clothing › Outerwear › Jackets › Fleece jackets",
        "vinted_catalog_ids": [1858, 1086],
        "is_group_all": False,
        "keywords": [
            "fleece", "synchilla", "retro-x", "retro x", "denali", "sherpa", "borg",
            "pile jacket", "teddy jacket", "8 ball fleece", "fleece jacket", "fleece zip",
            "snap-t", "snap t", "microdini", "better sweater", "jacket", "pullover",
        ],
        "contradicting_words": _SHOE_WORDS + _BOTTOMS_WORDS + _BAGS_ACC_WORDS + [
            "gilet", "body warmer", "bodywarmer", "t-shirt", "tshirt", "tee", "polo shirt",
            "trench coat", "leather jacket", "denim jacket", "blazer", "suit", "dress",
        ],
    },
    "outerwear_windbreakers": {
        "id": "outerwear_windbreakers",
        "name": "Windbreakers, Shells & Rain Jackets",
        "short_name": "Windbreakers & Raincoats",
        "parent": "Outerwear",
        "group_id": "outerwear",
        "department": "Clothing",
        "vinted_path": "Clothing › Outerwear › Jackets › Windbreakers & Raincoats",
        "vinted_catalog_ids": [2551, 2532, 1859, 1080, 2539, 2530],
        "is_group_all": False,
        "keywords": [
            "windbreaker", "raincoat", "rain jacket", "waterproof", "shell", "hardshell",
            "softshell", "gore-tex", "goretex", "gortex", "anorak", "smock", "cagoule",
            "beta", "alpha", "zeta", "theta", "gamma", "atom", "ski jacket", "snowboard jacket",
            "mountain jacket", "berghaus", "torrentshell", "jacket", "coat",
        ],
        "contradicting_words": _SHOE_WORDS + _BOTTOMS_WORDS + _BAGS_ACC_WORDS + _TOPS_SWEATERS_WORDS + [
            "gilet", "body warmer", "bodywarmer", "vest", "denim jacket", "leather jacket",
            "blazer", "suit", "dress",
        ],
    },
    "outerwear_bomber_varsity": {
        "id": "outerwear_bomber_varsity",
        "name": "Bomber, Varsity & Harrington Jackets",
        "short_name": "Bomber, Varsity & Harrington",
        "parent": "Outerwear",
        "group_id": "outerwear",
        "department": "Clothing",
        "vinted_path": "Clothing › Outerwear › Jackets › Bomber, Varsity & Harrington",
        "vinted_catalog_ids": [1223, 1078, 1226, 2550, 2531],
        "is_group_all": False,
        "keywords": [
            "bomber", "ma-1", "ma1", "flight jacket", "varsity", "letterman",
            "baseball jacket", "harrington", "baracuta", "blouson", "coach jacket", "jacket",
        ],
        "contradicting_words": _SHOE_WORDS + _BOTTOMS_WORDS + _BAGS_ACC_WORDS + _TOPS_SWEATERS_WORDS + [
            "gilet", "body warmer", "bodywarmer", "vest", "dress",
        ],
    },
    "outerwear_denim_utility": {
        "id": "outerwear_denim_utility",
        "name": "Denim, Field, Utility & Work Jackets",
        "short_name": "Denim, Field & Utility Jackets",
        "parent": "Outerwear",
        "group_id": "outerwear",
        "department": "Clothing",
        "vinted_path": "Clothing › Outerwear › Jackets › Field, Utility & Denim",
        "vinted_catalog_ids": [1224, 1079, 2535, 2528, 2538, 2529],
        "is_group_all": False,
        "keywords": [
            "detroit", "michigan", "active jacket", "santa fe", "chore", "work jacket",
            "utility jacket", "field jacket", "military jacket", "m65", "m-65", "overshirt",
            "shacket", "denim jacket", "jean jacket", "trucker jacket", "wax jacket",
            "barbour", "bedale", "beaufort", "ashby", "canvas jacket", "jacket",
        ],
        "contradicting_words": _SHOE_WORDS + _BOTTOMS_WORDS + _BAGS_ACC_WORDS + _TOPS_SWEATERS_WORDS + [
            "gilet", "body warmer", "bodywarmer", "dress", "bikini",
        ],
    },
    "outerwear_biker_leather": {
        "id": "outerwear_biker_leather",
        "name": "Biker, Racer & Leather Jackets",
        "short_name": "Biker & Leather Jackets",
        "parent": "Outerwear",
        "group_id": "outerwear",
        "department": "Clothing",
        "vinted_path": "Clothing › Outerwear › Jackets › Biker & racer jackets",
        "vinted_catalog_ids": [2534, 2527],
        "is_group_all": False,
        "keywords": [
            "leather jacket", "biker jacket", "racer jacket", "moto jacket", "motorcycle jacket",
            "suede jacket", "shearling jacket", "aviator jacket", "schott", "belstaff", "jacket",
        ],
        "contradicting_words": _SHOE_WORDS + _BOTTOMS_WORDS + _BAGS_ACC_WORDS + _TOPS_SWEATERS_WORDS + [
            "gilet", "body warmer", "dress",
        ],
    },
    "outerwear_gilets": {
        "id": "outerwear_gilets",
        "name": "Gilets & Body Warmers",
        "short_name": "Gilets & body warmers",
        "parent": "Outerwear",
        "group_id": "outerwear",
        "department": "Clothing",
        "vinted_path": "Clothing › Outerwear › Gilets & body warmers",
        "vinted_catalog_ids": [2553, 2524],
        "is_group_all": False,
        "keywords": [
            "gilet", "body warmer", "bodywarmer", "puffer vest", "down vest", "fleece vest",
            "utility vest", "tactical vest", "sleeveless jacket", "nuptse gilet", "vest",
        ],
        "contradicting_words": _SHOE_WORDS + _BOTTOMS_WORDS + _BAGS_ACC_WORDS + _TOPS_SWEATERS_WORDS + [
            "trench coat", "overcoat", "parka", "dress",
        ],
    },
    "outerwear_coats_parkas": {
        "id": "outerwear_coats_parkas",
        "name": "Coats, Parkas, Trench & Overcoats",
        "short_name": "Coats, Parkas & Trench",
        "parent": "Outerwear",
        "group_id": "outerwear",
        "department": "Clothing",
        "vinted_path": "Clothing › Outerwear › Coats",
        "vinted_catalog_ids": [2051, 1907, 1227, 1087, 1230, 1834, 2533, 2526, 1225, 2525, 1861, 1076],
        "is_group_all": False,
        "keywords": [
            "coat", "parka", "trench", "trench coat", "overcoat", "long coat", "wool coat",
            "peacoat", "pea coat", "duffle coat", "mac", "mackintosh", "car coat",
            "faux fur coat", "winter coat", "down coat",
        ],
        "contradicting_words": _SHOE_WORDS + _BOTTOMS_WORDS + _BAGS_ACC_WORDS + _TOPS_SWEATERS_WORDS + [
            "gilet", "body warmer", "bodywarmer", "vest", "dress",
        ],
    },

    # =========================================================================
    # 2. CLOTHING -> JUMPERS & SWEATERS (Vinted Catalog IDs: Men 79, Women 13)
    # =========================================================================
    "jumpers_all": {
        "id": "jumpers_all",
        "name": "All Jumpers, Hoodies & Sweaters",
        "short_name": "All Jumpers & sweaters",
        "parent": "Jumpers & sweaters",
        "group_id": "jumpers_sweaters",
        "department": "Clothing",
        "vinted_path": "Clothing › Jumpers & sweaters",
        "vinted_catalog_ids": [79, 13],
        "is_group_all": True,
        "keywords": [
            "hoodie", "hoody", "sweatshirt", "sweater", "jumper", "knit", "knitwear",
            "quarter zip", "1/4 zip", "half zip", "1/2 zip", "cardigan", "pullover",
            "turtleneck", "roll neck", "crewneck", "crew neck", "v-neck", "zip hoodie",
            "fleece",
        ],
        "contradicting_words": _SHOE_WORDS + _BOTTOMS_WORDS + _BAGS_ACC_WORDS + _OUTERWEAR_WORDS + _DRESSES_WORDS + [
            "t-shirt", "t-shirts", "tshirt", "tshirts", "tee", "tees", "polo shirt", "polo shirts", "tank top", "tank tops",
        ],
    },
    "hoodies_sweatshirts": {
        "id": "hoodies_sweatshirts",
        "name": "Hoodies & Sweatshirts",
        "short_name": "Hoodies & sweatshirts",
        "parent": "Jumpers & sweaters",
        "group_id": "jumpers_sweaters",
        "department": "Clothing",
        "vinted_path": "Clothing › Jumpers & sweaters › Hoodies & sweatshirts",
        "vinted_catalog_ids": [267, 196, 1812],
        "is_group_all": False,
        "keywords": [
            "hoodie", "sweatshirt", "hoody", "zip hoodie", "bape hoodie", "shark hoodie",
            "tracksuit top", "crewneck sweatshirt", "crew neck sweatshirt", "pullover hoodie",
            "oversized hoodie", "fleece hoodie", "sweater", "jumper",
        ],
        "contradicting_words": _SHOE_WORDS + _BOTTOMS_WORDS + _BAGS_ACC_WORDS + _OUTERWEAR_WORDS + _DRESSES_WORDS + [
            "t-shirt", "t-shirts", "tshirt", "tshirts", "tee", "tees", "polo shirt", "polo shirts",
        ],
    },
    "jumpers_zip_hoodies": {
        "id": "jumpers_zip_hoodies",
        "name": "Zip-Through Hoodies & Sweaters",
        "short_name": "Zip-through hoodies",
        "parent": "Jumpers & sweaters",
        "group_id": "jumpers_sweaters",
        "department": "Clothing",
        "vinted_path": "Clothing › Jumpers & sweaters › Zip-through hoodies & sweaters",
        "vinted_catalog_ids": [1812, 196],
        "is_group_all": False,
        "keywords": [
            "zip hoodie", "zip up hoodie", "zip-up hoodie", "zip through hoodie",
            "full zip hoodie", "shark hoodie", "bape hoodie", "hoodie", "track top",
            "zip sweatshirt", "zip sweater", "zip jumper",
        ],
        "contradicting_words": _SHOE_WORDS + _BOTTOMS_WORDS + _BAGS_ACC_WORDS + _OUTERWEAR_WORDS + _DRESSES_WORDS + [
            "t-shirt", "t-shirts", "tshirt", "tshirts", "tee", "tees", "polo shirt", "polo shirts",
        ],
    },
    "sweaters_knitwear": {
        "id": "sweaters_knitwear",
        "name": "Jumpers & Knitwear (Crew, V-Neck, Knitted)",
        "short_name": "Jumpers & Knitwear",
        "parent": "Jumpers & sweaters",
        "group_id": "jumpers_sweaters",
        "department": "Clothing",
        "vinted_path": "Clothing › Jumpers & sweaters › Jumpers",
        "vinted_catalog_ids": [1811, 1917, 1813, 264, 190, 529, 1815],
        "is_group_all": False,
        "keywords": [
            "sweater", "jumper", "knit", "knitwear", "quarter zip", "1/4 zip", "half zip",
            "1/2 zip", "cardigan", "pullover", "turtleneck", "crewneck", "crew neck",
            "v-neck", "v neck", "cable knit", "chunky knit", "wool", "cashmere", "merino",
            "lambswool", "knitted", "sweatshirt", "synchilla", "fleece",
        ],
        "contradicting_words": _SHOE_WORDS + _BOTTOMS_WORDS + _BAGS_ACC_WORDS + _OUTERWEAR_WORDS + _DRESSES_WORDS + [
            "t-shirt", "t-shirts", "tshirt", "tshirts", "tee", "tees", "tank top", "tank tops",
        ],
    },
    "jumpers_quarter_zip": {
        "id": "jumpers_quarter_zip",
        "name": "Quarter-Zip & Half-Zip Jumpers",
        "short_name": "Quarter-zip jumpers",
        "parent": "Jumpers & sweaters",
        "group_id": "jumpers_sweaters",
        "department": "Clothing",
        "vinted_path": "Clothing › Jumpers & sweaters › Quarter-zip & Half-zip",
        "vinted_catalog_ids": [1811, 1812, 1917],
        "is_group_all": False,
        "keywords": [
            "quarter zip", "1/4 zip", "half zip", "1/2 zip", "zip neck", "zip-neck",
            "funnel neck", "mock neck", "pullover", "jumper", "sweater", "sweatshirt", "fleece",
        ],
        "contradicting_words": _SHOE_WORDS + _BOTTOMS_WORDS + _BAGS_ACC_WORDS + _OUTERWEAR_WORDS + _DRESSES_WORDS + [
            "t-shirt", "t-shirts", "tshirt", "tshirts", "tee", "tees", "tank top", "tank tops",
        ],
    },
    "jumpers_cardigans": {
        "id": "jumpers_cardigans",
        "name": "Cardigans, Turtlenecks & Knitted Vests",
        "short_name": "Cardigans & Turtlenecks",
        "parent": "Jumpers & sweaters",
        "group_id": "jumpers_sweaters",
        "department": "Clothing",
        "vinted_path": "Clothing › Jumpers & sweaters › Cardigans & Turtlenecks",
        "vinted_catalog_ids": [266, 194, 265, 191, 1825, 1874],
        "is_group_all": False,
        "keywords": [
            "cardigan", "button knit", "mohair cardigan", "zip cardigan", "turtleneck",
            "roll neck", "polo neck", "sweater vest", "knitted vest", "sleeveless jumper",
            "tank jumper", "bolero", "jumper", "sweater", "knit",
        ],
        "contradicting_words": _SHOE_WORDS + _BOTTOMS_WORDS + _BAGS_ACC_WORDS + _OUTERWEAR_WORDS + _DRESSES_WORDS + [
            "t-shirt", "t-shirts", "tshirt", "tshirts", "tee", "tees",
        ],
    },

    # =========================================================================
    # 3. CLOTHING -> TOPS & T-SHIRTS (Vinted Catalog IDs: Men 76, Women 12)
    # =========================================================================
    "tshirts_tops": {
        "id": "tshirts_tops",
        "name": "All Tops, T-Shirts, Shirts & Polos",
        "short_name": "All Tops & t-shirts",
        "parent": "Tops & t-shirts",
        "group_id": "tops_tshirts",
        "department": "Clothing",
        "vinted_path": "Clothing › Tops & t-shirts",
        "vinted_catalog_ids": [76, 12],
        "is_group_all": True,
        "keywords": [
            "t-shirt", "tshirt", "tee", "shirt", "polo", "polo shirt", "top", "tank top",
            "jersey", "graphic tee", "vintage tee", "henley", "long sleeve", "long-sleeve",
            "button up", "button down", "oxford shirt", "flannel shirt", "blouse", "cami",
            "crop top", "vest top", "bodysuit", "tunic",
        ],
        "contradicting_words": _SHOE_WORDS + _BOTTOMS_WORDS + _BAGS_ACC_WORDS + _OUTERWEAR_WORDS + _DRESSES_WORDS + [
            "hoodie", "hoodies", "cardigan", "cardigans", "jumper", "jumpers", "sweater", "sweaters",
            "sweatshirt", "sweatshirts", "fleece", "fleeces", "tracksuit", "tracksuits",
        ],
    },
    "tops_tshirts_only": {
        "id": "tops_tshirts_only",
        "name": "T-Shirts & Henleys (Plain, Print, Long-Sleeve)",
        "short_name": "T-shirts",
        "parent": "Tops & t-shirts",
        "group_id": "tops_tshirts",
        "department": "Clothing",
        "vinted_path": "Clothing › Tops & t-shirts › T-shirts",
        "vinted_catalog_ids": [77, 221, 1806, 1807, 1808, 1810],
        "is_group_all": False,
        "keywords": [
            "t-shirt", "tshirt", "tee", "graphic tee", "print tee", "striped tee",
            "long sleeve tee", "long-sleeved t-shirt", "henley", "baby tee", "top", "jersey",
        ],
        "contradicting_words": _SHOE_WORDS + _BOTTOMS_WORDS + _BAGS_ACC_WORDS + _OUTERWEAR_WORDS + _DRESSES_WORDS + [
            "hoodie", "hoodies", "cardigan", "cardigans", "jumper", "jumpers", "sweater", "sweaters",
            "sweatshirt", "sweatshirts", "fleece", "fleeces", "flannel shirt", "oxford shirt", "tracksuit", "tracksuits",
        ],
    },
    "tops_shirts": {
        "id": "tops_shirts",
        "name": "Shirts (Checked, Oxford, Denim, Linen, Button-Up)",
        "short_name": "Shirts",
        "parent": "Tops & t-shirts",
        "group_id": "tops_tshirts",
        "department": "Clothing",
        "vinted_path": "Clothing › Tops & t-shirts › Shirts",
        "vinted_catalog_ids": [536, 222, 1801, 1802, 1803, 1804, 1805],
        "is_group_all": False,
        "keywords": [
            "shirt", "button up", "button-up", "button down", "button-down", "oxford shirt",
            "checked shirt", "plaid shirt", "flannel", "denim shirt", "linen shirt",
            "corduroy shirt", "striped shirt", "short sleeve shirt", "overshirt", "blouse",
        ],
        "contradicting_words": _SHOE_WORDS + _BOTTOMS_WORDS + _BAGS_ACC_WORDS + _OUTERWEAR_WORDS + _DRESSES_WORDS + [
            "hoodie", "sweatshirt", "tank top", "crop top",
        ],
    },
    "tops_polos": {
        "id": "tops_polos",
        "name": "Polo Shirts & Rugby Shirts",
        "short_name": "Polo shirts",
        "parent": "Tops & t-shirts",
        "group_id": "tops_tshirts",
        "department": "Clothing",
        "vinted_path": "Clothing › Tops & t-shirts › Polo shirts",
        "vinted_catalog_ids": [5492, 76],
        "is_group_all": False,
        "keywords": [
            "polo", "polo shirt", "polo top", "rugby shirt", "rugby polo", "long sleeve polo",
            "pique polo", "mesh polo", "collared shirt", "shirt", "top",
        ],
        "contradicting_words": _SHOE_WORDS + _BOTTOMS_WORDS + _BAGS_ACC_WORDS + _OUTERWEAR_WORDS + [
            "hoodie", "cardigan", "tank top", "crop top", "dress",
        ],
    },
    "tops_vests_blouses": {
        "id": "tops_vests_blouses",
        "name": "Vests, Tank Tops, Blouses, Camis & Crop Tops",
        "short_name": "Vests, Blouses & Crop tops",
        "parent": "Tops & t-shirts",
        "group_id": "tops_tshirts",
        "department": "Clothing",
        "vinted_path": "Clothing › Tops & t-shirts › Vests, Blouses & Camis",
        "vinted_catalog_ids": [560, 534, 1043, 14, 1041, 1835],
        "is_group_all": False,
        "keywords": [
            "vest", "vest top", "tank top", "sleeveless", "singlet", "camisole", "cami",
            "crop top", "blouse", "bodysuit", "halterneck", "bandeau", "tube top", "tunic", "top",
        ],
        "contradicting_words": _SHOE_WORDS + _BOTTOMS_WORDS + _BAGS_ACC_WORDS + [
            "jacket", "coat", "puffer", "parka", "hoodie",
        ],
    },

    # =========================================================================
    # 4. CLOTHING -> JEANS (Vinted Catalog IDs: Men 257, Women 183)
    # =========================================================================
    "jeans": {
        "id": "jeans",
        "name": "All Jeans (Straight, Baggy, Slim, Skinny, Ripped)",
        "short_name": "All Jeans",
        "parent": "Jeans",
        "group_id": "jeans_group",
        "department": "Clothing",
        "vinted_path": "Clothing › Jeans",
        "vinted_catalog_ids": [257, 183],
        "is_group_all": True,
        "keywords": [
            "jeans", "denim", "levis", "levi's", "501", "505", "550", "560", "511",
            "baggy jeans", "wide leg jeans", "straight jeans", "slim jeans", "skinny jeans",
            "flared jeans", "bootcut jeans", "ripped jeans", "raw denim", "selvedge",
            "carpenter jeans", "skater jeans", "boyfriend jeans", "mom jeans",
        ],
        "contradicting_words": _SHOE_WORDS + _BAGS_ACC_WORDS + _OUTERWEAR_WORDS + _TOPS_SWEATERS_WORDS + [
            "denim jacket", "jean jacket", "trucker jacket", "denim shirt", "shorts", "skirt", "dress",
        ],
    },
    "jeans_straight_baggy": {
        "id": "jeans_straight_baggy",
        "name": "Straight, Baggy, Wide-Leg & Flared Jeans",
        "short_name": "Straight & Baggy jeans",
        "parent": "Jeans",
        "group_id": "jeans_group",
        "department": "Clothing",
        "vinted_path": "Clothing › Jeans › Straight, Baggy & Flared",
        "vinted_catalog_ids": [1819, 1845, 1839, 1841],
        "is_group_all": False,
        "keywords": [
            "straight jeans", "straight fit", "straight leg", "baggy jeans", "wide leg jeans",
            "loose fit jeans", "relaxed jeans", "carpenter jeans", "skater jeans",
            "flared jeans", "bootcut jeans", "boyfriend jeans", "mom jeans", "501", "550", "569",
            "polar big boy", "jnco", "jeans", "denim",
        ],
        "contradicting_words": _SHOE_WORDS + _BAGS_ACC_WORDS + _OUTERWEAR_WORDS + _TOPS_SWEATERS_WORDS + [
            "skinny jeans", "super skinny", "spray on", "denim jacket", "shorts", "skirt", "dress",
        ],
    },
    "jeans_slim_skinny": {
        "id": "jeans_slim_skinny",
        "name": "Slim, Skinny & Ripped Jeans",
        "short_name": "Slim, Skinny & Ripped jeans",
        "parent": "Jeans",
        "group_id": "jeans_group",
        "department": "Clothing",
        "vinted_path": "Clothing › Jeans › Slim, Skinny & Ripped",
        "vinted_catalog_ids": [1817, 1818, 1816, 1844, 1843],
        "is_group_all": False,
        "keywords": [
            "slim jeans", "slim fit", "skinny jeans", "ripped jeans", "distressed jeans",
            "tapered jeans", "511", "512", "sullivan", "jeans", "denim",
        ],
        "contradicting_words": _SHOE_WORDS + _BAGS_ACC_WORDS + _OUTERWEAR_WORDS + _TOPS_SWEATERS_WORDS + [
            "denim jacket", "shorts", "skirt", "dress",
        ],
    },

    # =========================================================================
    # 5. CLOTHING -> TROUSERS & LEGGINGS (Vinted Catalog IDs: Men 34, Women 9)
    # =========================================================================
    "trousers_pants": {
        "id": "trousers_pants",
        "name": "All Trousers, Joggers, Cargos & Shorts",
        "short_name": "All Trousers & Shorts",
        "parent": "Trousers & Shorts",
        "group_id": "trousers_group",
        "department": "Clothing",
        "vinted_path": "Clothing › Trousers & Shorts",
        "vinted_catalog_ids": [34, 9, 80, 15],
        "is_group_all": True,
        "keywords": [
            "trousers", "pants", "track pants", "tracksuit bottoms", "joggers", "sweatpants",
            "cargo", "cargos", "cargo pants", "cargo trousers", "chinos", "tailored trousers",
            "wide leg trousers", "pleated trousers", "work pants", "double knee", "leggings",
            "shorts", "cargo shorts", "denim shorts", "chino shorts", "sweat shorts",
        ],
        "contradicting_words": _SHOE_WORDS + _BAGS_ACC_WORDS + _OUTERWEAR_WORDS + _TOPS_SWEATERS_WORDS + [
            "dress", "skirt",
        ],
    },
    "trousers_joggers": {
        "id": "trousers_joggers",
        "name": "Joggers, Sweatpants & Track Pants",
        "short_name": "Joggers & Track pants",
        "parent": "Trousers & Shorts",
        "group_id": "trousers_group",
        "department": "Clothing",
        "vinted_path": "Clothing › Trousers › Joggers & Track pants",
        "vinted_catalog_ids": [1821, 583, 573],
        "is_group_all": False,
        "keywords": [
            "joggers", "sweatpants", "track pants", "tracksuit bottoms", "trackies",
            "fleece pants", "tech fleece bottoms", "cuffed pants", "jogging bottoms", "pants", "trousers",
        ],
        "contradicting_words": _SHOE_WORDS + _BAGS_ACC_WORDS + _OUTERWEAR_WORDS + _TOPS_SWEATERS_WORDS + [
            "shorts", "jeans", "skirt", "dress",
        ],
    },
    "trousers_cargos": {
        "id": "trousers_cargos",
        "name": "Cargo, Utility, Work & Wide-Leg Trousers",
        "short_name": "Cargo & Wide-leg trousers",
        "parent": "Trousers & Shorts",
        "group_id": "trousers_group",
        "department": "Clothing",
        "vinted_path": "Clothing › Trousers › Cargo & Wide-leg trousers",
        "vinted_catalog_ids": [260, 1071, 263],
        "is_group_all": False,
        "keywords": [
            "cargo", "cargos", "cargo pants", "cargo trousers", "combat trousers",
            "parachute pants", "carpenter pants", "double knee", "work pants", "utility pants",
            "wide leg trousers", "baggy trousers", "nylon pants", "trousers", "pants",
        ],
        "contradicting_words": _SHOE_WORDS + _BAGS_ACC_WORDS + _OUTERWEAR_WORDS + _TOPS_SWEATERS_WORDS + [
            "shorts", "cargo shorts", "skirt", "dress",
        ],
    },
    "trousers_chinos_tailored": {
        "id": "trousers_chinos_tailored",
        "name": "Chinos, Tailored, Pleated & Smart Trousers",
        "short_name": "Chinos & Tailored trousers",
        "parent": "Trousers & Shorts",
        "group_id": "trousers_group",
        "department": "Clothing",
        "vinted_path": "Clothing › Trousers › Chinos & Tailored trousers",
        "vinted_catalog_ids": [1820, 261, 1070, 187],
        "is_group_all": False,
        "keywords": [
            "chinos", "chino trousers", "tailored trousers", "smart trousers", "dress pants",
            "suit trousers", "pleated trousers", "slacks", "corduroy trousers", "linen trousers",
            "cropped trousers", "dickies 874", "trousers", "pants",
        ],
        "contradicting_words": _SHOE_WORDS + _BAGS_ACC_WORDS + _OUTERWEAR_WORDS + _TOPS_SWEATERS_WORDS + [
            "shorts", "joggers", "sweatpants", "skirt", "dress",
        ],
    },
    "shorts_all": {
        "id": "shorts_all",
        "name": "Shorts (Cargo, Denim, Chino, Jersey & Swim)",
        "short_name": "Shorts",
        "parent": "Trousers & Shorts",
        "group_id": "trousers_group",
        "department": "Clothing",
        "vinted_path": "Clothing › Shorts",
        "vinted_catalog_ids": [80, 15, 1822, 1823, 1824, 538, 1103, 84],
        "is_group_all": False,
        "keywords": [
            "shorts", "cargo shorts", "denim shorts", "jorts", "chino shorts", "jersey shorts",
            "sweat shorts", "running shorts", "gym shorts", "swim shorts", "board shorts",
            "baggies", "bermuda shorts",
        ],
        "contradicting_words": _SHOE_WORDS + _BAGS_ACC_WORDS + _OUTERWEAR_WORDS + _TOPS_SWEATERS_WORDS + [
            "trousers", "jeans", "joggers", "sweatpants", "dress",
        ],
    },

    # =========================================================================
    # 6. CLOTHING -> ACTIVEWEAR, SUITS, DRESSES & SKIRTS
    # =========================================================================
    "activewear_tracksuits": {
        "id": "activewear_tracksuits",
        "name": "Tracksuits, Activewear & Sports Jerseys",
        "short_name": "Activewear & Tracksuits",
        "parent": "Activewear & Tailoring",
        "group_id": "activewear_tailoring",
        "department": "Clothing",
        "vinted_path": "Clothing › Activewear › Tracksuits & Jerseys",
        "vinted_catalog_ids": [30, 73, 582, 572, 3267, 3268],
        "is_group_all": False,
        "keywords": [
            "tracksuit", "full tracksuit", "two piece", "co-ord", "tech fleece", "track jacket",
            "track top", "football shirt", "football jersey", "soccer jersey", "basketball jersey",
            "team shirt", "training top", "drill top", "quarter zip training", "activewear",
            "gym wear", "sports bra", "running top",
        ],
        "contradicting_words": _SHOE_WORDS + _BAGS_ACC_WORDS + [
            "jeans", "trench coat", "formal suit", "dress",
        ],
    },
    "suits_blazers": {
        "id": "suits_blazers",
        "name": "Suits, Blazers, Tuxedos & Waistcoats",
        "short_name": "Suits & blazers",
        "parent": "Activewear & Tailoring",
        "group_id": "activewear_tailoring",
        "department": "Clothing",
        "vinted_path": "Clothing › Suits & blazers",
        "vinted_catalog_ids": [32, 8, 1786, 532, 1788],
        "is_group_all": False,
        "keywords": [
            "suit", "blazer", "suit jacket", "dinner jacket", "tuxedo", "waistcoat",
            "two piece suit", "three piece suit", "tailored jacket", "sports jacket",
        ],
        "contradicting_words": _SHOE_WORDS + _BAGS_ACC_WORDS + [
            "hoodie", "sweatshirt", "joggers", "tracksuit", "shorts", "t-shirt",
        ],
    },
    "dresses_skirts": {
        "id": "dresses_skirts",
        "name": "Dresses, Skirts & Jumpsuits",
        "short_name": "Dresses & Skirts",
        "parent": "Activewear & Tailoring",
        "group_id": "activewear_tailoring",
        "department": "Clothing",
        "vinted_path": "Clothing › Dresses, Skirts & Jumpsuits",
        "vinted_catalog_ids": [10, 11, 1035],
        "is_group_all": False,
        "keywords": [
            "dress", "mini dress", "midi dress", "maxi dress", "summer dress", "slip dress",
            "party dress", "evening dress", "gown", "skirt", "mini skirt", "midi skirt",
            "maxi skirt", "pleated skirt", "denim skirt", "skort", "jumpsuit", "playsuit", "romper",
        ],
        "contradicting_words": _SHOE_WORDS + _BAGS_ACC_WORDS + _OUTERWEAR_WORDS + [
            "hoodie", "sweatshirt", "joggers", "jeans", "trousers",
        ],
    },

    # =========================================================================
    # 7. SHOES -> TRAINERS, BOOTS, LOAFERS, CLOGS & SANDALS
    # =========================================================================
    "shoes_sneakers": {
        "id": "shoes_sneakers",
        "name": "Trainers & Sneakers",
        "short_name": "Trainers",
        "parent": "Shoes & Footwear",
        "group_id": "shoes_group",
        "department": "Shoes",
        "vinted_path": "Shoes › Trainers",
        "vinted_catalog_ids": [1242, 2632, 1452, 2630],
        "is_group_all": False,
        "keywords": [
            "gat", "gats", "replica sneaker", "sneaker", "sneakers", "trainer", "trainers",
            "runner", "runners", "dunk", "dunks", "jordan", "samba", "sambas", "gazelle",
            "gazelles", "spezial", "campus", "bapesta", "ramones", "geobasket", "990", "991",
            "992", "993", "2002r", "1906r", "550", "air force", "air max", "tn", "shox",
            "vomero", "yeezy", "speedcross", "asics", "gel-kayano", "gel-1130", "gel-nyc",
            "salomon", "xt-6", "xt6", "acs pro", "hoka", "on cloud", "chuck taylor", "all star",
            "vans", "sk8-hi", "old skool", "knu skool", "new balance", "shoe", "shoes", "kicks", "footwear",
        ],
        "contradicting_words": _OUTERWEAR_WORDS + _TOPS_SWEATERS_WORDS + _BOTTOMS_WORDS + _BAGS_ACC_WORDS + [
            "box only", "empty box", "shoelaces", "laces only", "poster", "keychain",
        ],
    },
    "shoes_sports_running": {
        "id": "shoes_sports_running",
        "name": "Sports, Running, Hiking & Football Shoes",
        "short_name": "Sports & Running shoes",
        "parent": "Shoes & Footwear",
        "group_id": "shoes_group",
        "department": "Shoes",
        "vinted_path": "Shoes › Sports shoes",
        "vinted_catalog_ids": [1452, 2630, 1453, 2651, 2678, 2645, 2676, 2643],
        "is_group_all": False,
        "keywords": [
            "running shoes", "trail shoes", "hiking shoes", "hiking boots", "walking boots",
            "climbing shoes", "football boots", "astro", "cleats", "basketball shoes",
            "tennis shoes", "golf shoes", "cycling shoes", "gym shoes", "metcon", "alphafly",
            "vaporfly", "pegasus", "speedcross", "xt-6", "salomon", "hoka", "brooks", "asics",
            "trainer", "trainers", "shoes",
        ],
        "contradicting_words": _OUTERWEAR_WORDS + _TOPS_SWEATERS_WORDS + _BOTTOMS_WORDS + _BAGS_ACC_WORDS,
    },
    "shoes_boots": {
        "id": "shoes_boots",
        "name": "Boots (Chelsea, Desert, Lace-Up, Ankle & Work)",
        "short_name": "Boots",
        "parent": "Shoes & Footwear",
        "group_id": "shoes_group",
        "department": "Shoes",
        "vinted_path": "Shoes › Boots",
        "vinted_catalog_ids": [1233, 1049, 2661, 2662, 2664, 2618],
        "is_group_all": False,
        "keywords": [
            "boot", "boots", "chelsea", "chelsea boot", "combat boot", "lace-up boot",
            "desert boot", "chukka", "tabi boot", "timberland", "6 inch boot", "dr martens",
            "doc martens", "1460", "jadon", "solovair", "red wing", "blundstone", "uggs",
            "ugg", "ankle boot", "work boot", "wellington", "wellies", "hunter", "cowboy boot",
            "biker boot", "snow boot",
        ],
        "contradicting_words": _OUTERWEAR_WORDS + _TOPS_SWEATERS_WORDS + _BOTTOMS_WORDS + _BAGS_ACC_WORDS,
    },
    "shoes_loafers": {
        "id": "shoes_loafers",
        "name": "Boat Shoes, Loafers, Wallabees & Formal Shoes",
        "short_name": "Loafers, Boat & Formal shoes",
        "parent": "Shoes & Footwear",
        "group_id": "shoes_group",
        "department": "Shoes",
        "vinted_path": "Shoes › Loafers, Boat & Formal shoes",
        "vinted_catalog_ids": [2656, 2954, 1238, 2951, 2970, 2623],
        "is_group_all": False,
        "keywords": [
            "loafer", "loafers", "penny loafer", "tassel loafer", "gucci loafer",
            "boat shoe", "boat shoes", "deck shoe", "timberland 3 eye", "moccasin", "moccasins",
            "wallabee", "wallabees", "clarks originals", "desert trek", "derby", "derbies",
            "oxford", "oxfords", "brogue", "brogues", "monk strap", "dress shoes", "formal shoes",
            "mary jane", "ballerinas", "flats", "mule", "mules", "clog", "clogs",
            "birkenstock", "birk", "boston", "bostons", "shoes",
        ],
        "contradicting_words": _OUTERWEAR_WORDS + _TOPS_SWEATERS_WORDS + _BOTTOMS_WORDS + _BAGS_ACC_WORDS,
    },
    "shoes_clogs_mules": {
        "id": "shoes_clogs_mules",
        "name": "Clogs, Mules, Sandals & Slides",
        "short_name": "Clogs, Mules & Sandals",
        "parent": "Shoes & Footwear",
        "group_id": "shoes_group",
        "department": "Shoes",
        "vinted_path": "Shoes › Clogs, Mules & Sandals",
        "vinted_catalog_ids": [2970, 2623, 2968, 2949, 2969, 2952, 2659, 215],
        "is_group_all": False,
        "keywords": [
            "clog", "clogs", "mule", "mules", "birkenstock", "birk", "boston", "bostons",
            "arizona", "sandal", "sandals", "slide", "slides", "sliders", "flip flop",
            "flip-flops", "yeezy slide", "foam runner", "crocs", "tasman", "tazz", "slipper", "slippers",
        ],
        "contradicting_words": _OUTERWEAR_WORDS + _TOPS_SWEATERS_WORDS + _BOTTOMS_WORDS + _BAGS_ACC_WORDS,
    },

    # =========================================================================
    # 8. BAGS -> BACKPACKS, SHOULDER BAGS, TOTE BAGS, DUFFELS & WALLETS
    # =========================================================================
    "bags_backpacks": {
        "id": "bags_backpacks",
        "name": "All Bags, Backpacks, Totes & Wallets",
        "short_name": "All Bags & Backpacks",
        "parent": "Bags &Luggage",
        "group_id": "bags_group",
        "department": "Bags",
        "vinted_path": "Bags › All Bags & Wallets",
        "vinted_catalog_ids": [94, 19],
        "is_group_all": True,
        "keywords": [
            "bag", "backpack", "rucksack", "tote", "tote bag", "duffle", "duffel", "holdall",
            "shoulder bag", "crossbody", "crossbody bag", "messenger", "messenger bag",
            "satchel", "bum bag", "waist bag", "fanny pack", "sling bag", "side bag",
            "handbag", "clutch", "hobo bag", "bucket bag", "gym bag", "wallet", "purse",
            "cardholder", "card holder", "pouch", "washbag",
        ],
        "contradicting_words": _SHOE_WORDS + _OUTERWEAR_WORDS + _TOPS_SWEATERS_WORDS + _BOTTOMS_WORDS,
    },
    "bags_backpacks_only": {
        "id": "bags_backpacks_only",
        "name": "Backpacks & Rucksacks",
        "short_name": "Backpacks",
        "parent": "Bags &Luggage",
        "group_id": "bags_group",
        "department": "Bags",
        "vinted_path": "Bags › Backpacks",
        "vinted_catalog_ids": [246, 157],
        "is_group_all": False,
        "keywords": [
            "backpack", "rucksack", "daypack", "bookbag", "school bag", "laptop bag",
            "hiking pack", "borealis", "jester", "recon", "mantis", "bag",
        ],
        "contradicting_words": _SHOE_WORDS + _OUTERWEAR_WORDS + _TOPS_SWEATERS_WORDS + _BOTTOMS_WORDS + [
            "wallet", "cardholder", "coin purse",
        ],
    },
    "bags_shoulder_crossbody": {
        "id": "bags_shoulder_crossbody",
        "name": "Shoulder, Crossbody, Messenger, Tote & Handbags",
        "short_name": "Shoulder, Crossbody & Totes",
        "parent": "Bags &Luggage",
        "group_id": "bags_group",
        "department": "Bags",
        "vinted_path": "Bags › Shoulder, Crossbody, Tote & Handbags",
        "vinted_catalog_ids": [247, 158, 1797, 1784, 552, 156, 2942, 2945],
        "is_group_all": False,
        "keywords": [
            "shoulder bag", "crossbody", "crossbody bag", "side bag", "sling bag",
            "messenger", "messenger bag", "satchel", "tote", "tote bag", "shopper",
            "handbag", "hobo bag", "bucket bag", "baguette bag", "clutch", "bag",
        ],
        "contradicting_words": _SHOE_WORDS + _OUTERWEAR_WORDS + _TOPS_SWEATERS_WORDS + _BOTTOMS_WORDS,
    },
    "bags_bumbags_duffels": {
        "id": "bags_bumbags_duffels",
        "name": "Bum Bags, Holdalls, Gym & Duffel Bags",
        "short_name": "Bum bags & Holdalls",
        "parent": "Bags &Luggage",
        "group_id": "bags_group",
        "department": "Bags",
        "vinted_path": "Bags › Bum bags, Holdalls & Gym bags",
        "vinted_catalog_ids": [1799, 1848, 1798, 1849, 2961, 2944],
        "is_group_all": False,
        "keywords": [
            "bum bag", "bumbag", "waist bag", "fanny pack", "belt bag", "flight bag",
            "holdall", "duffel", "duffle", "gym bag", "barrel bag", "weekender",
            "travel bag", "base camp", "luggage", "suitcase", "bag",
        ],
        "contradicting_words": _SHOE_WORDS + _OUTERWEAR_WORDS + _TOPS_SWEATERS_WORDS + _BOTTOMS_WORDS,
    },
    "bags_wallets_purses": {
        "id": "bags_wallets_purses",
        "name": "Wallets, Cardholders & Purses",
        "short_name": "Wallets & Purses",
        "parent": "Bags &Luggage",
        "group_id": "bags_group",
        "department": "Bags",
        "vinted_path": "Bags › Wallets & purses",
        "vinted_catalog_ids": [248, 160],
        "is_group_all": False,
        "keywords": [
            "wallet", "cardholder", "card holder", "purse", "coin purse", "billfold",
            "bifold", "money clip", "passport holder", "pouch", "wristlet",
        ],
        "contradicting_words": _SHOE_WORDS + _OUTERWEAR_WORDS + _TOPS_SWEATERS_WORDS + _BOTTOMS_WORDS + [
            "backpack", "rucksack", "duffel", "holdall", "suitcase",
        ],
    },

    # =========================================================================
    # 9. ACCESSORIES -> HATS, BELTS, SUNGLASSES, WATCHES & JEWELLERY
    # =========================================================================
    "hats_accessories": {
        "id": "hats_accessories",
        "name": "All Hats, Caps, Belts, Eyewear & Accessories",
        "short_name": "All Accessories",
        "parent": "Accessories",
        "group_id": "accessories_group",
        "department": "Accessories",
        "vinted_path": "Accessories › All Accessories",
        "vinted_catalog_ids": [82, 1187],
        "is_group_all": True,
        "keywords": [
            "hat", "cap", "beanie", "balaclava", "bucket hat", "snapback", "trucker cap",
            "scarf", "shawl", "gloves", "belt", "sunglasses", "glasses", "frames",
            "jewellery", "jewelry", "ring", "necklace", "chain", "pendant", "bracelet",
            "earrings", "watch", "wristwatch", "tie", "keyring", "keychain",
        ],
        "contradicting_words": _SHOE_WORDS + _OUTERWEAR_WORDS + _TOPS_SWEATERS_WORDS + _BOTTOMS_WORDS,
    },
    "acc_caps_beanies": {
        "id": "acc_caps_beanies",
        "name": "Hats, Caps, Beanies & Balaclavas",
        "short_name": "Hats, Caps & Beanies",
        "parent": "Accessories",
        "group_id": "accessories_group",
        "department": "Accessories",
        "vinted_path": "Accessories › Hats & caps",
        "vinted_catalog_ids": [86, 88, 287, 230, 2964, 2934, 288, 231, 2965, 2933],
        "is_group_all": False,
        "keywords": [
            "hat", "cap", "baseball cap", "snapback", "trucker cap", "5 panel", "dad hat",
            "beanie", "watch cap", "skull cap", "toque", "bobble hat", "bucket hat",
            "sun hat", "boonie", "beret", "flat cap", "balaclava", "ski mask", "headband",
        ],
        "contradicting_words": _SHOE_WORDS + _OUTERWEAR_WORDS + _TOPS_SWEATERS_WORDS + _BOTTOMS_WORDS + [
            "belt", "sunglasses", "watch", "ring", "necklace", "bracelet",
        ],
    },
    "acc_scarves_gloves": {
        "id": "acc_scarves_gloves",
        "name": "Scarves, Shawls, Gloves & Bandanas",
        "short_name": "Scarves & Gloves",
        "parent": "Accessories",
        "group_id": "accessories_group",
        "department": "Accessories",
        "vinted_path": "Accessories › Scarves, Shawls & Gloves",
        "vinted_catalog_ids": [87, 89, 91, 90, 2960, 2931],
        "is_group_all": False,
        "keywords": [
            "scarf", "wool scarf", "cashmere scarf", "check scarf", "shawl", "wrap",
            "snood", "neck warmer", "gloves", "mittens", "leather gloves", "running gloves",
            "bandana", "headscarf", "silk scarf", "handkerchief",
        ],
        "contradicting_words": _SHOE_WORDS + _OUTERWEAR_WORDS + _TOPS_SWEATERS_WORDS + _BOTTOMS_WORDS,
    },
    "acc_belts": {
        "id": "acc_belts",
        "name": "Belts & Buckles",
        "short_name": "Belts",
        "parent": "Accessories",
        "group_id": "accessories_group",
        "department": "Accessories",
        "vinted_path": "Accessories › Belts",
        "vinted_catalog_ids": [96, 20],
        "is_group_all": False,
        "keywords": [
            "belt", "leather belt", "reversible belt", "studded belt", "webbing belt",
            "canvas belt", "belt buckle", "designer belt",
        ],
        "contradicting_words": _SHOE_WORDS + _OUTERWEAR_WORDS + _TOPS_SWEATERS_WORDS + _BOTTOMS_WORDS + [
            "hat", "cap", "beanie", "scarf", "sunglasses", "watch",
        ],
    },
    "acc_sunglasses": {
        "id": "acc_sunglasses",
        "name": "Sunglasses & Eyewear",
        "short_name": "Sunglasses",
        "parent": "Accessories",
        "group_id": "accessories_group",
        "department": "Accessories",
        "vinted_path": "Accessories › Sunglasses",
        "vinted_catalog_ids": [98, 26],
        "is_group_all": False,
        "keywords": [
            "sunglasses", "shades", "eyewear", "glasses", "spectacles", "frames",
            "aviators", "wayfarer", "oakley", "ray-ban", "rayban", "goggles", "ski goggles",
        ],
        "contradicting_words": _SHOE_WORDS + _OUTERWEAR_WORDS + _TOPS_SWEATERS_WORDS + _BOTTOMS_WORDS + [
            "hat", "cap", "beanie", "scarf", "belt", "watch",
        ],
    },
    "acc_watches_jewellery": {
        "id": "acc_watches_jewellery",
        "name": "Watches & Jewellery (Rings, Necklaces, Bracelets)",
        "short_name": "Watches & Jewellery",
        "parent": "Accessories",
        "group_id": "accessories_group",
        "department": "Accessories",
        "vinted_path": "Accessories › Watches & Jewellery",
        "vinted_catalog_ids": [97, 22, 95, 21, 241, 242, 243, 164, 553, 165],
        "is_group_all": False,
        "keywords": [
            "watch", "wristwatch", "chronograph", "seiko", "casio", "g-shock", "omega",
            "rolex", "tissot", "citizen", "jewellery", "jewelry", "ring", "signet ring",
            "silver ring", "necklace", "chain", "pendant", "choker", "bracelet", "bangle",
            "cuff", "earrings", "studs", "hoops", "cufflinks", "brooch", "charm",
        ],
        "contradicting_words": _SHOE_WORDS + _OUTERWEAR_WORDS + _TOPS_SWEATERS_WORDS + _BOTTOMS_WORDS + [
            "hat", "cap", "beanie", "scarf", "belt", "sunglasses",
        ],
    },
}

# Universal spam & non-product exclusions (items that are never the genuine clothing/shoe)
GLOBAL_JUNK_TERMS = [
    "box only",
    "empty box",
    "replacement box",
    "dustbag only",
    "dust bag only",
    "laces only",
    "shoelaces only",
    "poster",
    "keychain",
    "keyring",
    "stickers",
    "receipt only",
    "shoe tree",
]


# ---------------------------------------------------------------------------
# Brand Knowledge Base
# ---------------------------------------------------------------------------

KNOWN_BRANDS = [
    ("Maison Margiela", ["margiela", "maison margiela", "mm6", "maison martin margiela"]),
    ("The North Face", ["north face", "the north face", "tnf"]),
    ("Ralph Lauren", ["ralph lauren", "polo ralph lauren", "polo"]),
    ("Patagonia", ["patagonia"]),
    ("A Bathing Ape", ["bape", "a bathing ape", "baby milo"]),
    ("Rick Owens", ["rick owens", "drkshdw"]),
    ("Arc'teryx", ["arc'teryx", "arcteryx"]),
    ("Nike", ["nike", "nike sportswear", "nike sb"]),
    ("Adidas", ["adidas", "adidas originals"]),
    ("Stüssy", ["stussy", "stüssy"]),
    ("Supreme", ["supreme"]),
    ("Stone Island", ["stone island", "shadow project"]),
    ("Carhartt WIP", ["carhartt", "carhartt wip"]),
    ("Palace", ["palace", "palace skateboards"]),
    ("Prada", ["prada", "prada linea rossa"]),
    ("Gucci", ["gucci"]),
    ("Balenciaga", ["balenciaga"]),
    ("Jordan", ["jordan", "air jordan"]),
    ("New Balance", ["new balance", "nb"]),
    ("Salomon", ["salomon", "salomon advanced"]),
    ("Asics", ["asics"]),
    ("C.P. Company", ["c.p. company", "cp company"]),
    ("Acne Studios", ["acne", "acne studios"]),
    ("Burberry", ["burberry"]),
    ("Diesel", ["diesel"]),
    ("Represent", ["represent", "represent clo"]),
    ("Corteiz", ["corteiz", "crtz"]),
    ("Trapstar", ["trapstar"]),
    ("Moncler", ["moncler"]),
    ("Canada Goose", ["canada goose"]),
    ("Superdry", ["superdry"]),
    ("Barbour", ["barbour"]),
    ("Birkenstock", ["birkenstock"]),
    ("Dr. Martens", ["dr martens", "doc martens", "dr. martens"]),
    ("Timberland", ["timberland"]),
    ("Clarks Originals", ["clarks", "clarks originals", "wallabee", "wallabees"]),
    ("Levi's", ["levis", "levi's"]),
]


# ---------------------------------------------------------------------------
# Intelligent Classifier & Categorizer
# ---------------------------------------------------------------------------

def suggest_brand_and_category(query: str) -> Dict[str, Any]:
    """Analyze query text to extract likely brand and suggested Vinted categories (preferring specific subcategories)."""
    q = query.lower().strip()

    # 1. Detect Brand
    detected_brand: Optional[str] = None
    for canonical_name, aliases in KNOWN_BRANDS:
        for alias in aliases:
            if re.search(r"\b" + re.escape(alias) + r"\b", q):
                detected_brand = canonical_name
                break
        if detected_brand:
            break

    # 2. Detect Specific Vinted Subcategories first, then Group Fallbacks
    specific_scores: Dict[str, int] = {}
    group_scores: Dict[str, int] = {}

    # Generic structural words that appear in many subcategories of the same group
    generic_words = {"jacket", "coat", "jumper", "sweater", "sweatshirt", "shirt", "top", "trousers", "pants", "shoes", "bag"}

    for cat_id, cat_info in VINTED_CATEGORIES.items():
        score = 0
        for kw in cat_info["keywords"]:
            if re.search(r"\b" + re.escape(kw) + r"\b", q):
                weight = (len(kw) + 8) if kw not in generic_words else 3
                score += weight
        if score > 0:
            if cat_info.get("is_group_all"):
                group_scores[cat_id] = score
            else:
                specific_scores[cat_id] = score

    matched_categories: List[str] = []
    if specific_scores:
        sorted_spec = sorted(specific_scores.items(), key=lambda x: x[1], reverse=True)
        top_score = sorted_spec[0][1]
        # Keep top specific matches that are close in score (up to 2)
        matched_categories = [
            cid for cid, sc in sorted_spec[:2]
            if sc >= max(6, int(top_score * 0.55))
        ]
        if not matched_categories:
            matched_categories = [sorted_spec[0][0]]
    elif group_scores:
        sorted_grp = sorted(group_scores.items(), key=lambda x: x[1], reverse=True)
        matched_categories = [cid for cid, _ in sorted_grp[:2]]

    # Fallback heuristics if no exact word boundary hit
    if not matched_categories:
        if any(w in q for w in ["shoe", "sneaker", "gat", "runner", "trainer", "kicks"]):
            matched_categories = ["shoes_sneakers"]
        elif any(w in q for w in ["boot", "chelsea", "martens"]):
            matched_categories = ["shoes_boots"]
        elif any(w in q for w in ["puffer", "nuptse", "down"]):
            matched_categories = ["outerwear_puffer_jackets"]
        elif any(w in q for w in ["fleece", "synchilla", "sherpa", "denali"]):
            matched_categories = ["outerwear_fleece_jackets"]
        elif any(w in q for w in ["gilet", "body warmer", "bodywarmer"]):
            matched_categories = ["outerwear_gilets"]
        elif any(w in q for w in ["jacket", "coat", "parka"]):
            matched_categories = ["coats_jackets"]
        elif any(w in q for w in ["hoodie", "hoody", "sweatshirt"]):
            matched_categories = ["hoodies_sweatshirts"]
        elif any(w in q for w in ["quarter zip", "1/4 zip", "half zip"]):
            matched_categories = ["jumpers_quarter_zip", "sweaters_knitwear"]
        elif any(w in q for w in ["knit", "sweater", "jumper", "cardigan"]):
            matched_categories = ["sweaters_knitwear"]
        else:
            matched_categories = ["coats_jackets"]

    category_labels = [
        VINTED_CATEGORIES[cid].get("vinted_path") or VINTED_CATEGORIES[cid]["name"]
        for cid in matched_categories
        if cid in VINTED_CATEGORIES
    ]
    short_labels = [
        VINTED_CATEGORIES[cid].get("short_name") or VINTED_CATEGORIES[cid]["name"]
        for cid in matched_categories
        if cid in VINTED_CATEGORIES
    ]
    detected_item_type = " + ".join(short_labels) if short_labels else "General Item"

    explanation = f"Detected: {detected_item_type}"
    if detected_brand:
        explanation += f" by {detected_brand}"

    return {
        "query": query,
        "suggested_brand": detected_brand or "",
        "suggested_categories": matched_categories,
        "suggested_category_labels": category_labels,
        "detected_item_type": detected_item_type,
        "explanation": explanation,
    }


# ---------------------------------------------------------------------------
# Strict Verification / Anti-Junk Inspection
# ---------------------------------------------------------------------------

def is_brand_match(target_brand: str, item_brand: Optional[str], item_title: str) -> Tuple[bool, str]:
    """Hard-filter: Verify whether scraped item's brand matches target brand.
    
    Returns (is_match, reason).
    """
    if not target_brand:
        return True, "No brand restriction set"

    tb = target_brand.strip().lower()
    ib = (item_brand or "").strip().lower()
    title = (item_title or "").strip().lower()

    aliases = [tb]
    for canon, alias_list in KNOWN_BRANDS:
        if tb == canon.lower() or tb in [a.lower() for a in alias_list]:
            aliases.extend([a.lower() for a in alias_list])
            aliases.append(canon.lower())
            break
    aliases = list(set(aliases))

    if ib:
        for alias in aliases:
            if alias in ib or ib in alias:
                return True, f"Brand '{ib}' matches target '{target_brand}'"
            if fuzz.partial_ratio(alias, ib) >= 80:
                return True, f"Brand '{ib}' fuzzy matches target '{target_brand}'"

        return False, f"Blatant brand mismatch: item brand is '{item_brand}' but target requires '{target_brand}'"

    for alias in aliases:
        if re.search(r"\b" + re.escape(alias) + r"\b", title):
            return True, f"Title contains brand keyword '{alias}'"

    return False, f"No brand detected and title does not mention '{target_brand}'"


def normalize_categories(raw: Any) -> List[str]:
    """Coerce various category formats (JSON list, comma-separated, legacy names) into canonical IDs."""
    import json

    if not raw:
        return []
    if isinstance(raw, list):
        items = raw
    elif isinstance(raw, str):
        raw_str = raw.strip()
        if not raw_str:
            return []
        try:
            parsed = json.loads(raw_str)
            if isinstance(parsed, list):
                items = parsed
            else:
                items = [str(parsed)]
        except Exception:
            items = [x.strip() for x in raw_str.split(",") if x.strip()]
    else:
        return []

    canonical: List[str] = []
    for item in items:
        item_str = str(item).strip()
        if not item_str:
            continue
        if item_str in VINTED_CATEGORIES:
            if item_str not in canonical:
                canonical.append(item_str)
            continue

        matched_id = None
        lower_item = item_str.lower()
        for cid, cinfo in VINTED_CATEGORIES.items():
            if (
                lower_item == cinfo["name"].lower()
                or lower_item == cinfo.get("short_name", "").lower()
                or lower_item in cinfo["name"].lower()
            ):
                matched_id = cid
                break
        if not matched_id:
            for cid, cinfo in VINTED_CATEGORIES.items():
                if any(w in lower_item for w in cinfo["name"].lower().split() if len(w) > 3):
                    matched_id = cid
                    break
        if not matched_id:
            for cid, cinfo in VINTED_CATEGORIES.items():
                for kw in cinfo.get("keywords", []):
                    if kw in lower_item:
                        matched_id = cid
                        break
                if matched_id:
                    break
        if matched_id and matched_id not in canonical:
            canonical.append(matched_id)

    return canonical


def get_vinted_catalog_ids_for_categories(target_categories: Any) -> List[int]:
    """Return deduplicated list of official Vinted UK numeric catalog_ids for the given categories."""
    normalized_cats = normalize_categories(target_categories)
    catalog_ids: List[int] = []
    for cid in normalized_cats:
        cinfo = VINTED_CATEGORIES.get(cid)
        if cinfo:
            for vid in cinfo.get("vinted_catalog_ids", []):
                if int(vid) not in catalog_ids:
                    catalog_ids.append(int(vid))
    return catalog_ids


def is_category_match(
    target_categories: List[str],
    item_title: str,
    item_description: Optional[str] = None,
    item_url: Optional[str] = None,
) -> Tuple[bool, str]:
    """Verify item is not blatantly a different category (e.g. shoes for a top/henley search, or gilet for a puffer jacket search).
    
    Inspects title, description, and URL slug for negative category contradictions and positive category alignment.
    Returns (is_match, reason).
    """
    normalized_cats = normalize_categories(target_categories)
    if not normalized_cats:
        return True, "No category restriction set"

    t = (item_title or "").lower()
    d = (item_description or "").lower()
    u = ""
    if item_url:
        # Extract slug tokens from URL (e.g. '10240151767-superdry-uk6-bundle' -> 'superdry uk6 bundle')
        slug_match = re.search(r"/items/\d+-([^/?#]+)", item_url.lower())
        if slug_match:
            u = slug_match.group(1).replace("-", " ")
        else:
            u = item_url.lower().replace("-", " ").replace("/", " ")

    # 1. Global junk terms check (across title and description)
    for junk in GLOBAL_JUNK_TERMS:
        if re.search(r"\b" + re.escape(junk) + r"\b", t):
            return False, f"Excluded as non-product listing ('{junk}' in title)"
        if d and re.search(r"\b" + re.escape(junk) + r"\b", d):
            return False, f"Excluded as non-product listing ('{junk}' in description)"

    # Build set of all positive keywords across all target categories
    allowed_keywords: Set[str] = set()
    for cat_id in normalized_cats:
        cat_info = VINTED_CATEGORIES.get(cat_id)
        if cat_info:
            for kw in cat_info.get("keywords", []):
                allowed_keywords.add(kw.lower())

    # Build set of contradictions that aren't allowed by any target category
    active_contradictions: Dict[str, str] = {}
    for cat_id in normalized_cats:
        cat_info = VINTED_CATEGORIES.get(cat_id)
        if not cat_info:
            continue
        for bad_word in cat_info.get("contradicting_words", []):
            bw = bad_word.lower()
            if bw not in allowed_keywords and bw not in active_contradictions:
                active_contradictions[bw] = cat_info.get("short_name") or cat_info["name"]

    # 2. Check for contradictions in title, description, and URL slug
    for bad_word, cat_name in active_contradictions.items():
        pattern = re.compile(r"\b" + re.escape(bad_word) + r"\b", re.IGNORECASE)
        if pattern.search(t):
            return False, f"Contradicts category '{cat_name}': title contains '{bad_word}'"
        if d and pattern.search(d):
            return False, f"Contradicts category '{cat_name}': description contains '{bad_word}'"
        if u and pattern.search(u):
            return False, f"Contradicts category '{cat_name}': URL contains '{bad_word}'"

    # 3. Positive category check:
    # If the user selected specific categories and description is available,
    # ensure at least one positive category keyword is present if title & URL are generic
    title_has_kw = any(re.search(r"\b" + re.escape(kw) + r"\b", t) for kw in allowed_keywords)
    url_has_kw = bool(u and any(re.search(r"\b" + re.escape(kw) + r"\b", u) for kw in allowed_keywords))

    if not title_has_kw and not url_has_kw:
        # Title and URL are completely generic (e.g. 'Superdry UK6 bundle', 'Superdry')
        if d:
            desc_has_kw = any(re.search(r"\b" + re.escape(kw) + r"\b", d) for kw in allowed_keywords)
            if not desc_has_kw:
                primary_cat_name = VINTED_CATEGORIES.get(normalized_cats[0], {}).get("short_name", "selected category")
                return False, f"Listing lacks positive keywords for '{primary_cat_name}' (generic title & non-matching description)"
        else:
            primary_cat_name = VINTED_CATEGORIES.get(normalized_cats[0], {}).get("short_name", "selected category")
            return False, f"Listing lacks positive keywords for '{primary_cat_name}' (generic title with no category keywords)"

    return True, "Passed category coherence"


# ---------------------------------------------------------------------------
# Child / Youth Size Detection Heuristics
# ---------------------------------------------------------------------------

CHILD_SIZE_REGEXES = [
    (re.compile(r"\b(age\s*\d{1,2}(-\d{1,2})?(\s*years?|\s*yrs?|\s*y)?)\b", re.IGNORECASE), "Age-based size"),
    (re.compile(r"\b(\d{1,2}-\d{1,2}\s*(years?|yrs?|y))\b", re.IGNORECASE), "Years range size"),
    (re.compile(r"\b(\d{1,2}\s*(years?|yrs?))\b", re.IGNORECASE), "Child year size"),
    (re.compile(r"\b(1[0-7][0-9]\s*cm)\b", re.IGNORECASE), "Children height (cm) size"),
    (re.compile(r"\b(0-3m|3-6m|6-9m|9-12m|12-18m|18-24m|2-3y|3-4y|4-5y|5-6y|[2-5]t)\b", re.IGNORECASE), "Baby/toddler size"),
    (re.compile(r"\b(yxs|ys|ym|yl|yxl)\b", re.IGNORECASE), "Youth sizing code (YXS-YXL)"),
    (re.compile(r"\b(kids?|childrens?|childs?|junior|juniors|jnr|youth|youths|infant|toddler)\b", re.IGNORECASE), "Child/youth keyword"),
    (re.compile(r"\bbaby\b(?!\s+(blue|pink|yellow|green|tee|tees))\b", re.IGNORECASE), "Baby clothing"),
    (re.compile(r"\b(boys?|girls?)\b(?!\s+(friend|friends))\b", re.IGNORECASE), "Boys/Girls clothing"),
]


def is_child_size(item_title: str, item_size: Optional[str] = None) -> Tuple[bool, str]:
    """Inspect item title and size string to detect child, kids, junior, or youth clothing."""
    size_str = (item_size or "").strip()
    title_str = (item_title or "").strip()

    if size_str:
        for pattern, label in CHILD_SIZE_REGEXES:
            m = pattern.search(size_str)
            if m:
                return True, f"{label}: '{m.group(0)}' in size '{size_str}'"

    if title_str:
        for pattern, label in CHILD_SIZE_REGEXES:
            m = pattern.search(title_str)
            if m:
                return True, f"{label}: '{m.group(0)}' in title"

    return False, ""


def is_item_relevant(
    target_brand: Optional[str],
    target_categories: Optional[Any],
    must_exclude: Optional[str],
    item_brand: Optional[str],
    item_title: str,
    exclude_child_sizes: bool = False,
    item_size: Optional[str] = None,
    item_description: Optional[str] = None,
    item_url: Optional[str] = None,
) -> Tuple[bool, str]:
    """Composite check: Determine whether a scraped listing should be kept or auto-removed."""
    # 1. Brand hard filter
    if target_brand:
        matched, reason = is_brand_match(target_brand, item_brand, item_title)
        if not matched:
            return False, reason

    # 2. Custom exclusions filter
    if must_exclude:
        terms = [x.strip().lower() for x in re.split(r"[,;]+", must_exclude) if x.strip()]
        t = (item_title or "").lower()
        d = (item_description or "").lower()
        for term in terms:
            pat = r"\b" + re.escape(term) + r"\b"
            if re.search(pat, t):
                return False, f"Matches excluded term in title: '{term}'"
            if d and re.search(pat, d):
                return False, f"Matches excluded term in description: '{term}'"

    # 3. Child size filter
    if exclude_child_sizes:
        child_match, child_reason = is_child_size(item_title, item_size)
        if child_match:
            return False, f"Child size excluded: {child_reason}"

    # 4. Category / type coherence filter
    if target_categories:
        matched, reason = is_category_match(
            target_categories,
            item_title,
            item_description=item_description,
            item_url=item_url,
        )
        if not matched:
            return False, reason

    return True, "Listing is relevant"
