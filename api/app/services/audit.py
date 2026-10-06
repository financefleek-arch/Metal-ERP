"""One-line audit entries for bulk and outward actions (the log itself is `AuditLog`)."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditLog


def record(
    session: Session,
    *,
    tenant_id: str,
    user_id: str | None,
    entity: str,
    entity_id: str,
    action: str,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> None:
    """Add an entry; the caller commits with its own work. `entity_id` is a record id, or the
    firm's id for an action that spans many records."""
    session.add(
        AuditLog(
            tenant_id=tenant_id,
            actor_user_id=user_id,
            entity=entity,
            entity_id=entity_id,
            action=action,
            before_json=before,
            after_json=after,
        )
    )
