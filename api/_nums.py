import time
from fastapi.testclient import TestClient
from app.main import app
from tests.conftest import _SessionTest  # noqa
from app.db import Base, engine
Base.metadata.create_all(engine)
with engine.begin() as conn:
    for t in reversed(Base.metadata.sorted_tables): conn.execute(t.delete())

c = TestClient(app)
r = c.post("/api/auth/register", json={"firm_name":"Sethia","email":"nums@example.com","password":"s3cret-pass"})
h = {"Authorization": f"Bearer {r.json()['access_token']}"}

t=time.time()
with open(r"C:\tmp\Master (3).xml","rb") as fh:
    r = c.post("/api/items/import?seed_all_hsn=true", headers=h, files={"file":("m.xml",fh,"text/xml")})
print(f"UPLOAD  {r.status_code}  {time.time()-t:.1f}s")
b = r.json()
print(f"  staged={b['total']}  dummies={b['dummies_skipped']}  groups={len(b['groups'])}")

t=time.time()
rev = c.get(f"/api/items/import/{b['batch_id']}", headers=h).json()
print(f"REVIEW  {time.time()-t:.1f}s   counts={rev['counts']}")
from collections import Counter
fc = Counter(f["code"] for row in rev["rows"] for f in row["flags"])
print(f"  flag codes (informational): {dict(fc)}")

t=time.time()
out = c.post(f"/api/items/import/{b['batch_id']}/commit", headers=h).json()
print(f"COMMIT  {time.time()-t:.1f}s   {out}")
