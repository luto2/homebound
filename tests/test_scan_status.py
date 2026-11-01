"""Rescan status: fresh, due and never, the threshold, clock skew and the age text."""
from datetime import datetime, timedelta, timezone

import pytest

from homebound.mapping.scan_status import ScanStatus, age_text, scan_status
from homebound.settings import Settings

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
DAY = timedelta(hours=24)


def ago(**delta) -> str:
    """Return the ISO timestamp this long before NOW, as signature_parser.merge stores it."""
    return (NOW - timedelta(**delta)).isoformat(timespec="seconds")


@pytest.mark.parametrize("wormhole", [True, False])
def test_fresh_and_due(wormhole) -> None:
    """A paste younger than the threshold is fresh, an older one is due, in w-space and k-space alike."""
    assert scan_status(ago(hours=3), NOW, DAY, wormhole=wormhole) == ScanStatus("fresh", "3h", timedelta(hours=3))
    assert scan_status(ago(hours=26), NOW, DAY, wormhole=wormhole) == ScanStatus("due", "1d 2h", timedelta(hours=26))


def test_exactly_on_the_threshold_is_due() -> None:
    """An age of exactly ``due_after`` is due; a second less is still fresh."""
    assert scan_status(ago(hours=24), NOW, DAY, wormhole=True).state == "due"
    assert scan_status(ago(hours=23, minutes=59, seconds=59), NOW, DAY, wormhole=True).state == "fresh"


def test_never_only_in_wormhole_space() -> None:
    """No paste yet is "never" in w-space; a never-pasted k-space system counts as fresh. Neither has an age."""
    assert scan_status(None, NOW, DAY, wormhole=True) == ScanStatus("never", "")
    assert scan_status(None, NOW, DAY, wormhole=False) == ScanStatus("fresh", "")


def test_future_timestamp_is_fresh() -> None:
    """A paste in the future (clock skew) is fresh and reads as "0m"."""
    future = (NOW + timedelta(hours=30)).isoformat()
    assert scan_status(future, NOW, DAY, wormhole=True) == ScanStatus("fresh", "0m", timedelta(0))


def test_threshold_from_settings() -> None:
    """The threshold comes from ``Settings.rescan_due_hours`` as whole hours."""
    settings = Settings(rescan_due_hours=12)
    due_after = timedelta(hours=settings.rescan_due_hours)
    assert scan_status(ago(hours=13), NOW, due_after, wormhole=True).state == "due"
    assert scan_status(ago(hours=11), NOW, due_after, wormhole=True).state == "fresh"


def test_other_timezones_compare_correctly() -> None:
    """A timestamp with another UTC offset is compared by the instant, not the wall clock."""
    local = (NOW - timedelta(hours=2)).astimezone(timezone(timedelta(hours=5))).isoformat()
    assert scan_status(local, NOW, DAY, wormhole=True) == ScanStatus("fresh", "2h", timedelta(hours=2))


def test_validation() -> None:
    """Wrong types raise TypeError; a threshold of zero or less raises ValueError."""
    with pytest.raises(TypeError):
        scan_status(5, NOW, DAY, wormhole=True)
    with pytest.raises(TypeError):
        scan_status(None, "now", DAY, wormhole=True)
    with pytest.raises(TypeError):
        scan_status(None, NOW, 24, wormhole=True)
    with pytest.raises(TypeError):
        scan_status(None, NOW, DAY, wormhole=1)
    with pytest.raises(ValueError):
        scan_status(None, NOW, timedelta(0), wormhole=True)


@pytest.mark.parametrize("age,expected", [
    (None, ""), (timedelta(0), "0m"), (timedelta(minutes=45), "45m"), (timedelta(hours=1), "1h"),
    (timedelta(hours=26, minutes=59), "26h"), (timedelta(hours=99), "99h"), (timedelta(hours=100), "4d"),
    (timedelta(days=9), "9d")])
def test_short_age(age, expected) -> None:
    """The tab age is the largest unit only: minutes, then whole hours, then days from 100 hours on."""
    assert ScanStatus("due", "", age).short_age == expected


def test_status_is_frozen() -> None:
    """A ScanStatus can't be changed after it is made."""
    with pytest.raises(AttributeError):
        ScanStatus("fresh").state = "due"


@pytest.mark.parametrize("delta,expected", [
    (dict(seconds=5), "0m"), (dict(minutes=5, seconds=5), "5m"), (dict(hours=2), "2h"),
    (dict(hours=2, minutes=10), "2h 10m"), (dict(days=3), "3d"), (dict(days=3, hours=4), "3d 4h"),
    (dict(days=3, hours=4, minutes=30), "3d 4h")])
def test_age_text_formatting(delta, expected) -> None:
    """Ages show the two largest units (d/h/m), dropping zero parts; under a minute is "0m"."""
    assert age_text(ago(**delta), NOW) == expected


def test_age_text_edge_cases() -> None:
    """No timestamp gives "", a future one "0m"; without ``now`` the system clock is used; bad types raise."""
    assert age_text(None, NOW) == "" and age_text("", NOW) == ""
    assert age_text((NOW + timedelta(minutes=5)).isoformat(), NOW) == "0m"
    assert age_text((datetime.now(timezone.utc) - timedelta(hours=2, seconds=5)).isoformat()) == "2h"
    with pytest.raises(TypeError):
        age_text(5)
    with pytest.raises(TypeError):
        age_text(ago(hours=1), "now")
