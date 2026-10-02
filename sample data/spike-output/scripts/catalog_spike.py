"""Spike: sample 100 catalog items -> groups, codes, prices, catalog PDF, barcode labels, Tally masters XML.
Scratchpad only. Not product code."""
import sys, os, re, json, collections
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE + "/lib")
import pymupdf as fitz
from barcode import Code128
import zxingcpp
from PIL import Image
from xml.sax.saxutils import escape as _esc
def escape(t): return _esc(t, {'"': '&quot;'})

SRC = "c:/Fleek Finance Code/Metal ERP/sample data/No1-30 Sept Glassware Stock.pdf"
OUT = HERE + "/out100"
os.makedirs(OUT, exist_ok=True)
N = 100
MULT = 1.25
STEP = 1
ROOT_GROUP = "ZZTEST Catalog"          # test root so cleanup is one subtree
PRICE = re.compile(r"Rs\s+([\d,.]+)\s+for\s+(\d+)\s*pcs?\s*(.*)", re.I)

# ---------------------------------------------------------------- extract
def extract(path):
    d = fitz.open(path)
    items = []
    for pn, p in enumerate(d):
        blocks = [b for b in p.get_text("dict")["blocks"] if b["type"] == 1]
        words = p.get_text("words")
        for b in sorted(blocks, key=lambda b: (round(b["bbox"][1] / 50), b["bbox"][0])):
            x0, y0, x1, y1 = b["bbox"]
            if x1 - x0 < 80:
                continue
            w = [t for t in words if x0 - 6 <= t[0] and t[2] <= x0 + 194 and y1 - 2 <= t[1] <= y1 + 105]
            ln = collections.defaultdict(list)
            for t in w:
                ln[round(t[1] / 4)].append(t)
            txt = [" ".join(t[4] for t in sorted(v, key=lambda t: t[0])) for k, v in sorted(ln.items())]
            price = per = None
            name = []
            for s in txt:
                m = PRICE.match(s)
                if m:
                    price = float(m.group(1).replace(",", "")); per = int(m.group(2))
                elif price is not None:
                    name.append(s)
            if len(name) < 2 or price is None:
                continue
            code = name.pop()
            items.append(dict(page=pn + 1, sup=code, raw=" ".join(name), cost=price, per=per, img=b["image"], ext=b["ext"]))
    return items

# ---------------------------------------------------------------- name + group
UNITS = {"ML", "LTR", "PC", "PCS", "SET", "CTN", "KFT", "DELI", "YUJING", "BLINKMAX", "BLMAX", "DS", "GK", "G", "K", "PET", "SS"}
def pretty(s):
    out = []
    for w in s.split():
        if any(c.isdigit() for c in w) or w.upper() in UNITS or not w.isalpha():
            out.append(w)
        else:
            out.append(w.capitalize())
    return " ".join(out)

def display_name(raw, sup):
    n = re.sub(r"^" + re.escape(sup) + r"\s*", "", raw)
    n = re.sub(r"\s+" + re.escape(sup) + r"$", "", n)
    carton = None
    m = re.search(r"[-\s]*(\d+)\s*SET\s*CTN\b", n, re.I)
    if m:
        carton = int(m.group(1)); n = n[:m.start()] + n[m.end():]
    n = re.sub(r"\b(IN\s+)?(COL|BROWN|KFT|GIFT|WHITE)?\s*BOX\b\s*-?$", "", n.strip(), flags=re.I)
    n = re.sub(r"\s+", " ", n).strip(" -")
    return pretty(n), carton

