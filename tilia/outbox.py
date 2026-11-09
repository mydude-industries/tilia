"""The outbox: every message the coach writes waits here until Telegram takes it.

The loop flushes it (oldest first, retried every 30 s for up to 6 h), so a Telegram blip
never loses a reply. Kept in the store's meta table, so it commits with the turn it answers."""

import json

from . import telegram


def enqueue(store, text: str, now, markup=None) -> None:
    box = json.loads(store.get_meta("outbox") or "[]")
    item = {"text": text, "since": now.isoformat()}
    if markup is not None:
        item["markup"] = markup
    box.append(item)
    store.set_meta("outbox", json.dumps(box))


def enqueue_reply(store, r, now) -> None:
    """A coach reply, with its worksheet button if it has one. When the personalized
    wording can't ride in the button (webapp.FRAGMENT_WORDING off), it follows the reply
    as its own message, carrying the button. In a crisis (this reply's state, or an open
    episode) any earlier "Open" button is taken away: the page is static, so only this
    side can stop a card or worksheet being opened mid-crisis."""
    from . import coach
    offer = r.offer
    if offer is not None and not offer.preface:
        markup = offer.markup  # a new keyboard replaces any old one anyway
    elif r.remove_keyboard or r.state == "crisis" or coach.in_episode(store, now):
        markup = telegram.REMOVE_KEYBOARD
    else:
        markup = None
    enqueue(store, r.reply, now, markup=markup)
    if offer is not None and offer.preface:
        enqueue(store, offer.preface, now, markup=offer.markup)
