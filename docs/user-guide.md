# MacArthurVenusSetup – User Guide

> **Platform:** Raspberry Pi 4 / Pi 5 running VenusOS ≥ v2.90
> **Requires:** [SetupHelper](https://github.com/kwindrem/SetupHelper) installed in `/data/SetupHelper/`

---

## What this package does

The MacArthur navigation HAT provides CAN bus (NMEA2000), NMEA 0183, AIS,
compass, and other marine interfaces for a Raspberry Pi running Victron
VenusOS.  Out of the box VenusOS does not know about the HAT's hardware.
This package makes the HAT work:

| Capability | How |
|---|---|
| NMEA2000 CAN bus | Loads an MCP2518FD driver overlay; udev brings up `can0` at 250 kbit/s |
| GPIO pin directions | Overlay sets GPIO21 (shutdown input) and GPIO26 (power-hold output) |
| Graceful shutdown | Daemon holds the DC-DC converter on and cuts power only after a clean shutdown |
| NMEA 2000 → VenusOS dbus bridge | Optional `MacArthurN2K` service reads raw CAN frames in listen-only mode and publishes tank levels (and future PGNs) to the VenusOS dbus — no TX required |
| Survives VenusOS updates | SetupHelper reinstalls the package automatically after a firmware update |

---

## Prerequisites

### 1 – SetupHelper

SetupHelper must be installed and running.  Follow the instructions at
https://github.com/kwindrem/SetupHelper.  Once installed, its files live in
`/data/SetupHelper/` and the PackageManager service runs in the background.

### 2 – Hardware

Supported platforms:

| Platform | Notes |
|----------|-------|
| Raspberry Pi 4 | Fully tested |
| Raspberry Pi 5 | SPI0 and GPIO assignments are identical; UART numbering changes (UART2/4 → UART3/5) but this package does not use UARTs — no changes needed |

---

## Installation

### Option A – Via PackageManager (recommended)

1. Copy the package directory to the Venus device:

   ```bash
   scp -r MacArthurVenusSetup root@<venus-ip>:/data/
   ```

2. SSH in and run setup interactively:

   ```bash
   ssh root@<venus-ip>
   /data/MacArthurVenusSetup/setup
   ```

3. Choose **Install** at the prompt.

4. **Reboot** the device:

   ```bash
   reboot
   ```

5. After rebooting, verify the CAN interface is up:

   ```bash
   ip link show can0
   # should show: can0: <NOARP,UP,LOWER_UP,ECHO> mtu 16 … state UNKNOWN
   ```

### Option B – Via removable media (offline / field install)

1. Create a zip archive of the package directory.
2. Copy the zip to a USB stick or SD card (FAT32 or ext4).
3. With SetupHelper installed, plug the media into the Venus device.
4. PackageManager detects the archive automatically when `AUTO_INSTALL_PACKAGES` is set, or you can trigger installation from the PackageManager GUI.

### Option C – PackageManager GUI

If your GitHub fork is configured in `gitHubInfo`, PackageManager can
download and install the package directly from the GUI under
**Settings → PackageManager**.

---

## Uninstallation

```bash
/data/MacArthurVenusSetup/setup
# choose Uninstall at the prompt
reboot
```

This removes the dtoverlay entries from `/u-boot/config.txt`, deletes the
compiled `.dtbo` files from `/u-boot/overlays/`, removes the udev rule, and
stops and removes the `MacArthurShutdown` service.

---

## NMEA2000 integration in VenusOS

This package makes the MCP2518FD hardware work at the kernel level and brings
up `can0` at 250 kbit/s.  There are two ways to get NMEA 2000 device data
into the VenusOS GUI.

### Option A – vecan-dbus via VeCanSetup (normal path)

Install [VeCanSetup](https://github.com/kwindrem/VeCanSetup) alongside this
package.  When you add a new HAT interface in VeCanSetup, select `can0` and
set the profile to **NMEA2000**.  VeCanSetup will configure the `vecan-dbus`
service to bridge NMEA2000 PGNs to the Victron dBus.

**Use this option if your CAN transceiver can transmit.**

### Option B – MacArthurN2K dbus bridge (TX-impaired workaround)

The MacArthur HAT's CAN transceiver TX path may be faulty.  Symptoms:

- `can0` receives frames fine (`candump can0` shows traffic).
- After any transmission attempt the RX error counter climbs to
  ERROR-PASSIVE (≥ 128) and most frames are subsequently dropped.
- `vecan-dbus` (from VeCanSetup) never shows NMEA 2000 devices — it must
  complete address claiming (which requires TX) before registering devices.

**Root cause:** `vecan-dbus` performs NMEA 2000 address claiming on startup.
The address claim is sent and acknowledged, but the bus degrades immediately
afterwards — almost certainly a CAN transceiver TX path issue on the HAT
(possible causes: missing termination resistor, STBY pin held in the wrong
state, or a faulty transceiver IC).

The optional `MacArthurN2K` service is a workaround that reads raw frames in
**listen-only** mode (zero bus participation) and publishes device data
directly to the VenusOS dbus, bypassing address claiming entirely.

To enable it, run setup and answer **Yes** to the prompt:

```bash
/data/MacArthurVenusSetup/setup
# → "Enable NMEA 2000 dbus bridge (workaround for CAN TX issues)?"
```

Currently published to dbus:

| NMEA 2000 PGN | Data | VenusOS service name |
|---|---|---|
| 127505 Fluid Level | Fuel type, level %, capacity | `com.victronenergy.tank.N2K_can0_<sa>_<inst>` |

Tank sensors appear in the VenusOS device list immediately after the service
starts (no reboot required).  Service logs are at
`/service/MacArthurN2K/log/main/current`.

If the TX path is repaired later, run setup again, answer **No** to the
bridge prompt, and configure VeCanSetup instead.

---

## Shutdown behaviour

The MacArthur HAT can request a graceful RPi shutdown when it detects loss
of 12 V supply (or via a dedicated button).  The sequence is:

```
HAT detects low voltage
        │
        ▼
GPIO21 driven LOW by HAT
        │
        ▼
MacArthurShutdown service detects falling edge
        │
        ▼
`shutdown -h now` issued – VenusOS begins shutdown
        │
        ▼
All services terminate; filesystems unmounted
        │
        ▼
s6 sends SIGTERM to MacArthurShutdown
        │
        ▼
GPIO26 driven LOW – HAT may now cut 12 V power
```

> **Safety fallback:** if the init system has not sent SIGTERM within 90 s
> after `shutdown` was called, the daemon drives GPIO26 LOW anyway to prevent
> the RPi being powered indefinitely in a broken state.

### Power-on / boot

At boot the HAT's DC-DC converter powers the RPi before any software
runs.  The device-tree overlay sets GPIO26 as an output pin.  As soon as
the `MacArthurShutdown` service starts (early in the s6 boot sequence) it
drives GPIO26 HIGH.  The DC-DC converter remains on throughout normal
operation.

---

## Verifying the installation

### Check overlays are loaded

```bash
vcgencmd get_config str | grep dtoverlay
# should include: dtoverlay=macarthur-can
#                 dtoverlay=macarthur-gpio
```

Or inspect the active device-tree:

```bash
ls /proc/device-tree/soc/spi@7e204000/
# should contain: mcp2515@0
```

### Check the CAN interface

```bash
ip -details link show can0
# bitrate 250000, state UNKNOWN (normal when no traffic) or UP
```

Send a test frame (requires `can-utils`):

```bash
cansend can0 123#DEADBEEF
candump can0
```

### Check the shutdown monitor service

```bash
svstat /service/MacArthurShutdown
# up (pid XXXX) XX seconds
```

View recent log entries:

```bash
cat /service/MacArthurShutdown/log/main/current
```

The first lines should include:

```
GPIO26 (DCDC_EN) → HIGH: DC-DC converter enabled
GPIO21 (SHUTDOWN_REQ) configured – monitoring for shutdown request
Monitoring GPIO21 for shutdown request (active-low)…
```

### Check GPIO states via sysfs

```bash
# GPIO26 should read 1 (DCDC_EN held HIGH)
cat /sys/class/gpio/gpio26/value

# GPIO21 should read 1 (idle / no shutdown request)
cat /sys/class/gpio/gpio21/value
```

---

## Troubleshooting

### `can0` interface does not appear

1. Confirm the overlay compiled successfully:
   ```bash
   ls -la /u-boot/overlays/macarthur-can.dtbo
   ```
2. Check `/u-boot/config.txt` contains the `dtoverlay=macarthur-can` line.
3. Verify SPI is enabled:
   ```bash
   ls /dev/spi*   # should show /dev/spidev0.0 or similar
   ```
   If missing, check that `dtparam=spi=on` is in `config.txt`.
4. Check for MCP2518FD kernel messages:
   ```bash
   dmesg | grep -i mcp
   # look for: mcp251xfd spi0.1: MCP2518FD rev0.x successfully initialized
   ```

### `MacArthurShutdown` service is not up

```bash
svstat /service/MacArthurShutdown
```

If it shows `down` or is respawning rapidly, check the log:

```bash
cat /service/MacArthurShutdown/log/main/current
```

Common causes:
- `python3` not found at `/usr/bin/python3` – check `which python3`
- `/sys/class/gpio/gpio21` or `gpio26` sysfs nodes are claimed by another
  driver – check `dmesg | grep gpio`
- Permissions issue on `/sys/class/gpio/export` – the service runs as root,
  so this is uncommon

### Shutdown does not cut power

If the RPi shuts down but the HAT does not cut 12 V:

1. Confirm GPIO26 goes LOW after shutdown:
   ```bash
   # Before shutdown – should be 1
   cat /sys/class/gpio/gpio26/value
   ```
2. Check the service log for the `GPIO26 (DCDC_EN) → LOW` message.
3. Verify HAT wiring: GPIO26 is on pin 37 of the 40-pin header.

### NMEA 2000 devices not appearing (vecan-dbus TX issue)

If `candump can0` shows frames but VeCanSetup / vecan-dbus never lists any
NMEA 2000 devices, the CAN transceiver TX path is likely faulty.

Confirm by checking the error counters immediately after vecan-dbus starts:

```bash
ip -details -statistics link show can0
# look for: RX errors climbing to 128+ (ERROR-PASSIVE) after vecan-dbus start
```

If the RX error counter climbs after any TX attempt, enable the MacArthurN2K
dbus bridge instead (see [Option B above](#option-b--macarthurn2k-dbus-bridge-tx-impaired-workaround)):

```bash
/data/MacArthurVenusSetup/setup
# Answer "Yes" to the N2K bridge prompt
```

### MacArthurN2K service not starting

```bash
svstat /service/MacArthurN2K
cat /service/MacArthurN2K/log/main/current
```

If it shows `down` with a `down` file present, the bridge is intentionally
disabled.  Run setup and answer **Yes** to enable it.

If it is failing (rapid respawn), common causes:
- `velib_python` not found: check `/opt/victronenergy/dbus-systemcalc-py/ext/velib_python/`
- `can0` interface not up yet: verify `ip link show can0`
- dbus not accessible: check `dbus-send --system --print-reply --dest=org.freedesktop.DBus /org/freedesktop/DBus org.freedesktop.DBus.ListNames`

### Spurious reboots / shutdowns

If the RPi shuts down unexpectedly:

1. Check if GPIO21 is floating (should be HIGH when idle):
   ```bash
   cat /sys/class/gpio/gpio21/value   # expected: 1
   ```
2. Verify the `macarthur-gpio` overlay loaded correctly (pull-up on GPIO21).
3. Check whether another service is toggling GPIO21.

---

## After a VenusOS firmware update

VenusOS uses a dual-partition A/B update scheme.  After applying a firmware
update the system boots to the new partition, which does not have the
package's overlays, udev rules, or service installed.

SetupHelper handles this automatically:

1. At boot, `reinstallMods` detects that the installed package version is
   missing from the new partition.
2. PackageManager reinstalls all packages, including this one.
3. The `setup` script recompiles the overlays, updates `config.txt`, and
   reinstalls the service.
4. A reboot is scheduled to activate the new overlays.

No manual intervention is needed.

---

## GPIO pin reference

| GPIO | Header pin | Signal | Direction | Description |
|------|-----------|--------|-----------|-------------|
| 7  | 26 | SPI0_CE1 | Output (SPI) | MCP2518FD chip-select (Linux CE1, `spi0.1`) |
| 9  | 21 | SPI0_MISO | Input (SPI) | MCP2518FD data out |
| 10 | 19 | SPI0_MOSI | Output (SPI) | MCP2518FD data in |
| 11 | 23 | SPI0_SCLK | Output (SPI) | MCP2518FD clock |
| 21 | 40 | SHUTDOWN_REQ | Input (pull-up) | HAT drives LOW to request shutdown |
| 25 | 22 | CAN_IRQ | Input | MCP2518FD interrupt (active-low, level) |
| 26 | 37 | DCDC_EN | Output | RPi holds HIGH; drive LOW after shutdown |