RULES = [
    ("Beer Mugs", r"BEER MUG|BEER GLASS|PILSNER"),
    ("Whisky Glasses", r"WHISKY|WHISKEY|ROCK GLASS|OLD FASHION"),
    ("Wine Glasses", r"WINE|CHAMPAGNE|GOBLET|FLUTE"),
    ("Shot Glasses", r"SHOT"),
    ("Jars & Storage", r"\bJAR\b|CANISTER|STORAGE|\bTANK\b"),
    ("Bottles", r"BOTTLE|DECANTER"),
    ("Jugs & Water Sets", r"\bJUG\b|CARAFFE|CARRAFE|PITCHER|LEMON SET|WATER SET|KETTLE"),
    ("Bowls & Bowl Sets", r"BOWL|PUDDING|SALAD|DESSERT"),
    ("Plates & Dinner Sets", r"PLATE|DINNER|SNACK SET|TRAY|SAUCER"),
    ("Juice & Water Glasses", r"JUICE|WATER GLASS|TUMBLER|HIGHBALL|DRINKING|SHERBET|LASSI"),
    ("Cups & Mugs", r"\bMUG\b|\bCUP\b|COFFEE|\bTEA\b"),
    ("Drinking Glasses", r"\bGLASS(ES)?\b"),
]
RX = [(g, re.compile(p, re.I)) for g, p in RULES]
def group_of(raw):
    for g, rx in RX:
        if rx.search(raw):
            return g
    return None

# ---------------------------------------------------------------- price
def sell_price(cost, mult):
    return int(round(cost * mult / STEP) * STEP)

# ---------------------------------------------------------------- barcode
def modules(code):
    return Code128(code).build()[0]           # '1010...' string

def draw_barcode(page, x, y, w, h, code):
    m = modules(code)
    mw = w / len(m)
    i = 0
    while i < len(m):
        if m[i] == "1":
            j = i
            while j < len(m) and m[j] == "1":
                j += 1
            page.draw_rect(fitz.Rect(x + i * mw, y, x + j * mw, y + h), color=None, fill=(0, 0, 0))
            i = j
        else:
            i += 1

FONT = fitz.Font("helv")
FONTB = fitz.Font("hebo")
def wrap(text, size, width, maxlines, font=FONT):
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if font.text_length(t, size) <= width:
            cur = t
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    if len(lines) > maxlines:
        lines = lines[:maxlines]
        l = lines[-1]
        while font.text_length(l + "..", size) > width and len(l) > 1:
            l = l[:-1]
        lines[-1] = l.rstrip() + ".."
    return lines

def put(page, x, y, text, size, bold=False, color=(0, 0, 0), anchor="l", width=None):
    f = FONTB if bold else FONT
    tw = f.text_length(text, size)
    if anchor == "c" and width:
        x = x + (width - tw) / 2
    elif anchor == "r" and width:
        x = x + width - tw
    page.insert_text((x, y), text, fontsize=size, fontname="hebo" if bold else "helv", color=color)

