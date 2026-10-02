import sys,json,collections,time
sys.path.insert(0,"lib")
from tally_import import *
from catalog_spike import escape, ROOT_GROUP
m=json.load(open("out100/manifest.json"))
for it in m: it["tally_name"]="ZZTEST B3 "+it["tally_name"]
def env(msgs): return ('<ENVELOPE><HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER><BODY><IMPORTDATA><REQUESTDESC><REPORTNAME>All Masters</REPORTNAME>'
  '<STATICVARIABLES><SVCURRENTCOMPANY>Fleek</SVCURRENTCOMPANY></STATICVARIABLES></REQUESTDESC><REQUESTDATA>'+"".join(f'<TALLYMESSAGE xmlns:UDF="TallyUDF">{x}</TALLYMESSAGE>' for x in msgs)+'</REQUESTDATA></IMPORTDATA></BODY></ENVELOPE>')
groups=sorted({i["group"] for i in m})
msgs=['<UNIT NAME="Nos" ACTION="Create"><NAME>Nos</NAME><ISSIMPLEUNIT>Yes</ISSIMPLEUNIT></UNIT>',
      f'<STOCKGROUP NAME="{escape(ROOT_GROUP)}" ACTION="Create"><NAME>{escape(ROOT_GROUP)}</NAME><PARENT></PARENT></STOCKGROUP>']
msgs+=[f'<STOCKGROUP NAME="{escape(g)}" ACTION="Create"><NAME>{escape(g)}</NAME><PARENT>{escape(ROOT_GROUP)}</PARENT></STOCKGROUP>' for g in groups]
msgs+=[f'<STOCKITEM NAME="{escape(i["tally_name"])}" ACTION="Create"><NAME>{escape(i["tally_name"])}</NAME><PARENT>{escape(i["group"])}</PARENT><BASEUNITS>Nos</BASEUNITS><PARTNO>{escape(i["code"])}</PARTNO></STOCKITEM>' for i in m]
t=time.time(); r1=post(env(msgs)); print(f"PASS 1 create ({len(msgs)} masters, {time.time()-t:.1f}s):",summarize(r1))
alias=[f'<STOCKITEM NAME="{escape(i["tally_name"])}" ACTION="Alter"><NAME>{escape(i["tally_name"])}</NAME><LANGUAGENAME.LIST><NAME.LIST TYPE="String"><NAME>{escape(i["tally_name"])}</NAME><NAME>{escape(i["code"])}</NAME></NAME.LIST><LANGUAGEID>1033</LANGUAGEID></LANGUAGENAME.LIST></STOCKITEM>' for i in m]
t=time.time(); r2=post(env(alias)); print(f"PASS 2 alias ({len(alias)} items, {time.time()-t:.1f}s):",summarize(r2))
x=collection("StockItem",["*"],company="Fleek")
found=al=pn=0; par=collections.Counter(); missing=[]
for i in m:
    nm=escape(i["tally_name"])
    b=re.search(r'<STOCKITEM NAME="%s".*?</STOCKITEM>'%re.escape(nm),x,re.S)
    if not b: missing.append(i["tally_name"]); continue
    b=b.group(0); found+=1
    al+=bool(re.search(r"<NAME>%s</NAME>"%re.escape(i["code"]),b)); pn+=bool(re.search(r"<MAILINGNAME[^>]*>%s<"%re.escape(i["code"]),b))
    par[re.search(r"<PARENT[^>]*>([^<]*)<",b).group(1).replace("&amp;","&")]+=1
print(f"READ BACK: {found}/100 items | {al} code-as-alias | {pn} part-no | missing {missing[:3]}")
print(dict(par))
gx=collection("StockGroup",["Name","Parent"],company="Fleek"); print("stock groups now:",len(re.findall(r"<STOCKGROUP NAME",gx)))
json.dump(m,open("out100/manifest_tally.json","w"),indent=1)
