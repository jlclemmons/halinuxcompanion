from halinuxcompanion.api import API
from halinuxcompanion.companion import Companion

from dbus_next.aio import MessageBus, ProxyInterface
from dbus_next import BusType
from aiohttp import ClientError
from typing import NamedTuple, Optional
import asyncio
import json
import logging
import time

logger = logging.getLogger(__name__)

GEOCLUE_BUS = "org.freedesktop.GeoClue2"
GEOCLUE_MANAGER_PATH = "/org/freedesktop/GeoClue2/Manager"
GEOCLUE_MANAGER_IFACE = "org.freedesktop.GeoClue2.Manager"
GEOCLUE_CLIENT_IFACE = "org.freedesktop.GeoClue2.Client"
GEOCLUE_LOCATION_IFACE = "org.freedesktop.GeoClue2.Location"

# GClueAccuracyLevel. EXACT is the only level that engages a GNSS source; the
# lower levels rely on wifi/cell lookups which are unavailable in some regions,
# in which case GeoClue reports no location at all rather than a coarse one.
ACCURACY_EXACT = 8

# GeoClue reports this path when the client has no fix at all.
NO_LOCATION = "/"


class Reading(NamedTuple):
    """A fix as read from GeoClue, with the age needed to judge it."""

    payload: dict
    accuracy: float
    # Epoch seconds the source took the fix, 0.0 when GeoClue reports none.
    fix_time: float