# ---------------------------------------------------------------- catalog pdf
def make_catalog(items, path):
    doc = fitz.open()
    W, H = 595, 842
    # cover
    pg = doc.new_page(width=W, height=H)
    pg.draw_rect(fitz.Rect(0, 0, W, 300), color=None, fill=(0.14, 0.32, 0.42))
    put(pg, 40, 150, "SAMPLE TRADERS", 13, True, (0.85, 0.92, 0.95))
    put(pg, 40, 200, "Glassware Price List", 34, True, (1, 1, 1))
    put(pg, 40, 232, f"{len(items)} items  |  prices valid till 31 Oct 2026", 12, False, (0.85, 0.92, 0.95))
    put(pg, 40, 340, "Prices are per pack as shown. Taxes extra. Sample catalog generated for testing.", 10, False, (0.3, 0.3, 0.3))
    groups = collections.OrderedDict()
    for it in sorted(items, key=lambda i: (i["group"], i["code"])):
        groups.setdefault(it["group"], []).append(it)
    y = 380
    put(pg, 40, y, "In this catalog", 11, True)
    for g, lst in groups.items():
        y += 18
        put(pg, 40, y, g, 10)
        put(pg, 260, y, f"{len(lst)} items", 10, False, (0.4, 0.4, 0.4))
    # grid pages
    M, GAP = 28, 10
    CW = (W - 2 * M - 2 * GAP) / 3
    IH = CW * 262 / 388
    CH = IH + 62
    for g, lst in groups.items():
        for start in range(0, len(lst), 12):
            pg = doc.new_page(width=W, height=H)
            pg.draw_rect(fitz.Rect(0, 0, W, 34), color=None, fill=(0.14, 0.32, 0.42))
            put(pg, M, 22, g, 13, True, (1, 1, 1))
            put(pg, W - M - 120, 22, "Sample Traders", 9, False, (0.85, 0.92, 0.95), "r", 120)
            for k, it in enumerate(lst[start:start + 12]):
                r, c = divmod(k, 3)
                x = M + c * (CW + GAP)
                y0 = 46 + r * (CH + 8)
                pg.insert_image(fitz.Rect(x, y0, x + CW, y0 + IH), stream=it["img"])
                pg.draw_rect(fitz.Rect(x, y0, x + CW, y0 + CH), color=(0.85, 0.85, 0.85), fill=None, width=0.5)
                ty = y0 + IH + 11
                for ln in wrap(it["display"], 8, CW - 8, 2):
                    put(pg, x + 4, ty, ln, 8)
                    ty += 9.5
                put(pg, x + 4, y0 + CH - 6, it["code"], 7, False, (0.4, 0.4, 0.4))
                put(pg, x + 4, y0 + CH - 6, f"Rs {it['sell']}", 12, True, (0.14, 0.32, 0.42), "r", CW - 8)
                if it["per"] > 1:
                    put(pg, x + 4, y0 + CH - 17, f"for {it['per']} pcs", 7, False, (0.4, 0.4, 0.4), "r", CW - 8)
    doc.set_metadata({"title": "Glassware Price List (sample)"})
    doc.save(path)
    return len(doc)

# ---------------------------------------------------------------- label pdf
def make_labels(items, path):
    doc = fitz.open()
    LW, LH = 50 * 72 / 25.4, 25 * 72 / 25.4          # 141.7 x 70.9 pt
    for it in items:
        pg = doc.new_page(width=LW, height=LH)
        for k, ln in enumerate(wrap(it["display"], 5.6, LW - 8, 2)):
            put(pg, 4, 8 + k * 6.4, ln, 5.6, False, (0, 0, 0))
        draw_barcode(pg, 14, 23, LW - 28, 26, it["code"])
        put(pg, 4, LH - 5, it["code"], 6.5, True)
        put(pg, 4, LH - 5, f"Rs {it['sell']}", 8, True, (0, 0, 0), "r", LW - 8)
    doc.save(path)
    return len(doc)

def verify_labels(path, items):
    d = fitz.open(path)
    ok = 0
    bad = []
    for it, p in zip(items, d):
        pix = p.get_pixmap(dpi=300)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        res = [r.text for r in zxingcpp.read_barcodes(img)]
        if it["code"] in res:
            ok += 1
        else:
            bad.append((it["code"], res))
    return ok, bad

