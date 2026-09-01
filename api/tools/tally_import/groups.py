"""Resolve a Tally ledger's group lineage to a party role.

A ledger's ultimate parent (walking <PARENT> links) decides:
  Sundry Debtors lineage   -> customer
  Sundry Creditors lineage -> supplier
  both                     -> both
  neither                  -> None  (only imported if the user ticks that group)
"""

from __future__ import annotations

from app.models._mixins import PartyRole
from tools.tally_import.parser import TallyGroup

DEBTOR_ROOTS = {"sundry debtors"}
CREDITOR_ROOTS = {"sundry creditors"}


class GroupTree:
    def __init__(self, groups: list[TallyGroup]) -> None:
        self._parent: dict[str, str | None] = {}
        for g in groups:
            self._parent[g.name.strip().lower()] = (
                g.parent.strip().lower() if g.parent else None
            )

    def roots_of(self, group_name: str | None) -> set[str]:
        """Every ancestor name (lowercased), including the group itself."""
        seen: set[str] = set()
        cur: str | None = (group_name or "").strip().lower()
        while cur and cur not in seen:
            seen.add(cur)
            cur = self._parent.get(cur)
        return seen

    def role_for(self, group_name: str | None) -> PartyRole | None:
        anc = self.roots_of(group_name)
        is_debtor = bool(anc & DEBTOR_ROOTS)
        is_creditor = bool(anc & CREDITOR_ROOTS)
        if is_debtor and is_creditor:
            return PartyRole.both
        if is_debtor:
            return PartyRole.customer
        if is_creditor:
            return PartyRole.supplier
        return None

    def top_group(self, group_name: str | None) -> str | None:
        """The name (original case unknown here, so lowercased) of the outermost
        group, for the 'pick extra groups' list.
        """
        cur = (group_name or "").strip().lower()
        last = cur or None
        seen: set[str] = set()
        while cur and cur not in seen:
            seen.add(cur)
            nxt = self._parent.get(cur)
            if not nxt:
                last = cur
                break
            cur = nxt
        return last
