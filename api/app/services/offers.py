"""A shop sends its new range to the customers who agreed, as a WhatsApp carousel.

See docs/PLAN-fleek-control-plane.md (the composer).

The shop picks items on the Items page and presses Send. Everything else is a default worked out from what the
shop already has:

  * the catalogue behind the button is a share link over the picked items (one the shop could already make);
  * the cards are the best sellers among them that have a photo, as many as the offer message has cards;
  * the audience is the customers who agreed to offers; Fleek's CRM gate has the last word (STOP, block, cap);
  * the pictures are square JPEG copies of the item photos, on links valid for the whole paced send.

Fleek's backend sends (the CRM engine, from the shop's own number); this app only assembles the request.
Nothing here talks to Meta.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.models import Item, MediaAsset, Tenant
from app.schemas_customer_catalog import CatalogLayout, CatalogSelection
from app.services import catalog_consent, crm_sync, fleek_messages
from app.services import media as media_svc
from app.services import share_links as share_links_svc
from app.services.catalog import item_catalogs
from app.services.catalog.storage import CatalogStorage
from app.services.item_filter import resolve_ids
from app.services.media_urls import card_url

log = logging.getLogger(__name__)

# What Meta charges for one marketing message in India, before GST, as most 2026 sources give it. Shown as
# "about": the shop is billed by Meta directly, and the rate can change.
APPROX_RUPEES_PER_MESSAGE = 0.86


def _clean(value: str) -> str:
    """One line, single spaces: a title is shown inside a message and on a link page."""
    return " ".join(str(value).split())


class OfferRefused(Exception):
    """The request cannot go ahead. `status` is the HTTP answer; str() is a sentence the shop can act on."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class Card:
    item_id: str
    name: str
    price: str
    media_id: str
    values: list[str] = field(default_factory=list)


@dataclass
class OfferPlan:
    selected: int = 0
    offered: int = 0  # in stock and priced: what the customer's catalogue will show
    with_photo: int = 0
    without_photo: int = 0
    template: dict[str, Any] | None = None
    cards: list[Card] = field(default_factory=list)
    audience: dict[str, int] = field(default_factory=dict)
    allowance: dict[str, int] | None = None
    blockers: list[str] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return not self.blockers


# --------------------------------------------------------------------------- pure


def rank_cards(items: list[Item], n_cards: int) -> list[Item]:
    """Pure. The items with a photo, best sellers first (most billed), then by name; at most `n_cards`."""
    with_photo = [it for it in items if it.primary_media_id]
    with_photo.sort(key=lambda it: (-(it.times_billed or 0), it.name.casefold(), it.id))
    return with_photo[:n_cards]


# the card line of an offer message is either fixed text (0 variables) or the item's name and price (2)
CARD_VARIABLES_SUPPORTED = (0, 2)


def pick_template(templates: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, str | None]:
    """Pure. -> (the offer message to use, None) or (None, why there is none).

    Among the ready ones, the message whose cards name the item and its price comes first."""
    usable = [t for t in templates if int(t.get("card_variables") or 0) in CARD_VARIABLES_SUPPORTED]
    if not usable:
        return None, "No offer message is set up for your shop yet. Ask Fleek."
    ready = [t for t in usable if t.get("ready")]
    if not ready:
        return None, "WhatsApp is still checking your offer message. It usually takes a day."
    ready.sort(key=lambda t: -int(t.get("card_variables") or 0))
    return ready[0], None


def price_text(rate: object) -> str:
    """Pure. A price for a card's line: 640, or 640.50 (the rupee sign is in the message)."""
    value = Decimal(str(rate or 0)).quantize(Decimal("0.01"))
    return f"{value:.0f}" if value == value.to_integral_value() else f"{value:.2f}"


def card_values(name: str, rate: object) -> list[str]:
    """Pure. What fills a card's line of text: the item's name (kept short) and its price."""
    return [_clean(name)[:60], price_text(rate)]


def blockers_for(plan: OfferPlan, n_cards: int) -> list[str]:
    """Pure. Plain sentences for what stops a send; empty means it can go."""
    out: list[str] = []
    if plan.offered == 0:
        out.append(
            "None of the picked items can be shown to customers (they need a price and to be in stock)."
        )
    elif n_cards and plan.with_photo < n_cards:
        out.append(
            f"The offer message shows {n_cards} items with photos. "
            f"Your picked items have {plan.with_photo} with a photo. Add photos or pick more items."
        )
    if not plan.audience.get("agreed"):
        out.append("No customer has agreed to offers on WhatsApp yet.")
    return out


# --------------------------------------------------------------------------- Fleek


def _fleek_org(tenant: Tenant) -> str:
    if not tenant.fleek_org_id:
        raise OfferRefused(409, "Offers on WhatsApp are not set up for this firm yet. Ask Fleek.")
    return tenant.fleek_org_id


