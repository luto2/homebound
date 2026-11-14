"""Which of two characters with the same name is on Tranquility, asked of ESI (EVE's public web API).

The test servers (Singularity, Thunderdome) are separate clusters: a character copied there gets another
ID, but its client writes Local logs to the same folder, with the same file name pattern and the same
``Listener:`` name. Homebound can't tell the copy from the log, so when two IDs share a name it asks
GET https://esi.evetech.net/latest/characters/<id>/ for each. ESI only knows Tranquility, the live
server: the real character answers with its name, a test server copy with 404 (or with the name of an
unrelated Tranquility character that happens to have that ID). Nothing is asked while no name is shared.
"""
import queue
import threading
import time
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta, timezone

import httpx

from .http import BackgroundFetcher, make_client
from .validation import require_integer, require_text, require_type

CHARACTER_URL = "https://esi.evetech.net/latest/characters/{}/?datasource=tranquility"
REQUEST_INTERVAL = 0.5             # seconds between requests
RETRY_AFTER = timedelta(minutes=10)  # a group that couldn't be decided is asked again after this long
_STOP = None                       # queue item that ends the worker


class Unanswered(Exception):
    """ESI gave no usable answer (network error, server error, a reply that isn't a character)."""


def same_name(first: str, second: str) -> bool:
    """Return whether two character names are the same; EVE names are unique regardless of case."""
    require_type(first, str, "first")
    require_type(second, str, "second")
    return " ".join(first.split()).casefold() == " ".join(second.split()).casefold()


def tranquility_name(client: httpx.Client, character_id: int) -> str | None:
    """Return the Tranquility name of a character ID, or None if Tranquility has no live character with it.

    Args:
        client: The HTTP client.
        character_id: The EVE character ID.

    Raises:
        Unanswered: If ESI couldn't be asked or gave an answer that is neither a character nor a 404.
    """
    require_integer(character_id, "character_id", minimum=1)
    try:
        response = client.get(CHARACTER_URL.format(character_id))
    except httpx.HTTPError as error:
        raise Unanswered(type(error).__name__) from error
    if response.status_code == 404:   # never existed there, or deleted
        return None
    if response.status_code != 200:
        raise Unanswered(f"HTTP {response.status_code}")
    try:
        name = response.json().get("name")
    except (ValueError, AttributeError) as error:
        raise Unanswered("not a character") from error
    if not isinstance(name, str) or not name.strip():
        raise Unanswered("not a character")
    return name


def split_by_server(name: str, answers: dict[int, str | None]) -> tuple[int, list[int]] | None:
    """Decide which ID is the Tranquility character, from ESI's answers.

    Args:
        name: The name the IDs share in the logs.
        answers: Each ID's Tranquility name, or None where Tranquility has no character with it.

    Returns:
        ``(tranquility_id, other_ids)`` if exactly one ID is ``name`` on Tranquility, else None (nothing is
        decided when none of them is, or when more than one is).
    """
    require_text(name, "name")
    require_type(answers, dict, "answers")
    real = [character_id for character_id, answer in answers.items() if answer is not None and same_name(answer, name)]
    if len(real) != 1:
        return None
    return real[0], sorted(character_id for character_id in answers if character_id != real[0])


class DuplicateChecker(BackgroundFetcher):
    """Asks ESI, on a background thread, which of the IDs that share a name is on Tranquility.

    Call :meth:`request` with a group of IDs. ``on_update(name, tranquility_id, other_ids)`` is called from the
    worker thread when exactly one is, so marshal it to Qt with a signal.
    """

    def __init__(self, on_update: Callable[[str, int, list[int]], object],
                 on_status: Callable[[str, str], object] | None = None) -> None:
        """Create the checker; call :meth:`start` to run it.

        Args:
            on_update: Called with ``(name, tranquility_id, other_ids)`` once a group is decided.
            on_status: Called with ``(level, text)`` when a group couldn't be decided.
        """
        super().__init__("esi", on_update, on_status)
        self.queue: queue.Queue[tuple[str, frozenset[int]] | None] = queue.Queue()
        self.asked: dict[frozenset[int], datetime] = {}   # group -> when it was last queued
        self._asked_lock = threading.Lock()

    def stop(self) -> None:
        """Ask the worker thread to finish after its current group."""
        self._stopping = True
        self.queue.put(_STOP)

    def request(self, name: str, character_ids: Iterable[int], now: datetime | None = None) -> bool:
        """Queue a group of IDs that share a name, unless the same group was queued in the last RETRY_AFTER.

        Args:
            name: The name they share.
            character_ids: Two or more character IDs.
            now: The current time (UTC), or None for the clock.

        Returns:
            True if the group was queued.
        """
        require_text(name, "name")
        require_type(now, (datetime, type(None)), "now")
        group = frozenset(character_ids)
        for character_id in group:
            require_integer(character_id, "character_ids item", minimum=1)
        if len(group) < 2:
            raise ValueError("character_ids needs at least two IDs")
        now = now or datetime.now(timezone.utc)
        with self._asked_lock:
            last = self.asked.get(group)
            if last is not None and now - last < RETRY_AFTER:
                return False
            self.asked[group] = now
        self.queue.put((name, group))
        return True

    def _run(self) -> None:
        """Worker loop: one group at a time, one request per ID, at most one request per REQUEST_INTERVAL."""
        client = None
        try:
            client = make_client()
            while not self._stopping:
                item = self.queue.get()
                if item is _STOP:
                    break
                name, group = item
                try:
                    self.check(client, name, group)
                except Exception as error:  # keep the worker alive whatever one group does
                    self._status("err", f"ESI: {type(error).__name__}")
        finally:
            if client:
                client.close()

    def check(self, client: httpx.Client, name: str, group: Iterable[int]) -> tuple[int, list[int]] | None:
        """Ask ESI about each ID of a group and report the outcome.

        Args:
            client: The HTTP client.
            name: The name the IDs share.
            group: The IDs.

        Returns:
            ``(tranquility_id, other_ids)``, or None if the group wasn't decided (``on_status`` says why).
        """
        answers: dict[int, str | None] = {}
        for character_id in sorted(group):
            try:
                answers[character_id] = tranquility_name(client, character_id)
            except Unanswered as error:
                self._status("warn", f"ESI: {error}; “{name}” is checked again later")
                return None
            finally:
                time.sleep(REQUEST_INTERVAL)
        decided = split_by_server(name, answers)
        if decided is None:
            self._status("warn", f"ESI: none or several of the characters called “{name}” are on Tranquility")
            return None
        self.on_update(name, *decided)
        return decided
