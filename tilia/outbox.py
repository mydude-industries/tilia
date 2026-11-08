"""The outbox: every message the coach writes waits here until Telegram takes it.

The loop flushes it (oldest first, retried every 30 s for up to 6 h), so a Telegram blip
never loses a reply. Kept in the store's meta table, so it commits with the turn it answers."""

import json


def enqueue(store, text: str, now) -> None:
    box = json.loads(store.get_meta("outbox") or "[]")
    box.append({"text": text, "since": now.isoformat()})
    store.set_meta("outbox", json.dumps(box))
