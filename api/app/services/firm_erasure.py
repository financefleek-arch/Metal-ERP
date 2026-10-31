"""Erase a firm and every trace of it (platform-admin "Delete shop").

The set of tables is read from the ORM metadata, not listed by hand, so a table added later is
covered without touching this file: a table is the firm's if it points at `tenant`, or at any table
that is. Rows go child-first (reverse FK order), then files in R2, then the firm row itself.

Files are removed only after the database delete has committed, and best-effort: a storage failure
is reported back, never rolls the delete back.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy import Table, delete, or_, select, update
from sqlalchemy.orm import Session

from app import backup_storage
from app.db import Base
from app.models import BackupShop, BackupUpload, OtpChallenge, SignupRequest, TallySyncJob, Tenant, User
from app.services.catalog.storage import CatalogStorage

log = logging.getLogger(__name__)

# Folders in object storage that are keyed by tenant id (see media_key / image_key).
_PREFIXES = ("media/{t}", "catalog/{t}")


class ErasureRefused(Exception):
    """The firm may not be deleted (operator tenant)."""


@dataclass
class ErasureReport:
    rows: dict[str, int] = field(default_factory=dict)
    files_removed: int = 0
    files_failed: list[str] = field(default_factory=list)

    @property
    def total_rows(self) -> int:
        return sum(self.rows.values())


def _tenant_tables() -> dict[str, Table]:
    """Every table whose rows belong to a firm: a path of foreign keys leads back to `tenant`."""
    tables = Base.metadata.tables
    owned: set[str] = {"tenant"}
    grew = True
    while grew:
        grew = False
        for name, table in tables.items():
            if name in owned:
                continue
            if any(fk.column.table.name in owned for fk in table.foreign_keys):
                owned.add(name)
                grew = True
    return {n: tables[n] for n in owned}


def _scope(table: Table, firm_id: str, owned: dict[str, Table]):  # type: ignore[no-untyped-def]
    """WHERE clause: the rows of `table` that belong to the firm (any FK path counts)."""
    parts = []
    for fk in table.foreign_keys:
        parent = fk.column.table
        if parent.name not in owned or parent is table:
            continue
        if parent.name == "tenant":
            parts.append(fk.parent == firm_id)
        else:
            parts.append(fk.parent.in_(select(fk.column).where(_scope(parent, firm_id, owned))))
    return or_(*parts)


def _storage_keys(session: Session, firm_id: str) -> list[str]:
    shop_ids = select(BackupShop.id).where(BackupShop.tenant_id == firm_id)
    keys = [
        k
        for k in session.scalars(select(BackupUpload.r2_key).where(BackupUpload.shop_id.in_(shop_ids)))
    ]
    keys += [
        k
        for k in session.scalars(
            select(BackupShop.installer_r2_key).where(BackupShop.tenant_id == firm_id)
        )
        if k
    ]
    keys += [
        k
        for k in session.scalars(
            select(TallySyncJob.r2_key).where(_scope(TallySyncJob.__table__, firm_id, _tenant_tables()))
        )
        if k
    ]
    return list(dict.fromkeys(keys))


def erase_firm(session: Session, firm: Tenant, storage: CatalogStorage) -> ErasureReport:
    if session.scalar(select(User.id).where(User.tenant_id == firm.id, User.is_platform_admin.is_(True))):
        raise ErasureRefused("This is the operator's own tenant; it cannot be deleted.")

    firm_id = firm.id
    owned = _tenant_tables()
    keys = _storage_keys(session, firm_id)
    report = ErasureReport()

    # one-time codes point at a user / sign-up by id only (no foreign key), so they are not found by the walk
    subjects = list(session.scalars(select(User.id).where(User.tenant_id == firm_id))) + list(
        session.scalars(select(SignupRequest.id).where(SignupRequest.tenant_id == firm_id))
    )
    if subjects:
        n = session.execute(delete(OtpChallenge).where(OtpChallenge.subject_id.in_(subjects))).rowcount
        if n:
            report.rows["otp_challenge"] = n

    # self-references (item.merged_into_id, supplier_catalog.supersedes_catalog_id) would block a
    # row-by-row order, so cut them first
    for table in owned.values():
        if table.name == "tenant":
            continue
        for fk in table.foreign_keys:
            if fk.column.table is table:
                session.execute(
                    update(table).where(_scope(table, firm_id, owned)).values({fk.parent.name: None})
                )

    session.expire_all()
    ordered = [t for t in Base.metadata.sorted_tables if t.name in owned and t.name != "tenant"]
    for table in reversed(ordered):
        result = session.execute(delete(table).where(_scope(table, firm_id, owned)))
        if result.rowcount:
            report.rows[table.name] = result.rowcount
    session.execute(delete(Tenant.__table__).where(Tenant.__table__.c.id == firm_id))
    report.rows["tenant"] = 1
    session.commit()

    # -- files, after the database is safely done --
    for prefix in _PREFIXES:
        try:
            report.files_removed += storage.delete_prefix(prefix.format(t=firm_id))
        except Exception as exc:  # noqa: BLE001
            log.warning("erase %s: could not clear %s: %s", firm_id, prefix, exc)
            report.files_failed.append(prefix.format(t=firm_id))
    for key in keys:
        try:
            backup_storage.delete_object(key)
            report.files_removed += 1
        except backup_storage.R2NotConfigured:
            break
        except Exception as exc:  # noqa: BLE001
            log.warning("erase %s: could not delete %s: %s", firm_id, key, exc)
            report.files_failed.append(key)
    return report
