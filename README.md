# Home Assistant Linux Companion

Application to run on Linux desktop computer to provide sensor data to Home Assistant, and get notifications as if it was a mobile device.

## How To

### Requirements

Python 3.10+ and the related `dev` dependencies (usually `python3-dev` or `python3-devel` on your package manager)

### Instructions

1. [Get a long-lived access token from your Home Assistant user](https://www.home-assistant.io/docs/authentication/#your-account-profile)
1. Clone this repository in a subfolder from your home directory (unless you don't want to run the service from `systemd`)
1. Create a Python virtual environment and install all the requirements:

   ```shell
   cd halinuxcompanion  # this is the root of the cloned project
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

1. Copy `config.example.json` to `config.json`.
1. Modify `config.json` to match your setup and desired options.
1. Run the application, either from:
   1. the virtual environment directly: `python -m halinuxcompanion --config config.json`. In this case, you'll need to run it again when you restart.
   1. or setting up a systemd service (don't use `sudo` for any of the commands below; if you need it, something is probably wrong with your setup):
      1. Copy the sample unit file from `halinuxcompanion/resources/halinuxcompanion.service` to `~/.config/systemd/user/`
      1. Modify it to match your setup - mainly, the installation paths at `WorkingDirectory` and `ExecStart`
      1. (Re)Load it with `systemctl --user daemon-reload`
      1. Start it with `systemctl --user start halinuxcompanion`
      1. You can check if it went well with `systemctl --user status halinuxcompanion`. If it errored, you can check logs with `journalctl --user -u halinuxcompanion`
      1. If all went well, you can enable it permanently with `systemctl --user enable halinuxcompanion`

Now in your Home Assistant you will see a new device in the **"mobile_app"** integration, and there will be a new service to notify your Linux desktop. Notification actions work and the expected events will be fired in Home Assistant.

## [Example configuration file](config.example.json)

```json
{
  "ha_url": "http://homeassistant.local:8123/",
  "ha_token": "mysuperlongtoken",
  "device_id": "computername",
  "device_name": "whatever you want can be left empty",
  "manufacturer": "whatever you want can be left empty",
  "model": "Computer",
  "computer_ip": "192.168.1.15",
  "computer_port": 8400,
  "refresh_interval": 15,
  "loglevel": "INFO",
  "location": {
    "enabled": false,
    "desktop_id": "halinuxcompanion",
    "distance_threshold": 30,
    "time_threshold": 0,
    "heartbeat": 900,
    "max_accuracy": 500,
    "max_age": 3600
  },
  "sensors": {
    "cpu": {
      "enabled": true,
      "name": "CPU"
    },
    "memory": {
      "enabled": true,
      "name": "Memory Load"
    },
    "uptime": {
      "enabled": true,
      "name": "Uptime"
    },
    "status": {
      "enabled": true,
      "name": "Status"
    },
    "battery_level": {
      "enabled": true,
      "name": "Battery Level"
    },
    "battery_state": {
      "enabled": true,
      "name": "Battery State"
    },
    "camera_state": {
      "enabled": true,
      "name": "Camera State"
    }
  },
  "services": {
    "notifications": {
      "enabled": true,
      "url_program": "xdg-open",
      "commands": {
        "command_suspend": {
          "name": "Suspend",
          "command": ["systemctl", "suspend"]
        },
        "command_poweroff": {
          "name": "Power off",
          "command": ["systemctl", "poweroff"]
        },
        "command_reboot": {
          "name": "Reboot",
          "command": ["systemctl", "reboot"]
        },
        "command_hibernate": {
          "name": "Hibernate",
          "command": ["systemctl", "hibernate"]
        },
        "command_open_ha": {
          "name": "Open Home Assistant",
          "command": ["xdg-open", "http://homeassistant.local:8123/"]
        },
        "command_open_spotify": {
          "name": "Open Spotify Flatpak",
          "command": ["flatpak", "run", "com.spotify.Client"]
        }
      }
    }
  }
}
```

## Location

Optional, disabled unless the `location` section sets `"enabled": true`. When on,
the application asks [GeoClue](https://gitlab.freedesktop.org/geoclue/geoclue) for
the machine's position and posts it to Home Assistant, which creates a
`device_tracker` entity for the device. No registration step is needed: Home
Assistant creates the tracker from the first update.

GeoClue must be running on the system bus, and `desktop_id` has to match an
installed `.desktop` file — GeoClue's agent authorizes clients by that id, and an
unknown one leaves the client started but never receiving a fix.

**How well this works depends entirely on what the machine can position with.** A
device with a GNSS receiver (a Linux phone, or a laptop with a WWAN module that
exposes GPS) gets fixes accurate to a few metres. A typical desktop or laptop has
no GNSS at all, so GeoClue falls back to a wifi-based lookup, which is usually
accurate to tens or hundreds of metres and depends on the surrounding access
points being present in the database it queries. On a machine that never moves,
that may still be all you need for a home/away zone; it is not a substitute for a
phone's tracker, and it can be wrong by a street.

| Option | Default | Meaning |
| --- | --- | --- |
| `enabled` | `false` | Whether to report position at all. |
| `desktop_id` | – | Must match an installed `.desktop` file, see above. |
| `distance_threshold` | `30` | Metres of movement before GeoClue emits an update. Larger values save power, and cost accuracy: the last reported position can be this far behind where the device actually stopped. |
| `time_threshold` | `0` | Seconds between GeoClue updates. `0` leaves GeoClue's own default, i.e. distance drives updates. |
| `heartbeat` | `900` | Seconds after which the current position is reported even if the device has not moved. `0` disables it. |
| `max_accuracy` | `500` | Metres. Fixes reported as less accurate than this are ignored rather than sent. Tighten it on a device with GNSS; raise it if a wifi-positioned machine reports nothing. |
| `max_age` | `3600` | Seconds. A fix older than this is not reported, so Home Assistant shows a stale tracker rather than a confident wrong position. `0` disables the check. |

The `max_accuracy` and `max_age` checks exist because the `update_location`
webhook carries no timestamp — Home Assistant stamps whatever arrives as current.
Without them, a coarse or stale fix is indistinguishable from a good one, and the
device sits confidently in the wrong place. For the same reason the heartbeat
re-reads GeoClue instead of resending the last payload, and sends nothing at all
when there is no usable fix.

## Technical

- [Home Assistant Native App Integration](https://developers.home-assistant.io/docs/api/native-app-integration)
- [Home Assistant REST API](https://developers.home-assistant.io/docs/api/rest)
- Asynchronous (because why not :smile:)
  - HTTP Server ([aiohttp](https://docs.aiohttp.org/en/stable/)): Listen to POST notification service call from Home Assistant
  - Client ([aiohttp](https://docs.aiohttp.org/en/stable/)): POST to Home Assistant api, sensors, events, etc
  - [Dbus](https://www.freedesktop.org/wiki/Software/dbus/) interface ([dbus_next](https://python-dbus-next.readthedocs.io/en/latest/index.html)): Sending notifications and listening to notification actions from the desktop, also listens to sleep, shutdown to update the status sensor

## To-do

- [ ] [Implement encryption](https://developers.home-assistant.io/docs/api/native-app-integration/sending-data)
- [ ] Move sensors to MQTT  
    The reasoning for the change is the limitations of the API, naturally is expected that desktop and laptops would go offline and I would like for the sensors to reflect this new state. But if for some reason the application is unable to send this new state to Home Assistant the values of the sensors would be stuck. But if the app uses MQTT it can set will topics for the sensors to be updated when the client can't communicate with the server.
- [ ] One day make it work with remote and local instance, for laptops roaming networks
- [x] Status sensors that listens to sleep, wakeup, shutdown, power_on
- [ ] Add more sensors
- [ ] Finish notifications functionality
    - [x] Add notification commands
    - [x] [Notifications Clearing](https://companion.home-assistant.io/docs/notifications/notifications-basic/#clearing)
    - [ ] [Notification Icon](https://companion.home-assistant.io/docs/notifications/notifications-basic/#notification-icon)

## Features

- Sensors:
  - CPU
  - Memory
  - Uptime
  - Status: Computer status, reflects if the computer went to sleep, wakes up, shutdown, turned on. The sensor is updated right before any of these events happen by listening to dbus signals.
  - Battery Level
  - Batter State
- Location: reports position through GeoClue as a `device_tracker`, for presence
  and zone automations. Optional and off by default, see [Location](#location).
- Notifications:
  - [Actionable Notifications](https://companion.home-assistant.io/docs/notifications/actionable-notifications#building-actionable-notifications) (Triggers event in Home Assistant)
      - [Local action handler using URI](https://companion.home-assistant.io/docs/notifications/actionable-notifications#uri-values): only relative style `/lovelace/myviwew` and `http(s)` uri supported so far.
  - [Notification cleared/dismissed](https://companion.home-assistant.io/docs/notifications/notification-cleared/) (Triggers event in Home Assistant)
  - [Timeout](https://companion.home-assistant.io/docs/notifications/notifications-basic#notification-timeout)
  - [Commands](https://companion.home-assistant.io/docs/notifications/notification-commands/)
  - [Replacing](https://companion.home-assistant.io/docs/notifications/notifications-basic/#replacing)
  - [Clearing](https://companion.home-assistant.io/docs/notifications/notifications-basic/#clearing)
  - [Icon](https://companion.home-assistant.io/docs/notifications/notifications-basic/#notification-icon) **TODO**
- Default commands (example config):
  - Suspend
  - Power off
  - Reboot
  - Hibernate
