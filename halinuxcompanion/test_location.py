from halinuxcompanion.companion import Companion
from halinuxcompanion.location import Location, Reading
import asyncio
import json
import time
import types


def run(coro):
    """Drive a coroutine to completion.

    The suite has no async plugin configured in tox.ini, so the tests stay
    synchronous and drive the event loop themselves.
    """
    return asyncio.new_event_loop().run_until_complete(coro)


def companion(**overrides) -> types.SimpleNamespace:
    """A stand in for Companion carrying only what Location reads."""
    config = {
        "location_enabled": True,
        "location_desktop_id": "halinuxcompanion",
        "location_distance_threshold": 30,
        "location_time_threshold": 0,
        "location_heartbeat": 900,
        "location_max_accuracy": 500,
        "location_max_age": 3600,
    }
    config.update(overrides)
    return types.SimpleNamespace(**config)


def make(**overrides) -> Location:
    return Location(api=None, companion=companion(**overrides))


def reading(accuracy: float = 10.0, age: float = 0.0, lat: float = 41.0, lon: float = -73.0) -> Reading:
    return Reading(
        payload={"gps": [lat, lon], "gps_accuracy": round(accuracy)},
        accuracy=accuracy,
        fix_time=time.time() - age,
    )


def collect(location: Location) -> list:
    """Capture what would be posted to Home Assistant."""
    sent: list = []

    async def send(payload):
        sent.append(payload)
        location.last_sent = time.monotonic()

    location.send = send
    return sent


def base_config() -> dict:
    with open("tests/config.json") as f:
        return json.load(f)


def test_config_without_a_location_section_is_still_valid():
    # Configuration files predate this feature, so its absence has to keep
    # working rather than failing validation at startup.
    config = base_config()
    config.pop("location", None)
    companion = Companion(config)
    assert companion.location_enabled is False


def test_location_section_only_needs_to_be_enabled():
    config = base_config()
    config["location"] = {"enabled": True, "desktop_id": "halinuxcompanion"}
    companion = Companion(config)

    assert companion.location_enabled is True
    assert companion.location_desktop_id == "halinuxcompanion"
    assert companion.location_distance_threshold == 30
    assert companion.location_time_threshold == 0
    assert companion.location_heartbeat == 900
    assert companion.location_max_accuracy == 500
    assert companion.location_max_age == 3600


def test_location_section_overrides_the_defaults():
    config = base_config()
    config["location"] = {
        "enabled": True,
        "desktop_id": "halinuxcompanion",
        "distance_threshold": 100,
        "time_threshold": 60,
        "heartbeat": 300,
        "max_accuracy": 50,
        "max_age": 120,
    }
    companion = Companion(config)

    assert companion.location_distance_threshold == 100
    assert companion.location_time_threshold == 60
    assert companion.location_heartbeat == 300
    assert companion.location_max_accuracy == 50
    assert companion.location_max_age == 120


def test_accuracy_within_the_ceiling_is_reported():
    assert make().usable(reading(accuracy=12.0))
    assert make().usable(reading(accuracy=500.0))


def test_accuracy_beyond_the_ceiling_is_ignored():
    # Cell tower derived fixes land here, and would otherwise be reported as
    # the device's position with no indication they are a guess.
    assert not make().usable(reading(accuracy=1500.0))


def test_accuracy_ceiling_can_be_disabled():
    assert make(location_max_accuracy=0).usable(reading(accuracy=9999.0))


def test_unstated_accuracy_is_not_treated_as_perfect_or_rejected():
    # GeoClue reports 0.0 when the source did not say, which is not a claim of
    # perfect accuracy, but there is nothing to reject it by either.
    assert make().usable(reading(accuracy=0.0))


def test_fresh_fix_is_reported():
    assert make().usable(reading(age=5))


def test_fix_older_than_max_age_is_ignored():
    assert not make().usable(reading(age=7200))


def test_age_check_can_be_disabled():
    assert make(location_max_age=0).usable(reading(age=7200))


def test_fix_without_a_timestamp_is_not_treated_as_stale():
    ageless = Reading(payload={"gps": [1, 2], "gps_accuracy": 10}, accuracy=10.0, fix_time=0.0)
    assert make().usable(ageless)


def test_heartbeat_reports_the_current_position_not_the_last_one_sent():
    location = make(location_heartbeat=0.01)
    sent = collect(location)
    current = reading(lat=41.001, lon=-73.001)
    stale = reading(lat=41.0, lon=-73.0)

    async def current_location_path():
        return "/org/freedesktop/GeoClue2/Location/1"

    async def read_location(path):
        return current

    location.current_location_path = current_location_path
    location.read_location = read_location

    async def beat_once():
        task = asyncio.ensure_future(location.heartbeat_task())
        await asyncio.sleep(0.15)
        task.cancel()

    run(beat_once())

    assert sent, "heartbeat reported nothing"
    assert sent[0]["gps"] == current.payload["gps"]
    assert all(payload["gps"] != stale.payload["gps"] for payload in sent)


def test_heartbeat_reports_nothing_when_there_is_no_fix():
    location = make(location_heartbeat=0.01)
    sent = collect(location)

    async def current_location_path():
        return None

    location.current_location_path = current_location_path

    async def beat_once():
        task = asyncio.ensure_future(location.heartbeat_task())
        await asyncio.sleep(0.1)
        task.cancel()

    run(beat_once())

    # Better a tracker that goes stale than one confidently in the wrong place.
    assert sent == []


def test_heartbeat_does_not_report_an_unusable_fix():
    location = make(location_heartbeat=0.01)
    sent = collect(location)

    async def current_location_path():
        return "/org/freedesktop/GeoClue2/Location/1"

    async def read_location(path):
        return reading(accuracy=3000.0)

    location.current_location_path = current_location_path
    location.read_location = read_location

    async def beat_once():
        task = asyncio.ensure_future(location.heartbeat_task())
        await asyncio.sleep(0.1)
        task.cancel()

    run(beat_once())

    assert sent == []


def test_heartbeat_defers_to_a_recent_update():
    location = make(location_heartbeat=0.05)
    sent = collect(location)

    async def read_location(path):
        raise AssertionError("heartbeat should not have read a location")

    async def current_location_path():
        return "/org/freedesktop/GeoClue2/Location/1"

    location.current_location_path = current_location_path
    location.read_location = read_location
    location.last_sent = time.monotonic()

    async def beat_once():
        task = asyncio.ensure_future(location.heartbeat_task())
        await asyncio.sleep(0.07)
        task.cancel()

    run(beat_once())

    assert sent == []


def test_heartbeat_can_be_disabled():
    location = make(location_heartbeat=0)
    run(asyncio.wait_for(location.heartbeat_task(), timeout=1))


def test_disabled_location_does_not_start_a_heartbeat():
    location = make(location_enabled=False)
    run(asyncio.wait_for(location.heartbeat_task(), timeout=1))
