"""The fixed item taxonomy for a metal / kitchenware retail-wholesale shop.

Pure data. No I/O. Consumed by `item_classify.classify_item` (the rules-first
classifier) and `services.catalogue.seed_taxonomy` (which materialises the
departments, brands and groups as `item_category` / `product_group` rows).

Design (see docs/visual-plan/item-categorization-plan.md):
  * category = a BRAND where one is detected, else a DEPARTMENT  ("hybrid")
  * group    = the product type ("Kadai", "Rice Cooker", "Jhula")
  * 12 departments, ~85 groups.  Two minor lines (Water & Filtration,
    Metal / Trade) were folded into "Other" 2026-09-02 — their phrases stay
    in the table, pointed at OTHER, so a re-split is a one-line re-point.

Everything is matched against the *normalised* name (see
`domain.normalize.normalize_name`): lower-case, punctuation → spaces,
synonyms applied. So phrases here are lower-case, space-delimited, and a
leading/trailing space in a phrase means "word boundary" (we match on
`" " + normalised + " "`).
"""

from __future__ import annotations

OTHER_DEPARTMENT = "Other / Uncategorised"

# --------------------------------------------------------------------------
# departments  (name, sort)  — sort keeps the Items screen order stable
# --------------------------------------------------------------------------

DEPARTMENTS: list[str] = [
    "Steel Utensils & Serveware",
    "Kitchen Appliances",
    "Cookware",
    "Plasticware",
    "Cutlery & Kitchen Tools",
    "Flasks, Bottles & Thermoware",
    "Pressure Cookers",
    "Pooja & Wooden Goods",
    "Glassware & Crockery",
    "Household & Cleaning",
    "Furniture",
    OTHER_DEPARTMENT,
]

# --------------------------------------------------------------------------
# brands  — canonical name -> phrases that identify it (normalised, boundary)
# A brand only becomes its own category when it appears on >= BRAND_MIN_ITEMS
# items in a run (see seed_taxonomy / reclassify).  Below that it is watched
# but the item keeps its department as category.
# --------------------------------------------------------------------------

BRAND_MIN_ITEMS = 3

BRANDS: dict[str, list[str]] = {
    "Prestige": [
        " prestige ", " pkoss ", " nakshatra ", " svachh ", " contura ",
        " clip on ", " clipon ", " triply ",
    ],
    "Hawkins": [" hawkins ", " bigboy ", " big boy "],
    "Milton": [
        " milton ", " thermosteel ", " thermo steel ", " insu steel ",
        " therminox ", " inox hydra ", " hydra plus ",
    ],
    "Cello": [" cello ", " maxfresh ", " max fresh "],
    "Borosil": [" borosil "],
    "Panasonic": [" sr wa ", " sr g ", " sr 942 ", " sr 972 ", " srwa ", " panasonic "],
    "Bajaj": [" bajaj "],
    "Pigeon": [" pigeon ", " stovekraft "],
    "Butterfly": [" butterfly "],
    "Vinod": [" vinod "],
    "Signoraware": [" signora ", " signoraware "],
    "Treo": [" treo "],
    "La Opala": [" la opala ", " opala "],
    "Wonderchef": [" wonderchef "],
    "Jaypee": [" jaypee "],
    "Judge": [" judge "],
}

# Brands to seed as categories on register even before any item proves them.
STARTER_BRAND_CATEGORIES: list[str] = [
    "Prestige", "Hawkins", "Milton", "Cello", "Borosil", "Panasonic", "Bajaj",
    "Butterfly", "Vinod", "Signoraware", "Treo", "La Opala", "Wonderchef",
]

# --------------------------------------------------------------------------
# keyword rules  (department, group, [phrases])
# FIRST MATCH WINS — order matters. Specific before generic:
# "rice cooker" is above "cooker"; brand-model words that imply a type
# ("prwo" -> rice cooker) sit with their type.
# --------------------------------------------------------------------------

