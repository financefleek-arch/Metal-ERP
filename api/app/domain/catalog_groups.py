"""Product-type group rules for supplier catalogs.

Pure, first-hit-wins keyword rules over the supplier's product name. Kept apart
from `item_taxonomy` (the 12-department steel/bartan classifier) so that one and
its tests are untouched: the catalog's group basis is *product type* (Beer Mugs,
Jars & Storage, ...), not department or brand.

Order matters: a more specific phrase must come before a general one
("BEER MUG" before "MUG"). The POC measured 87% of a 2,056-item glassware catalog
classified by these rules; the rest is edited in bulk or learned later.
"""

from __future__ import annotations

import re

RULES: list[tuple[str, str]] = [
    ("Beer Mugs", r"BEER MUG|BEER GLASS|PILSNER"),
    ("Whisky Glasses", r"WHISKY|WHISKEY|ROCK GLASS|OLD FASHION"),
    ("Wine Glasses", r"WINE|CHAMPAGNE|GOBLET|FLUTE"),
    ("Shot Glasses", r"SHOT"),
    ("Cocktail & Dessert Glasses", r"MARGHARITA|MARGARITA|COCKTAIL|ICE CREAM|MARTINI|PARFAIT"),
    ("Jars & Storage", r"\bJAR\b|CANISTER|STORAGE|\bTANK\b"),
    ("Bottles", r"BOTTLE|DECANTER"),
    ("Jugs & Water Sets", r"\bJUG\b|CARAFFE|CARRAFE|PITCHER|LEMON SET|WATER SET|KETTLE"),
    ("Bowls & Bowl Sets", r"BOWL|PUDDING|SALAD|DESSERT"),
    ("Casseroles & Serveware", r"CASSEROLE|SERVING|HOT ?POT|SERVER"),
    ("Vases & Decor", r"\bVASE\b|FLOWER|DECOR"),
    ("Plates & Dinner Sets", r"PLATE|DINNER|SNACK SET|TRAY|SAUCER"),
    ("Juice & Water Glasses", r"JUICE|WATER GLASS|TUMBLER|HIGHBALL|DRINKING|SHERBET|LASSI"),
    ("Cups & Mugs", r"\bMUG\b|\bCUP\b|COFFEE|\bTEA\b"),
    ("Jugs & Water Sets", r"\bWATER\b"),
    ("Drinking Glasses", r"\bGLASS(ES)?\b"),
]

_COMPILED = [(group, re.compile(pattern, re.IGNORECASE)) for group, pattern in RULES]


def classify(text: str) -> str | None:
    """The product-type group for a product name, or None when no rule fires."""
    for group, rx in _COMPILED:
        if rx.search(text):
            return group
    return None