# ---------------------------------------------------------------- tally xml
def tally_xml(items, company, with_part=True):
    groups = sorted({i["group"] for i in items})
    out = ['<ENVELOPE><HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER><BODY><IMPORTDATA><REQUESTDESC><REPORTNAME>All Masters</REPORTNAME>'
           f'<STATICVARIABLES><SVCURRENTCOMPANY>{escape(company)}</SVCURRENTCOMPANY></STATICVARIABLES></REQUESTDESC><REQUESTDATA>']
    out.append('<TALLYMESSAGE xmlns:UDF="TallyUDF"><UNIT NAME="Nos" ACTION="Create"><NAME>Nos</NAME><ISSIMPLEUNIT>Yes</ISSIMPLEUNIT></UNIT></TALLYMESSAGE>')
    out.append(f'<TALLYMESSAGE xmlns:UDF="TallyUDF"><STOCKGROUP NAME="{escape(ROOT_GROUP)}" ACTION="Create"><NAME>{escape(ROOT_GROUP)}</NAME><PARENT></PARENT></STOCKGROUP></TALLYMESSAGE>')
    for g in groups:
        out.append(f'<TALLYMESSAGE xmlns:UDF="TallyUDF"><STOCKGROUP NAME="{escape(g)}" ACTION="Create"><NAME>{escape(g)}</NAME><PARENT>{escape(ROOT_GROUP)}</PARENT></STOCKGROUP></TALLYMESSAGE>')
    for it in items:
        n = escape(it["tally_name"])
        part = (f'<LANGUAGENAME.LIST><NAME.LIST TYPE="String"><NAME>{n}</NAME><NAME>{escape(it["code"])}</NAME></NAME.LIST><LANGUAGEID>1033</LANGUAGEID></LANGUAGENAME.LIST>'
                f'<PARTNO>{escape(it["code"])}</PARTNO>') if with_part else ""
        out.append(f'<TALLYMESSAGE xmlns:UDF="TallyUDF"><STOCKITEM NAME="{n}" ACTION="Create"><NAME>{n}</NAME><PARENT>{escape(it["group"])}</PARENT><BASEUNITS>Nos</BASEUNITS>{part}</STOCKITEM></TALLYMESSAGE>')
    out.append('</REQUESTDATA></IMPORTDATA></BODY></ENVELOPE>')
    return "".join(out)

# ---------------------------------------------------------------- main
if __name__ == "__main__":
    allitems = extract(SRC)
    print("extracted", len(allitems))
    # coverage of group rules on the FULL file
    cov = collections.Counter(group_of(i["raw"]) for i in allitems)
    unc = cov[None]
    print(f"group rules on all {len(allitems)}: classified {len(allitems)-unc} ({100*(len(allitems)-unc)/len(allitems):.1f}%), none {unc}")
    for g, c in cov.most_common():
        print(f"   {c:5d}  {g}")
    print("   sample unclassified:", [i["raw"][:48] for i in allitems if group_of(i["raw"]) is None][:8])

    # 100 evenly spaced
    idx = sorted({round(k * len(allitems) / N) for k in range(N)})[:N]
    sample = [allitems[i] for i in idx]
    seen = collections.Counter()
    for n, it in enumerate(sample, 1):
        it["code"] = f"GL-{n+700:06d}"
        it["display"], it["carton"] = display_name(it["raw"], it["sup"])
        it["group"] = group_of(it["raw"]) or "Other Glassware"
        mult = 1.40 if n % 17 == 0 else MULT
        it["mult"] = mult
        it["sell"] = sell_price(it["cost"], mult)
        seen[it["display"].lower()] += 1
    for it in sample:
        it["tally_name"] = it["display"] if seen[it["display"].lower()] == 1 else f'{it["display"]} ({it["sup"]})'
    dup = sum(1 for it in sample if seen[it["display"].lower()] > 1)
    print(f"\nsample {len(sample)} items; groups:", dict(collections.Counter(i["group"] for i in sample)))
    print("name collisions needing supplier-code suffix:", dup)
    for it in sample[:5]:
        print("  ", it["code"], "|", it["tally_name"], "|", it["group"], "| cost", it["cost"], "->", it["sell"], "| per", it["per"], "| carton", it["carton"])

    pages = make_catalog(sample, OUT + "/catalog.pdf")
    labels = make_labels(sample, OUT + "/labels.pdf")
    print(f"\ncatalog.pdf {pages} pages; labels.pdf {labels} labels")
    ok, bad = verify_labels(OUT + "/labels.pdf", sample)
    print(f"barcode decode check: {ok}/{len(sample)} labels decode to their own code", bad[:3])

    open(OUT + "/tally_masters.xml", "w", encoding="utf-8").write(tally_xml(sample, "COMPANY_PLACEHOLDER"))
    manifest = [{k: v for k, v in it.items() if k not in ("img",)} for it in sample]
    json.dump(manifest, open(OUT + "/manifest.json", "w"), indent=1)
    for it in sample[:12]:
        open(OUT + f"/img_{it['code']}.{it['ext']}", "wb").write(it["img"])
    print("wrote", sorted(os.listdir(OUT))[:6], "...")
