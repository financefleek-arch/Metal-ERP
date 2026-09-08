"""Tally Connector (F1) services — masters-in staging, sync jobs, R2 fetch.

F1a: `staging.stage_masters_xml` / `staging.stage_stock_items_xml` parse a
Tally Prime masters export into the existing `staging_tally_party` /
`staging_tally_item` tables. Both the manual file-upload routes
(`/api/parties/import`, `/api/items/import`) and the Tally Connector pull
result-callback call these, so the parse -> match -> stage logic lives in
one place.
"""
