"""SQLAlchemy models for Metal ERP.

The full schema is defined here from day one. Columns for later maturity
stages (GST, stock tracking, barcodes, weighbridge) exist now but are
nullable / defaulted and unused until their stage — so turning a stage on
is a code change, not a data-migrating ALTER on a populated table.

Importing this package registers every model on `Base.metadata`, which is
what `alembic/env.py` targets for autogenerate.
"""

from sqlalchemy.orm import column_property

from app.models.account import AuthAttempt, OtpChallenge, SignupRequest
from app.models.activity import ActivityEvent
from app.models.audit import AuditLog
from app.models.catalog import (
    CatalogOutputJob,
    CatalogProduct,
    CodeSequence,
    CustomerCatalog,
    SupplierCatalog,
    SupplierCatalogDefault,
    SupplierCatalogGroupMargin,
    SupplierCatalogItem,
    SupplierPricePoint,
)
from app.models.common import HsnCode, NumberSequence, Synonym
from app.models.entitlement import TenantEntitlement
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
from app.models.media import MediaAsset
from app.models.ordering import CatalogShareLink, CustomerOrder, CustomerOrderLine
from app.models.party import Party, PartyAddress
from app.models.payment import Payment, PaymentAllocation
from app.models.reminder import PaymentReminder
from app.models.slip_capture import SlipCapture
from app.models.tally_agent import AgentOutboxItem, BackupShop, BackupUpload
from app.models.tally_connector import (
    ItemTallyState,
    TallyCompany,
    TallyLink,
    TallySyncJob,
    default_item_state_expr,
    master_linked_expr,
)
from app.models.tally_import import StagingTallyParty
from app.models.tally_stock_import import StagingTallyItem
from app.models.tenant import Tenant, User
from app.models.whatsapp import TenantWhatsappConfig, WhatsappMessage

__all__ = [
    "ActivityEvent",
    "AgentOutboxItem",
    "AuditLog",
    "BackupShop",
    "BackupUpload",
    "CatalogOutputJob",
    "CatalogProduct",
    "CatalogShareLink",
    "PaymentReminder",
    "CustomerOrder",
    "CustomerOrderLine",
    "CodeSequence",
    "SupplierCatalogDefault",
    "SupplierPricePoint",
    "CustomerCatalog",
    "MediaAsset",
    "HsnCode",
    "NumberSequence",
    "Synonym",
    "SupplierCatalog",
    "SupplierCatalogGroupMargin",
    "SupplierCatalogItem",
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
    "Payment",
    "PaymentAllocation",
    "SlipCapture",
    "ItemTallyState",
    "StagingTallyItem",
    "StagingTallyParty",
    "TallyCompany",
    "TallyLink",
    "TallySyncJob",
    "Tenant",
    "TenantEntitlement",
    "AuthAttempt",
    "OtpChallenge",
    "SignupRequest",
    "TenantWhatsappConfig",
    "User",
    "WhatsappMessage",
]

# Linked to Tally in the firm's default company? A SQL-side flag on the record (S1.2), so lists and
# filters need no join of their own.
Item.tally_linked = column_property(master_linked_expr(Item, "item"))  # type: ignore[attr-defined]
# The item's Tally push state, read from its firm's default company (S1.2b). Read-only: a push writes
# `item_tally_state` for the company it ran against (services/tally/item_state.py).
Item.tally_status = column_property(default_item_state_expr(Item, "status"))  # type: ignore[attr-defined]
Item.tally_price = column_property(default_item_state_expr(Item, "price"))  # type: ignore[attr-defined]
Item.tally_seen_price = column_property(default_item_state_expr(Item, "seen_price"))  # type: ignore[attr-defined]
Item.tally_pushed_at = column_property(default_item_state_expr(Item, "pushed_at"))  # type: ignore[attr-defined]
