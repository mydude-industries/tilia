"""Rehearsal assignments: open on /practice, close on /done, follow up 3 days later."""

from datetime import timedelta

from .coach import end_rehearsal

FOLLOW_UP_TEXT = "follow up: how did the real conversation go?"
FOLLOW_UP_KIND = "followup"  # not a practice to run: a question to ask after one
FOLLOW_UP_DAYS = 3


def start_rehearsal(store, description: str) -> int:
    if store.get_meta("mode") == "rehearsal" or store.get_meta("rehearsal_assignment_id"):
        end_rehearsal(store, "dropped")  # never orphan a live rehearsal
    aid = store.add_assignment("rehearsal", description, None)
    store.set_meta("rehearsal_role", description)
    store.set_meta("rehearsal_assignment_id", str(aid))
    store.set_meta("mode", "rehearsal")
    return aid


def finish_rehearsal(store, now) -> None:
    """Close the rehearsal as done and leave a challenge-free follow-up due in 3 days."""
    end_rehearsal(store, "done")
    store.add_assignment(FOLLOW_UP_KIND, FOLLOW_UP_TEXT,
                         (now + timedelta(days=FOLLOW_UP_DAYS)).isoformat())