def _ask(method: str, tenant: Tenant, suffix: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """One signed call to Fleek; its refusal becomes an OfferRefused with Fleek's own sentence."""
    try:
        status, body = fleek_messages.request(method, _fleek_org(tenant), suffix, payload)
    except fleek_messages.FleekUnavailable as exc:
        raise OfferRefused(502, "Fleek could not be reached. Try again in a moment.") from exc
    if 200 <= status < 300:
        return body
    reason = str(body.get("message") or body.get("detail") or "That did not work.")
    if status == 404 or body.get("error") == "campaigns_not_available":
        raise OfferRefused(409, "Offers on WhatsApp are not part of this firm's plan yet. Ask Fleek.")
    raise OfferRefused(409 if status < 500 else 502, reason)


# --------------------------------------------------------------------------- the plan


def _offered_items(
    session: Session, tenant_id: str, selection: CatalogSelection
) -> tuple[int, item_catalogs.Plan]:
    ids = resolve_ids(session, tenant_id, selection.ids, selection.filter)
    if not ids:
        raise OfferRefused(422, "Pick the items to send first.")
    return len(ids), item_catalogs.plan(session, tenant_id, ids, CatalogLayout(include_expected=False))


def plan_offer(session: Session, tenant: Tenant, selection: CatalogSelection) -> OfferPlan:
    """What a send would do, without doing it: for the composer's one screen."""
    selected, catalog = _offered_items(session, tenant.id, selection)
    plan = OfferPlan(selected=selected, offered=len(catalog.items))
    plan.with_photo = sum(1 for it in catalog.items if it.primary_media_id)
    plan.without_photo = plan.offered - plan.with_photo
    plan.audience = catalog_consent.offers_counts(session, tenant.id)

    try:
        templates = _ask("GET", tenant, "campaigns/templates").get("templates") or []
        allowance = _ask("GET", tenant, "campaigns/allowance")
    except OfferRefused as exc:
        plan.blockers.append(str(exc))
        return plan
    plan.allowance = {k: int(allowance.get(k) or 0) for k in ("cap", "used")}
    plan.template, why = pick_template(templates)
    n_cards = int(plan.template["cards"]) if plan.template else 0
    if why:
        plan.blockers.append(why)
    plan.cards = [
        Card(
            it.id,
            it.name,
            f"{float(it.default_rate or 0):.2f}",
            it.primary_media_id or "",
            card_values(it.name, it.default_rate),
        )
        for it in rank_cards(catalog.items, n_cards)
    ]
    plan.blockers += blockers_for(plan, n_cards)
    if plan.allowance and plan.audience.get("agreed", 0) > max(
        0, plan.allowance["cap"] - plan.allowance["used"]
    ):
        plan.blockers.append("This would pass your plan's monthly allowance of offer messages.")
    return plan


# --------------------------------------------------------------------------- send


def send_offer(
    session: Session,
    tenant: Tenant,
    storage: CatalogStorage,
    *,
    user_id: str,
    selection: CatalogSelection,
    title: str,
    allow_pieces: bool = False,
) -> dict[str, Any]:
    """Make the catalogue link, push the customers, ask Fleek to start the campaign.

    -> the campaign as Fleek reports it."""
    plan = plan_offer(session, tenant, selection)
    if not plan.ready or plan.template is None:
        raise OfferRefused(422, plan.blockers[0] if plan.blockers else "This cannot be sent yet.")

    cards: list[dict[str, str]] = []
    for card in plan.cards:
        asset = session.get(MediaAsset, card.media_id)
        if asset is None or media_svc.card_jpeg(storage, asset) is None:
            raise OfferRefused(422, f"The photo of {card.name} could not be read. Replace it and try again.")
        entry: dict[str, Any] = {"image_url": card_url(card.media_id)}
        if int(plan.template.get("card_variables") or 0):
            entry["values"] = card.values
        cards.append(entry)

    name = _clean(title)[:120] or "New range"
    link = share_links_svc.create(
        session,
        tenant_id=tenant.id,
        user_id=user_id,
        title=name,
        selection=selection,
        include_expected=False,
        expires_at=None,
        allow_pieces=allow_pieces,
    )
    try:
        crm_sync.push(
            session, tenant
        )  # tokens and consent must be fresh in the CRM before it picks the audience
    except crm_sync.SyncError as exc:
        raise OfferRefused(502, f"Your customers could not be sent to Fleek: {exc}") from exc
    session.commit()  # the link and the tokens are now known to Fleek: keep them whatever happens next

    body = _ask(
        "POST",
        tenant,
        "campaigns",
        {
            "name": name,
            "template": plan.template["ref"],
            "catalog_link": link.token,
            "cards": cards,
            "start": True,
            "requested_by": user_id,
        },
    )
    out = dict(body.get("campaign") or {})
    out["link_token"] = link.token
    out["approx_cost"] = round(int(out.get("recipients") or 0) * APPROX_RUPEES_PER_MESSAGE, 2)
    return out


def campaign_status(tenant: Tenant, campaign_id: str) -> dict[str, Any]:
    return _ask("GET", tenant, f"campaigns/{campaign_id}")


def campaign_action(tenant: Tenant, campaign_id: str, action: str, user_id: str) -> dict[str, Any]:
    return _ask("POST", tenant, f"campaigns/{campaign_id}/{action}", {"requested_by": user_id})