class Location:
    """Reports device position to Home Assistant as a device_tracker.

    Home Assistant creates the device_tracker entity from the first
    update_location webhook, so no registration step is needed.
    """

    api: API
    bus: MessageBus
    client: ProxyInterface
    enabled: bool
    desktop_id: str
    distance_threshold: int
    time_threshold: int
    heartbeat: int
    max_accuracy: int
    max_age: int
    last_sent: float

    def __init__(self, api: API, companion: Companion) -> None:
        self.api = api
        self.enabled = companion.location_enabled
        self.desktop_id = companion.location_desktop_id
        self.distance_threshold = companion.location_distance_threshold
        self.time_threshold = companion.location_time_threshold
        self.heartbeat = companion.location_heartbeat
        self.max_accuracy = companion.location_max_accuracy
        self.max_age = companion.location_max_age
        self.last_sent = 0.0

    async def init(self) -> bool:
        """Create a GeoClue client and start receiving position updates."""
        if not self.enabled:
            return False

        try:
            self.bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
            introspection = await self.bus.introspect(GEOCLUE_BUS, GEOCLUE_MANAGER_PATH)
            manager = self.bus.get_proxy_object(GEOCLUE_BUS, GEOCLUE_MANAGER_PATH, introspection)
            manager_iface = manager.get_interface(GEOCLUE_MANAGER_IFACE)
            client_path = await manager_iface.call_get_client()

            introspection = await self.bus.introspect(GEOCLUE_BUS, client_path)
            client = self.bus.get_proxy_object(GEOCLUE_BUS, client_path, introspection)
            self.client = client.get_interface(GEOCLUE_CLIENT_IFACE)

            # The agent authorizes by desktop id, so it has to match an installed
            # .desktop file, otherwise the client starts but never receives a fix.
            await self.client.set_desktop_id(self.desktop_id)
            await self.client.set_requested_accuracy_level(ACCURACY_EXACT)
            await self.client.set_distance_threshold(self.distance_threshold)
            if self.time_threshold > 0:
                await self.client.set_time_threshold(self.time_threshold)

            self.client.on_location_updated(self.on_location_updated)
            await self.client.call_start()
        except Exception as e:
            logger.error("Location reporting unavailable, is geoclue running? %s", e)
            return False

        logger.info(
            "Location reporting started, desktop_id:%s distance_threshold:%sm "
            "time_threshold:%ss heartbeat:%ss max_accuracy:%sm max_age:%ss",
            self.desktop_id, self.distance_threshold, self.time_threshold,
            self.heartbeat, self.max_accuracy, self.max_age)
        return True

    def on_location_updated(self, old_path: str, new_path: str) -> None:
        """GeoClue signal handler, schedules the update since it cannot await."""
        asyncio.create_task(self.handle_location(new_path))

    async def handle_location(self, path: str) -> None:
        reading = await self.read_location(path)
        if reading is not None and self.usable(reading):
            await self.send(reading.payload)

    async def read_location(self, path: str) -> Optional[Reading]:
        """Read a GeoClue location object, or None if it cannot be read."""
        try:
            introspection = await self.bus.introspect(GEOCLUE_BUS, path)
            obj = self.bus.get_proxy_object(GEOCLUE_BUS, path, introspection)
            location = obj.get_interface(GEOCLUE_LOCATION_IFACE)

            accuracy = await location.get_accuracy()
            payload = {
                "gps": [await location.get_latitude(), await location.get_longitude()],
                "gps_accuracy": round(accuracy),
            }

            # GeoClue reports these as 0.0 when the source cannot determine them,
            # and Home Assistant would show that as a real value.
            altitude = await location.get_altitude()
            if altitude:
                payload["altitude"] = round(altitude)
            speed = await location.get_speed()
            if speed and speed > 0:
                payload["speed"] = round(speed)
            heading = await location.get_heading()
            if heading and heading > 0:
                payload["course"] = round(heading)

            # Timestamp is (seconds, microseconds) since the epoch, describing
            # when the source took the fix rather than when it was read here.
            # Sources that do not supply one report zero.
            timestamp = await location.get_timestamp()
            fix_time = float(timestamp[0]) if timestamp else 0.0
        except Exception as e:
            logger.error("Failed to read location from geoclue: %s", e)
            return None

        return Reading(payload, accuracy, fix_time)

    def usable(self, reading: Reading) -> bool:
        """Whether a fix is worth asserting as the device's position.

        Home Assistant plots whatever point it is given, so a fix that is
        merely the best GeoClue could manage still has to be filtered here or
        it becomes the device's reported position.
        """
        # Zero means the source did not state an accuracy, which is not the
        # same as a perfect fix, and there is nothing to judge it by.
        if self.max_accuracy > 0 and reading.accuracy > self.max_accuracy:
            logger.info(
                "Ignoring location, accuracy %sm exceeds max_accuracy %sm",
                round(reading.accuracy), self.max_accuracy)
            return False

        age = time.time() - reading.fix_time if reading.fix_time else 0.0
        if self.max_age > 0 and age > self.max_age:
            logger.info(
                "Ignoring location, fix is %ss old, over max_age %ss",
                round(age), self.max_age)
            return False

        return True

    async def send(self, payload: dict) -> None:
        data = json.dumps({"type": "update_location", "data": payload})
        try:
            await self.api.webhook_post("update_location", data=data)
            self.last_sent = time.monotonic()
            logger.info("Location update sent, accuracy:%sm", payload["gps_accuracy"])
        except ClientError as e:
            logger.error("Failed to send location update: %s", e)

    async def heartbeat_task(self) -> None:
        """Report the current position when nothing has moved.

        DistanceThreshold means a stationary device stops emitting updates
        entirely, which is exactly when a lost or stolen device still needs to
        report where it is.

        This re-reads GeoClue rather than resending the last payload. The
        threshold suppresses updates until the device has moved that far, so
        the last emitted fix can be a threshold's worth of distance behind
        where it actually came to rest, and replaying it would keep asserting
        that stale point — the webhook carries no timestamp, so Home Assistant
        stamps every arrival as current no matter how old the fix is. Reading
        the client's current location converges on the resting position
        instead, and costs nothing extra: GeoClue serves the last fix its
        source produced, it does not power up a receiver to answer.
        """
        if not self.enabled or self.heartbeat <= 0:
            return

        while True:
            await asyncio.sleep(self.heartbeat)
            if time.monotonic() - self.last_sent < self.heartbeat:
                continue

            path = await self.current_location_path()
            if path is None:
                continue
            reading = await self.read_location(path)
            if reading is not None and self.usable(reading):
                await self.send(reading.payload)

    async def current_location_path(self) -> Optional[str]:
        """The client's current fix, or None when it has none to give."""
        try:
            path = await self.client.get_location()
        except Exception as e:
            logger.error("Failed to read current location from geoclue: %s", e)
            return None

        if not path or path == NO_LOCATION:
            logger.info("No location available from geoclue yet")
            return None
        return path