RULES: list[tuple[str, str, list[str]]] = [
    # ---------- Kitchen Appliances ----------
    ("Kitchen Appliances", "Rice Cooker",
     [" rice cooker ", " prwo ", " prow ", " srwa ", " sr wa ", " sr g ",
      " cute steel bowl ", " drpc "]),
    ("Kitchen Appliances", "Electric Kettle", [" kettle "]),
    ("Kitchen Appliances", "Induction Cooktop", [" induction "]),
    ("Kitchen Appliances", "Mixer / Grinder",
     [" mixer ", " grinder ", " wet grin", " juicer ", " blender ",
      " juicer mixer ", " mg black ", " mg wine "]),
    ("Kitchen Appliances", "OTG / Oven / Grill",
     [" otg ", " oven ", " microwave ", " prime grill ", " elite prime "]),
    ("Kitchen Appliances", "Gas Stove / Hob",
     [" gas stove ", " stove ", " hob ", " glasstop ", " glass top ",
      " 2br ", " 3br ", " 4br ", " 2 br ", " 3 br ", " 4 br ", " burner ",
      " gas dgs ", " cooktop "]),
    ("Kitchen Appliances", "Toaster / Sandwich Maker",
     [" toaster ", " sandwich ", " pop up ", " krispy "]),
    ("Kitchen Appliances", "Electric Fryer / Air Fryer",
     [" electric fryer ", " elec fryer ", " deep fryer ", " air fryer "]),
    ("Kitchen Appliances", "Garment Iron",
     [" bajaj iron ", " dry iron ", " steam iron ", " pdi "]),
    ("Kitchen Appliances", "Atta / Dough Machine",
     [" aata machine ", " atta machine ", " dough kne", " flour mill ",
      " kneader ", " kneder "]),
    ("Kitchen Appliances", "Hand Blender / Beater",
     [" hand blender ", " electric beater ", " mathani elec"]),
    ("Kitchen Appliances", "Heater / Hot Plate / Immersion Rod",
     [" immersion ", " heating rod ", " room heater ", " hot plate ",
      " hotplate ", " immersion rod "]),

    # ---------- Pressure Cookers ----------
    ("Pressure Cookers", "Pressure Cooker",
     [" pressure cooker ", " preesure cooker ", " preasure cooker ",
      " hawkins bigboy ", " hawkins classic ", " hawkins cooker ",
      " popular cooker "]),
    ("Pressure Cookers", "Pressure Pan", [" pressure pan ", " pressure handi "]),
    ("Pressure Cookers", "Cooker Gasket",
     [" gaskit ", " gasket ", " gaskt ", " gaskit "]),
    ("Pressure Cookers", "Cooker Spare",
     [" safety valve ", " vent weight ", " cooker handle ", " outer lid ",
      " inner lid ", " pressure regulator ", " cooker lid ", " gasket ring "]),
    ("Pressure Cookers", "Cooker (unclassified)", [" cooker "]),

    # ---------- Cookware ----------
    ("Cookware", "Kadai / Kadhai", [" kadai ", " kadhai ", " karahi ", " kaadai "]),
    ("Cookware", "Fry Pan",
     [" fry pan ", " frypan ", " fry paan ", " frying pan ", " ceraglide "]),
    ("Cookware", "Tawa", [" tawa ", " tava ", " omni tawa ", " dosa tawa "]),
    ("Cookware", "Handi", [" handi "]),
    ("Cookware", "Patila / Topa / Bhagona",
     [" patila ", " tope ", " topa ", " bhagona ", " degchi ", " cheeba "]),
    ("Cookware", "Sauce Pan / Milk Pan",
     [" sauce pan ", " saucepan ", " sauspan ", " milk pan ", " tea pan "]),
    ("Cookware", "Appam / Idli / Paniyaram",
     [" appam ", " paniyaram ", " idli ", " appachatty ", " paddu ",
      " idli panai ", " appam patra "]),

    # ---------- Cutlery & Kitchen Tools ----------
    ("Cutlery & Kitchen Tools", "Knife / Chopper",
     [" knife ", " choper ", " chopper ", " chipser ", " cleaver ", " bhola knife "]),
    ("Cutlery & Kitchen Tools", "Skimmer / Jhara / Palta",
     [" skimmer ", " jhara ", " palta ", " palti ", " turner ", " frying spoon ",
      " serving spoon ", " powni ", " karchi ", " karchhi ", " doi "]),
    ("Cutlery & Kitchen Tools", "Masher / Ghotni",
     [" masher ", " ghotni ", " bhaji masher ", " potato masher "]),
    ("Cutlery & Kitchen Tools", "Grater / Slicer / Kisni",
     [" grater ", " slicer ", " kisni ", " kaddukas "]),
    ("Cutlery & Kitchen Tools", "Peeler / Cutter",
     [" peeler ", " cutter ", " apple cutter ", " pizza cutter ", " pastry cutter ",
      " lemon squeezer ", " squeezer "]),
    ("Cutlery & Kitchen Tools", "Chimta / Tong / Sansi",
     [" chimta ", " chimata ", " tong ", " sansi ", " sandasi ", " pakkad "]),
    ("Cutlery & Kitchen Tools", "Sarota / Nut Cracker",
     [" sarota ", " nut cracker ", " supari cutter "]),
    ("Cutlery & Kitchen Tools", "Tea Strainer / Chalni",
     [" tea stainer ", " tea strainer ", " chalni ", " chhalni ", " stainer ",
      " channi ", " conical strainer ", " tea chhani "]),
    ("Cutlery & Kitchen Tools", "Chakla Belan / Rolling Pin",
     [" belan ", " chakla ", " rolling pin "]),
    ("Cutlery & Kitchen Tools", "Sil Batta / Mortar",
     [" sil batta ", " silbatta ", " khalbatta ", " okhli ", " mortar pestle ",
      " kharal "]),
    ("Cutlery & Kitchen Tools", "Whisk / Mathani",
     [" mathani ", " whisk ", " egg beater ", " egg wish ", " egg wisk ",
      " akhand "]),
    ("Cutlery & Kitchen Tools", "Scoop (Aata / Ice Cream)",
     [" aata scoop ", " atta scoop ", " ice cream scoop ", " scooper ",
      " scoop no "]),
    ("Cutlery & Kitchen Tools", "Kitchen Tool Set",
     [" 7 in 1 ", " 5 in 1 ", " kitchen tool set ", " gadget set "]),

    # ---------- Flasks, Bottles & Thermoware ----------
    ("Flasks, Bottles & Thermoware", "Vacuum Flask / Thermos",
     [" thermosteel ", " thermos ", " vacuum flask ", " flask ", " thermo warm ",
      " therminox ", " eiffel flask "]),
    ("Flasks, Bottles & Thermoware", "Steel Water Bottle",
     [" steel bottle ", " ss bottle ", " insu steel ", " hydra ", " sipper ",
      " fridge bottle ", " sports bottle "]),
    ("Flasks, Bottles & Thermoware", "Copper Bottle / Jug",
     [" copper bottle ", " copper jug ", " copper surahi ", " steel copper jug "]),
    ("Flasks, Bottles & Thermoware", "Insulated Casserole",
     [" casserole ", " aspire ", " ambition ", " aura ", " angelina ",
      " adore ", " nanonine ", " hot serve ", " hot pot ", " hotpot "]),
    ("Flasks, Bottles & Thermoware", "Beverage Dispenser / Airpot",
     [" beverage dispenser ", " airpot ", " air pot ", " dispenser ", " beverage dis "]),
    ("Flasks, Bottles & Thermoware", "Tea Can / Tea Container",
     [" tea can ", " tea container ", " aristocrat tea ", " aristrocrat tea "]),

    # ---------- Pooja & Wooden Goods ----------
    ("Pooja & Wooden Goods", "Jhula / Palna",
     [" jhula ", " zhula ", " palna ", " jhulla ", " jhoola "]),
    ("Pooja & Wooden Goods", "Bajot / Chowki / Patla",
     [" bajot ", " bajoth ", " chowki ", " choki ", " patla ", " austkon ",
      " aachkka "]),
    ("Pooja & Wooden Goods", "Mandir / Temple", [" mandir ", " temple "]),
    ("Pooja & Wooden Goods", "Jewellery Box",
     [" jwellery box ", " jewellery box ", " jewel box ", " jwellary ",
      " putturi ", " jwellery "]),
    ("Pooja & Wooden Goods", "Dry Fruit Box", [" dry fruit box ", " dryfruit box "]),
    ("Pooja & Wooden Goods", "Bangle Box",
     [" bangel box ", " bangle box ", " bangadi box ", " roll bangel "]),
    ("Pooja & Wooden Goods", "Puja Thali / Kalash / Diya / Bell",
     [" puja thali ", " pooja thali ", " kalash ", " diya ", " aarti ",
      " ganesh idol ", " laxmi idol ", " bells ", " pooja bell "]),
    ("Pooja & Wooden Goods", "Weight Box / Cash Box",
     [" weight box ", " cash box ", " golla ", " golak ", " gollak ",
      " money bank ", " piggy bank "]),
    ("Pooja & Wooden Goods", "Puper Wedding Box",
     [" puper w box ", " puper wbox ", " puper w b", " puper "]),

    # ---------- Steel Utensils & Serveware ----------
    ("Steel Utensils & Serveware", "Balti Set (Gift Set)",
     [" balti set ", " balty set ", " magnum ", " gift set "]),
    ("Steel Utensils & Serveware", "Dinner Set", [" dinner set "]),
    ("Steel Utensils & Serveware", "Tiffin / Lunch Box",
     [" tiffin ", " lunch box ", " lunchbox ", " tiff box ", " bestie ",
      " big bite "]),
    ("Steel Utensils & Serveware", "Thali / Plate",
     [" thali ", " plate ", " quarter plate ", " half plate ", " dinner plate ",
      " full plate ", " plainware ", " 5 in one thali "]),
    ("Steel Utensils & Serveware", "Bowl / Katori",
     [" katori ", " wati ", " bowl ", " vati ", " pride bowl "]),
    ("Steel Utensils & Serveware", "Glass / Tumbler",
     [" water glass ", " tumbler ", " glass 6 ", " glass set ", " glass 6pcs ",
      " glass pcs ", " amp glass "]),
    ("Steel Utensils & Serveware", "Jug", [" jug "]),
    ("Steel Utensils & Serveware", "Mug", [" mug "]),
    ("Steel Utensils & Serveware", "Serving Bowl / Donga",
     [" donga ", " serving bowl ", " serving dish ", " biryani pot ",
      " serving pot ", " handi serving "]),
    ("Steel Utensils & Serveware", "Table Spoon / Fork",
     [" table spoon ", " dinner spoon ", " tea spoon ", " baby spoon ",
      " fork ", " dessert spoon ", " soup spoon ", " coffee spoon ",
      " bar spoon ", " tea pipe "]),
    ("Steel Utensils & Serveware", "Lota / Gadva / Golchi",
     [" lota ", " gadva ", " gadwa ", " panchpatra ", " golchi ", " golchhi ",
      " ghadva "]),
    ("Steel Utensils & Serveware", "Steel Container / Ghee Pot",
     [" dabba ", " masala box ", " ghee pot ", " oil pot ", " ger set ",
      " storage box steel ", " dabba set "]),
    ("Steel Utensils & Serveware", "Kitchen Rack / Stand",
     [" dish rack ", " plate rack ", " cutlery rack ", " kitchen rack ",
      " kitchen stand ", " ss kitchen ", " glass stand ", " thali stand "]),
    ("Steel Utensils & Serveware", "Tray / Ash Tray",
     [" ash tray ", " serving tray ", " tray "]),

    # ---------- Glassware & Crockery ----------
    ("Glassware & Crockery", "Storage Jar", [" jar "]),
    ("Glassware & Crockery", "Drinking Glass Set",
     [" beer glass ", " whiskey ", " juice glass ", " verti ", " glass 6 pcs set "]),
    ("Glassware & Crockery", "Cup & Saucer",
     [" cup saucer ", " cup n saucer ", " cup and saucer ", " bella cup ",
      " oasis cup ", " cc cup ", " ccipl "]),
    ("Glassware & Crockery", "Ceramic / Glass Mug",
     [" coffe mug ", " coffee mug ", " ceramic mug ", " beer mug "]),
    ("Glassware & Crockery", "Glass Bowl",
     [" glass bowl ", " borosil bowl ", " mixing bowl glass "]),
    ("Glassware & Crockery", "Opalware / Crockery Dinner Set",
     [" opalware ", " opal ", " dplate ", " fluted dplate ", " crockery "]),

    # ---------- Plasticware ----------
    ("Plasticware", "Plastic Storage Container",
     [" storage container ", " air tight ", " airtight ", " plastic container ",
      " modular ", " super chill ", " super lock "]),
    ("Plasticware", "Plastic Bucket / Bath Set",
     [" balti plastic ", " plastic bucket ", " plastic mug ", " bath set "]),
    ("Plasticware", "Plastic Jug / Bottle",
     [" plastic jug ", " tuff jug ", " viva jug ", " fridge jug ", " pearl pet "]),
    ("Plasticware", "Plastic Basket / Tray",
     [" plastic basket ", " plastic tray ", " fruit basket "]),
    ("Plasticware", "Chopping Board",
     [" chopping board ", " chop board ", " cutting board "]),
    ("Plasticware", "Cooler Box", [" cooler box ", " kool ", " ice box "]),

    # ---------- Household & Cleaning ----------
    ("Household & Cleaning", "Bucket - Aluminium",
     [" al bucket ", " aluminium bucket ", " alu bucket ", " al bkt "]),
    ("Household & Cleaning", "Bucket - GI / Steel",
     [" g i bucket ", " gi bucket ", " steel bucket ", " balti "]),
    ("Household & Cleaning", "Tub / Ghamela / Parat",
     [" ghamela ", " parat ", " tub ", " tasla ", " tagari ", " basin "]),
    ("Household & Cleaning", "Gamla / Planter",
     [" gamla ", " planter ", " flower pot ", " jhajri gamla "]),
    ("Household & Cleaning", "Mop", [" mop ", " spin mop ", " mop head "]),
    ("Household & Cleaning", "Broom / Wiper / Brush",
     [" broom ", " jhadu ", " wiper ", " floor brush ", " toilet brush ",
      " duster ", " hockey "]),
    ("Household & Cleaning", "Dustbin", [" dustbin ", " dust bin ", " pedal bin ", " garbage "]),
    ("Household & Cleaning", "Drying Stand / Hanger",
     [" cloth dry ", " drying stand ", " hanger ", " clothes stand ", " cloths stand "]),
    ("Household & Cleaning", "Lighter / Agarbatti Stand",
     [" lighter ", " gas lighter ", " aggarbatti ", " agarbatti ", " matchbox "]),
    ("Household & Cleaning", "Padlock / Hardware",
     [" padlock ", " pad lock ", " tower bolt ", " aldrop ", " hinge ", " latch "]),
    ("Household & Cleaning", "Coal Iron (non-electric)",
     [" 18x18 iron ", " iron peti ", " coal iron ", " charcoal iron "]),

    # ---------- Furniture ----------
    ("Furniture", "Chair", [" chair "]),
    ("Furniture", "Stool", [" stool "]),
    ("Furniture", "Table", [" table ", " folding table "]),
    ("Furniture", "Rack / Shelf / Almirah",
     [" shoe rack ", " book rack ", " storage rack ", " shelf ", " almirah ",
      " cupboard "]),
    ("Furniture", "Garden Swing", [" koko pinky ", " garden swing "]),
    ("Furniture", "Bed / Bedding", [" mattress ", " bedding ", " folding bed ", " utsav "]),

    # ---------- folded into Other (kept, re-pointable) ----------
    (OTHER_DEPARTMENT, "Water Filter (folded)",
     [" water filter ", " bharati ", " filter candle ", " candle long ",
      " pure sleek ", " pure and sleek "]),
    (OTHER_DEPARTMENT, "Camper / Matka / Surahi (folded)",
     [" camper ", " matka ", " surahi "]),
    (OTHER_DEPARTMENT, "Scrap (folded)", [" scrap ", " scraps "]),
    (OTHER_DEPARTMENT, "Sheet / Circle / Patti (folded)",
     [" circle ", " sheet ", " patti ", " patra "]),
    (OTHER_DEPARTMENT, "Brass / Copper Utensils (folded)",
     [" brass utensil ", " copper utensil ", " steel copper utensil ",
      " brass bartan "]),
    (OTHER_DEPARTMENT, "Consumable / Packing (folded)",
     [" consumable item ", " master packing ", " file folder ", " packing "]),
]

