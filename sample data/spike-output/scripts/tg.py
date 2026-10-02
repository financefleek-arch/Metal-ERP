import urllib.request, re
URL="http://localhost:9000"
def post(xml:str)->str:
    req=urllib.request.Request(URL,data=xml.encode("utf-8"),headers={"Content-Type":"text/xml; charset=utf-8"})
    return urllib.request.urlopen(req,timeout=120).read().decode("utf-8","replace")
def collection(ctype, fields, company=None, extra=""):
    sv=f"<SVCURRENTCOMPANY>{company}</SVCURRENTCOMPANY>" if company else ""
    f="".join(f"<NATIVEMETHOD>{x}</NATIVEMETHOD>" for x in fields)
    return post(f"""<ENVELOPE><HEADER><VERSION>1</VERSION><TALLYREQUEST>Export</TALLYREQUEST><TYPE>Collection</TYPE><ID>MyColl</ID></HEADER>
<BODY><DESC><STATICVARIABLES><SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>{sv}</STATICVARIABLES>
<TDL><TDLMESSAGE><COLLECTION NAME="MyColl" ISMODIFY="No"><TYPE>{ctype}</TYPE>{f}{extra}</COLLECTION></TDLMESSAGE></TDL></DESC></BODY></ENVELOPE>""")
