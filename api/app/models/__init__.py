"""SQLAlchemy models for Metal ERP.

The full schema is defined here from day one. Columns for later maturity
stages (GST, stock tracking, barcodes, weighbridge) exist now but are
nullable / defaulted and unused until their stage — so turning a stage on
is a code change, not a data-migrating ALTER on a populated table.

Importing this package registers every model on `Base.metadata`, which is
what `alembic/env.py` targets for autogenerate.
"""

from app.models.audit import AuditLog
from app.models.common import HsnCode, NumberSequence, Synonym
from app.models.invoice import Invoice, InvoiceLine
from app.models.inward import (
    ExtractionRun,
    InwardBill,
    InwardBillLine,
    Job,
    SupplierTemplate,
    TallyLedgerConfig,
)
from app.models.item import Item, ItemAlias, ProductGroup
from app.models.item_category import ItemCategory
from app.models.item_classify_rule import ItemClassifyRule
from app.models.party import Party, PartyAddress
from app.models.tally_agent import AgentOutboxItem, BackupShop, BackupUpload
from app.models.tally_import import StagingTallyParty
from app.models.tally_stock_import import StagingTallyItem
from app.models.tenant import Tenant, User
from app.models.whatsapp import TenantWhatsappConfig, WhatsappMessage

__all__ = [
    "AgentOutboxItem",
    "AuditLog",
    "BackupShop",
    "BackupUpload",
    "HsnCode",
    "NumberSequence",
    "Synonym",
    "ExtractionRun",
    "InwardBill",
    "InwardBillLine",
    "Job",
    "SupplierTemplate",
    "TallyLedgerConfig",
    "Invoice",
    "InvoiceLine",
    "Item",
    "ItemAlias",
    "ItemCategory",
    "ItemClassifyRule",
    "ProductGroup",
    "Party",
    "PartyAddress",
    "StagingTallyItem",
    "StagingTallyParty",
    "Tenant",
    "TenantWhatsappConfig",
    "User",
    "WhatsappMessage",
]