# --------------------------------------------------------------------------
# HSN 2-digit chapter -> department  (the fallback when no keyword hits)
# --------------------------------------------------------------------------

HSN_CHAPTER_DEPARTMENT: dict[str, str] = {
    "73": "Steel Utensils & Serveware",
    "76": "Cookware",
    "70": "Glassware & Crockery",
    "39": "Plasticware",
    "82": "Cutlery & Kitchen Tools",
    "84": "Kitchen Appliances",
    "85": "Kitchen Appliances",
    "96": "Flasks, Bottles & Thermoware",
    "44": "Pooja & Wooden Goods",
    "94": "Furniture",
    "69": "Glassware & Crockery",
    "74": OTHER_DEPARTMENT,   # copper/brass — folded
    "40": "Pressure Cookers",  # rubber — mostly gaskets
    "83": "Household & Cleaning",
}

# The group an HSN-fallback item lands in, per department. Must exist in RULES
# above as a real group name (seed_taxonomy materialises exactly these).
HSN_FALLBACK_GROUP: dict[str, str] = {
    "Steel Utensils & Serveware": "Steel Utensils (unsorted)",
    "Cookware": "Aluminium / Other Cookware (unsorted)",
    "Glassware & Crockery": "Glassware (unsorted)",
    "Plasticware": "Plasticware (unsorted)",
    "Cutlery & Kitchen Tools": "Kitchen Tools (unsorted)",
    "Kitchen Appliances": "Appliances (unsorted)",
    "Flasks, Bottles & Thermoware": "Thermoware (unsorted)",
    "Pooja & Wooden Goods": "Wooden Goods (unsorted)",
    "Furniture": "Furniture (unsorted)",
    "Pressure Cookers": "Cooker (unclassified)",
    "Household & Cleaning": "Household (unsorted)",
    OTHER_DEPARTMENT: "Uncategorised",
}


def all_group_names() -> list[tuple[str, str]]:
    """Every (department, group) pair the taxonomy defines — the set
    `seed_taxonomy` must create. Rule groups first, then the per-department
    '(unsorted)' fallback buckets, then the bare 'Uncategorised' of Other.
    De-duplicated, order preserved.
    """
    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str]] = []
    for dept, grp, _phrases in RULES:
        pair = (dept, grp)
        if pair not in seen:
            seen.add(pair)
            out.append(pair)
    for dept, grp in HSN_FALLBACK_GROUP.items():
        pair = (dept, grp)
        if pair not in seen:
            seen.add(pair)
            out.append(pair)
    return out
