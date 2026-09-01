"""Every product line from the 5 real bills — the parser's acceptance corpus.

Source images (in the plan discussion):
  - inward INV RSW/167 p1 + p2 : Sethiya Bartan Bhandar wholesale, priced per KG
  - handwritten K.D bill        : counter sale, mixed piece / kg
  - handwritten bill #1801      : Siliguri Metal, branded goods, per PIECE

Each entry: (raw_line, expect) where `expect` names only the fields we can
assert with confidence. `None` = "don't assert this field".
"""

from __future__ import annotations

from app.models._mixins import RateMode

# brands the tenant would have (item_category names) at parse time
BRANDS = [
    "ST",
    "GS",
    "SS",
    "Hawkins",
    "Mintage",
    "Dhara Kettle",
    "SINI",
    "Prestige",
]

# from_token -> to_token (a subset of the metal-trade synonym seed + bartan)
SYNONYMS = {
    "kettly": "kettle",
    "s": "s",  # noop guard
}

# (raw, {field: expected | None})
CORPUS: list[tuple[str, dict]] = [
    # --- wholesale inward, per KG ---
    ("ST STORAGE BOX 12X18 273.685 KGS per KGS 182",
     {"brand": "ST", "size": "12x18", "size_kind": "nxn", "rate_mode": RateMode.kg, "rate": 182.0}),
    ("GS DEEP DIBBA 10X14 43.630 KGS per KGS 308.5",
     {"brand": "GS", "size": "10x14", "rate_mode": RateMode.kg, "rate": 308.5}),
    ("C.B.KADAI 12X18 8.674 KGS per KGS 289",
     {"size": "12x18", "rate_mode": RateMode.kg, "rate": 289.0}),
    ("ST BUCKET 12X15 9.255 KGS per KGS 192",
     {"brand": "ST", "size": "12x15", "rate_mode": RateMode.kg, "rate": 192.0}),
    ("ST DHEGCHI 00X5 27.152 KGS per KGS 282",
     {"brand": "ST", "size": "00x5", "rate_mode": RateMode.kg, "rate": 282.0}),
    ("ST MILK POT DK 10X18 29.568 KGS per KGS 268.5",
     {"brand": "ST", "size": "10x18", "rate_mode": RateMode.kg, "rate": 268.5}),
    ("FLAT BOTTAM TOP 10X18 43.968 KGS per KGS 239",
     {"size": "10x18", "rate_mode": RateMode.kg, "rate": 239.0}),
    ("RAJAT TOP 29X32 8.814 KGS per KGS 392",
     {"size": "29x32", "rate_mode": RateMode.kg, "rate": 392.0}),

    # --- counter bill, mixed ---
    ("Hawkins 10 31X5 1 x 2445",
     {"brand": "Hawkins", "rate_mode": RateMode.piece, "qty": 1.0, "rate": 2445.0}),
    ("St Tray 18.60 x 450",
     {"rate_mode": RateMode.kg, "qty": 18.6, "rate": 450.0}),
    ("SS 16 16 1 x 2500",
     {"brand": "SS", "rate_mode": RateMode.piece, "qty": 1.0, "rate": 2500.0}),
    ("Hawkins 22 7 x 6006",
     {"brand": "Hawkins", "rate_mode": RateMode.piece, "qty": 7.0, "rate": 6006.0}),
    ("SINI B.Thali 13.970 x 270",
     {"brand": "SINI", "rate_mode": RateMode.kg, "qty": 13.97, "rate": 270.0}),
    ("Spoon 100 x 22.50",
     {"rate_mode": RateMode.piece, "qty": 100.0, "rate": 22.5}),

    # --- branded goods bill #1801, per PIECE ---
    ("Dhara Kettly 10cup 6PC 425",
     {"brand": "Dhara Kettle", "size": "10cup", "size_kind": "cup",
      "rate_mode": RateMode.piece, "qty": 6.0, "rate": 425.0}),
    ("Dhara Kettly 25cup 3PC 600",
     {"brand": "Dhara Kettle", "size": "25cup", "rate_mode": RateMode.piece,
      "qty": 3.0, "rate": 600.0}),
    ("Mintage 3499 5Ltr 1PC 1820",
     {"brand": "Mintage", "sku": "3499", "size": "5ltr", "size_kind": "litre",
      "rate_mode": RateMode.piece, "qty": 1.0, "rate": 1820.0}),
    ("Mintage 4899 10Ltr 1PC 2548",
     {"brand": "Mintage", "sku": "4899", "size": "10ltr",
      "rate_mode": RateMode.piece, "rate": 2548.0}),
    ("Mintage 4199 7.5Ltr 4PC 2183",
     {"brand": "Mintage", "sku": "4199", "size": "7.5ltr", "rate_mode": RateMode.piece,
      "qty": 4.0, "rate": 2183.0}),
]
