import sys, json
sys.path.insert(0, "lib")
from tg import post, collection, re
import catalog_spike as cs
def summarize(resp):
    g=lambda t: re.findall(r"<%s>(\d+)</%s>"%(t,t),resp)
    return dict(created=g("CREATED"),altered=g("ALTERED"),errors=g("ERRORS"),exceptions=g("EXCEPTIONS"),
                lineerror=re.findall(r"<LINEERROR>([^<]*)</LINEERROR>",resp)[:6])
def run(items, part=True, company="Fleek"):
    xml = cs.tally_xml(items, company, with_part=part)
    r = post(xml)
    return summarize(r), r
def readback(names, company="Fleek"):
    x = collection("StockItem", ["Name","Parent","BaseUnits","PartNumber"], company=company)
    out = {}
    for m in re.finditer(r'<STOCKITEM NAME="([^"]*)".*?</STOCKITEM>', x, re.S):
        blk = m.group(0); nm = m.group(1)
        if nm in names:
            out[nm] = dict(parent=(re.search(r"<PARENT[^>]*>([^<]*)<",blk) or [None,None])[1],
                           unit=(re.search(r"<BASEUNITS[^>]*>([^<]*)<",blk) or [None,None])[1],
                           part=(re.search(r"<PARTNUMBER[^>]*>([^<]*)<",blk) or [None,None])[1])
    return out
if __name__ == "__main__":
    mode = sys.argv[1]
    m = json.load(open("out100/manifest.json"))
    if mode == "probe":
        sub = [m[0], m[1]]
        s, raw = run(sub)
        print("probe import:", s)
        if s["lineerror"] or s["errors"] not in ([], ["0"]): print(raw[:1500])
        print("readback:", json.dumps(readback({i["tally_name"] for i in sub}), indent=1))
        gx = collection("StockGroup", ["Name","Parent"], company="Fleek")
        print("groups:", re.findall(r'<STOCKGROUP NAME="([^"]*)"', gx))
