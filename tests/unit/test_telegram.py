"""A0.4: other user_id ignored (and logged), /mode changes the mode, clicks recorded."""

from datetime import UTC, datetime

from app.notify.base import Button, Event
from app.notify.telegram import ModeStore, build_keyboard, handle_callback, handle_text, is_authorised

ME = 123456


class FakeStore:
    def __init__(self):
        self.mode = "reunion"
        self.until = None
        self.events = []

    def as_store(self) -> ModeStore:
        return ModeStore(set_mode=self._set, current_mode=lambda: self.mode, log_event=self._log)

    def _set(self, mode, until, source):
        self.mode, self.until = mode, until

    def _log(self, *a):
        self.events.append(a)


def test_is_authorised():
    assert is_authorised(ME, ME)
    assert not is_authorised(999, ME)
    assert not is_authorised(0, 0)  # unset user id never authorises anyone


def test_other_user_is_ignored_and_logged():
    st = FakeStore()
    r = handle_text("/mode absent", 999, ME, st.as_store())
    assert not r.handled and r.text == ""
    assert st.mode == "reunion"
    assert st.events == [(999, False, "command", "/mode absent", None, False)]


def test_mode_command_changes_mode_with_duration():
    st = FakeStore()
    now = datetime(2026, 9, 15, 6, 0, tzinfo=UTC)
    r = handle_text("/mode reunion 2h", ME, ME, st.as_store(), now=now)
    assert r.handled and st.mode == "reunion"
    assert st.until == datetime(2026, 9, 15, 8, 0, tzinfo=UTC)
    assert "12:00 Réunion (10:00 Paris)" in r.text


def test_invalid_mode_command_gives_usage():
    st = FakeStore()
    r = handle_text("/mode vacances", ME, ME, st.as_store())
    assert "Usage" in r.text and st.mode == "reunion"


def test_callback_recorded_for_owner_only():
    st = FakeStore()
    assert handle_callback("test:vu", ME, ME, st.as_store()).text == "Enregistré : Vu"
    assert not handle_callback("test:vu", 42, ME, st.as_store()).handled
    assert [e[1] for e in st.events] == [True, False]


def test_keyboard_and_message_length():
    ev = Event(
        "P2", "proposal", "BUY X", [f"ligne {i}" for i in range(20)], [Button("Vu", "vu"), Button("Ignorer", "ignorer")]
    )
    assert len(ev.telegram_text().splitlines()) <= 12
    kb = build_keyboard(ev.buttons, prefix="proposal:")
    assert kb.inline_keyboard[0][0].callback_data == "proposal:vu"
