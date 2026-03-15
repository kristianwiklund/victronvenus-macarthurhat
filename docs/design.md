# MacArthurVenusSetup – Design Documentation

---

## 1. Overview

This document describes the internal design of the `MacArthurVenusSetup`
package: its goals, component boundaries, hardware model, software
architecture, and the reasoning behind key design decisions.

---

## 2. System context

```
┌──────────────────────────────────────────────────────────┐
│  Raspberry Pi running VenusOS                            │
│                                                          │
│  ┌─────────────┐   SocketCAN    ┌──────────────────┐    │
│  │  MCP2518FD  │◄──────────────►│  can0 netdev     │    │
│  │  (on HAT)   │   kernel drv   │  250 kbit/s      │    │
│  └──────┬──────┘                └──────────────────┘    │
│  SPI0   │  GPIO25(IRQ)                  │               │
│         │                       NMEA2000│               │
│  ┌──────┴──────────────────────────────▼──────────┐     │
│  │  macarthur-can.dtbo   (this package)            │     │
│  │  macarthur-gpio.dtbo  (this package)            │     │
│  │  42-macarthur.rules   (this package)            │     │
│  └─────────────────────────────────────────────────┘     │
│                                                          │
│  GPIO26 ──► DCDC_EN ──────────────────────────────┐     │
│  GPIO21 ◄── SHUTDOWN_REQ ─────────────────────┐   │     │
│                                               │   │     │
│  ┌────────────────────────────────────────────┼───┼───┐ │
│  │  MacArthurShutdown service (this package)  │   │   │ │
│  │  shutdown_monitor.py                       │   │   │ │
│  └────────────────────────────────────────────┼───┼───┘ │
│                                               │   │     │
└───────────────────────────────────────────────┼───┼─────┘
                                                │   │
┌───────────────────────────────────────────────┼───┼─────┐
│  MacArthur HAT                                │   │     │
│                                               │   │     │
│  ┌──────────────┐  GPIO21 ───────────────────►│   │     │
│  │  Power logic  │  GPIO26 ◄──────────────────┘   │     │
│  │  (12 V detect)│                                 │     │
│  └──────────────┘                                 │     │
│                                                   │     │
│  ┌──────────────┐                                 │     │
│  │  DC-DC conv. │◄────── DCDC_EN ─────────────────┘     │
│  └──────────────┘                                       │
└─────────────────────────────────────────────────────────┘
```

---

## 3. Hardware model

### 3.1 MCP2518FD CAN-FD controller

The MacArthur HAT connects a Microchip MCP2518FD CAN-FD controller to the
RPi via SPI0.  This was verified from the KiCad schematic in the
[OpenMarine/MacArthur-HAT](https://github.com/OpenMarine/MacArthur-HAT)
repository.

| Signal | GPIO | RPi pin | Notes |
|--------|------|---------|-------|
| MOSI | 10 | 19 | SPI0 data to MCP2518FD |
| MISO | 9  | 21 | SPI0 data from MCP2518FD |
| SCLK | 11 | 23 | SPI0 clock |
| nCS  | 7  | 26 | Chip-select CE1 (active-low). Linux `spi0.1`, `reg = <1>`. Confirmed by OpenPlotter CAN configurator source: "SPI0 CE1" → `spi0-1`. |
| INT  | 25 | 22 | MCP2518FD interrupt (active-low) |

The MCP2518FD is clocked from a **20 MHz** crystal (Y1, 18 pF load) on the
HAT.  SPI clock is 10 MHz (MCP2518FD maximum is 0.85 × 20 MHz = 17 MHz).
The chip is also pin-compatible with the MCP2517FD.

The kernel driver `mcp251xfd` manages the device and exposes it as a
standard Linux SocketCAN network interface (`can0`).

> **CE numbering:** The HAT documentation and OpenPlotter both say "SPI0 CE1".
> The OpenPlotter CAN configurator maps "SPI0 CE1" → `spi0-1` (Linux CE1,
> GPIO7, `reg = <1>`).  This is standard Linux 0-based CE numbering.  CE0
> (GPIO8) is unconnected to the MCP2518FD.

### 3.2 Power management GPIO

| GPIO | Direction | Active level | Signal |
|------|-----------|-------------|--------|
| 26 | Output (RPi drives) | HIGH = DC-DC on | DCDC_EN |
| 21 | Input (HAT drives) | LOW = shutdown request | SHUTDOWN_REQ |

**DCDC_EN (GPIO26)**
The HAT's DC-DC converter is enabled when this pin is HIGH.  The RPi must
assert HIGH before or shortly after boot and hold it HIGH throughout normal
operation.  Only after the operating system has completed a clean shutdown
should the pin be driven LOW, signalling the HAT that it is safe to cut
12 V power.

**SHUTDOWN_REQ (GPIO21)**
The HAT drives this pin LOW to signal that a graceful shutdown is needed
(e.g. 12 V supply loss imminent, watchdog timeout, or user button).  The
pin is configured with an internal RPi pull-up resistor so it idles HIGH
when nothing is driving it.

---

## 4. Component design

### 4.1 Device-tree overlays

#### `macarthur-can.dtbo`

**Source:** `overlays/macarthur-can.dts`
**Compiled to:** `/u-boot/overlays/macarthur-can.dtbo`

Four DT fragments:

| Fragment | Purpose |
|----------|---------|
| `@0` | Enable `spi0` controller |
| `@1` | Disable the stock `spidev1` node on CE1 so the `mcp251xfd` driver can claim it |
| `@2` | Declare a `fixed-clock` node for the 20 MHz MCP2518FD crystal oscillator |
| `@3` | Register the `mcp2518fd` device on `spi0` at CE1 (`reg = <1>`) with the oscillator reference and GPIO25 interrupt |

The interrupt is declared as `LEVEL_LOW` (`8` in the BCM2835 interrupt
type encoding), which is required by the `mcp251xfd` driver.

**Why not the stock `mcp2515-can0` overlay?**
VenusOS is built with Buildroot and ships only the overlays explicitly
included by Victron.  Relying on a stock RPi firmware overlay would make
the package fragile.  Shipping our own pre-compiled `.dtbo` binary is the
same approach taken by VeCanSetup.

#### `macarthur-gpio.dtbo`

**Source:** `overlays/macarthur-gpio.dts`
**Compiled to:** `/u-boot/overlays/macarthur-gpio.dtbo`

One DT fragment targeting the `gpio` controller.  Sets pinmux state for:

- GPIO21: `BCM2835_FSEL_GPIO_IN` (function 0), `BCM2835_PUD_UP` (pull 2)
- GPIO26: `BCM2835_FSEL_GPIO_OUT` (function 1), `BCM2835_PUD_OFF` (pull 0)

**Why no `gpio-hog` for GPIO26?**
A `gpio-hog` node inside the GPIO controller's device-tree node would cause
the kernel to claim GPIO26 at init time and hold it HIGH — which is
desirable for early boot safety.  However, a hogged GPIO is marked as
"in use" by the kernel's GPIO descriptor infrastructure.  Subsequent
`open("/sys/class/gpio/export")` calls for that pin would fail with
`EBUSY`, breaking the shutdown monitor's sysfs-based control.

The chosen trade-off:

- The DTS overlay sets the pin *direction* (output) so the kernel pin-mux
  state is correct from boot.
- The `MacArthurShutdown` service drives the pin HIGH as its very first
  action, before entering the monitoring loop.  On a normal VenusOS boot
  the service is up within a few seconds — acceptable given the HAT's
  typical grace period before enforcing the DCDC_EN signal.
- If earlier assertion is needed in future, a small `oneshot` s6 service
  or an `rcS.local` snippet can be added without altering the overlay.

### 4.2 udev rule

**File:** `udev/42-macarthur.rules`
**Installed to:** `/etc/udev/rules.d/42-macarthur.rules`

```
ACTION=="add", SUBSYSTEM=="net", KERNEL=="can0", \
    RUN+="/bin/ip link set can0 up type can bitrate 250000 restart-ms 100"
```

Triggered when the `mcp251x` driver creates the `can0` network device
(which happens after the overlay loads and the kernel module initialises
the MCP2515).  Sets:

- `bitrate 250000` – NMEA2000 standard bit rate
- `restart-ms 100` – automatic bus-off recovery after 100 ms; standard
  practice for NMEA2000 nodes sharing a bus with other talkers

**Why not a systemd-networkd or s6 service for CAN bring-up?**
VenusOS does not use systemd-networkd.  A udev rule fires exactly once
when the device appears and requires no ongoing service, making it the
simplest and most reliable approach.  VeCanSetup uses the same pattern.

### 4.3 Shutdown monitor (`shutdown_monitor.py`)

**Installed to:** `src/shutdown_monitor.py` (within the package directory)
**Run by:** `/service/MacArthurShutdown/run`
**Interface:** Linux sysfs GPIO (`/sys/class/gpio/`)

#### State machine

```
         ┌─────────────────────────┐
   start │  INIT                   │
──────►  │  export GPIO26 & GPIO21 │
         │  GPIO26 → HIGH (DCDC on)│
         │  GPIO21 edge = falling  │
         └────────────┬────────────┘
                      │ setup complete
                      ▼
         ┌─────────────────────────┐
         │  MONITORING             │◄──────────────────┐
         │  poll(GPIO21, 30 s)     │                   │
         └────────────┬────────────┘                   │
                      │                                │
          ┌───────────┴───────────┐                   │
          │ timeout               │ edge detected      │
          │ (heartbeat)           │ value == "0"       │
          ▼                       ▼                    │
         log                ┌──────────┐  value == "1" │
         debug               │ SHUTDOWN │ (bounce)     │
                             │ PENDING  │──────────────┘
                             └────┬─────┘
                                  │ call shutdown -h now
                                  ▼
                          ┌───────────────┐
                          │  WAITING      │
                          │  sleep 90 s   │
                          └───────┬───────┘
                                  │
                      ┌───────────┴───────────┐
                      │ SIGTERM               │ timeout (90 s)
                      │ (normal path)         │ (safety fallback)
                      ▼                       ▼
              GPIO26 → LOW           GPIO26 → LOW
              unexport GPIOs         unexport GPIOs
              sys.exit(0)            return from main()
```

#### SIGTERM handler

`signal.signal(SIGTERM, on_signal)` is registered before any GPIO work.
When s6 tears down the service during shutdown, the handler:

1. Logs the signal name.
2. Drives GPIO26 LOW (`release_dcdc()`).
3. Unexports both GPIOs.
4. Calls `sys.exit(0)`.

This is the **normal shutdown path**.  The HAT sees GPIO26 go LOW and may
cut 12 V.

The same handler is installed for `SIGINT` so the service can be tested
safely from the command line (`Ctrl-C`).

#### Edge detection mechanism

The sysfs GPIO edge-detection mechanism works as follows:

1. Write `"falling"` to `/sys/class/gpio/gpio21/edge`.
2. Open `/sys/class/gpio/gpio21/value` for reading.
3. Call `select.poll()` with `POLLPRI` on the file descriptor.
4. `poll()` returns when the kernel delivers a `POLLPRI` event on a sysfs
   GPIO value file — which happens on the configured edge.
5. Seek to offset 0 and re-read to obtain the current value.

This is the correct POSIX interface for interrupt-driven GPIO monitoring
without busy-polling.  The 30-second poll timeout provides periodic
heartbeat log messages and prevents silent hangs.

#### Why Python, not C or shell?

- Python 3 is always present on VenusOS (required by the Victron dBus
  daemon infrastructure).
- The sysfs GPIO interface needs only the standard library (`os`, `select`,
  `signal`, `subprocess`).
- A shell script equivalent (`read`/`select` on sysfs) would be harder to
  test and reason about, especially for the signal-handling and cleanup
  ordering.

### 4.4 `setup` script

**File:** `setup`
**Interface:** SetupHelper `IncludeHelpers` / `CommonResources`

#### Flow

```
setup called
     │
     ▼
source IncludeHelpers
     │
     ├── scriptAction == NONE
     │       │
     │       ├── print description
     │       ├── show current N2K bridge state (enabled/disabled)
     │       ├── yesNoPrompt "Enable NMEA 2000 dbus bridge?"
     │       │       ├── Yes → rm services/MacArthurN2K/down
     │       │       └── No  → touch services/MacArthurN2K/down
     │       └── standardActionPrompt
     │
     ├── scriptAction == INSTALL
     │       │
     │       ├── updateRootToReadWrite()
     │       ├── verify overlay directory exists
     │       ├── installOverlay "macarthur-can"
     │       ├── installOverlay "macarthur-gpio"
     │       ├── addConfigBlock  → /u-boot/config.txt
     │       ├── installUdevRules
     │       ├── updateServiceRunScript  (embeds $scriptDir path in both
     │       │                            MacArthurShutdown/run and MacArthurN2K/run)
     │       ├── log N2K bridge state (down file present or absent)
     │       └── rebootNeeded=true
     │
     ├── scriptAction == UNINSTALL
     │       │
     │       ├── updateRootToReadWrite()
     │       ├── removeConfigBlock
     │       ├── removeOverlay "macarthur-can"
     │       ├── removeOverlay "macarthur-gpio"
     │       ├── removeUdevRules
     │       └── rebootNeeded=true
     │
     └── endScript INSTALL_SERVICES
             │
             ├── INSTALL:   installs MacArthurShutdown and MacArthurN2K
             │              from $servicesDir (s6 respects down file)
             └── UNINSTALL: removes both services from /service/
```

#### `installOverlay` function

Calls `updateActiveFile "$src.dtbo" "$dst.dtbo"` to copy the pre-compiled
binary shipped with the package into the boot overlay directory.  No
on-device compiler is required.  This matches the approach used by
VeCanSetup.

#### `/u-boot/config.txt` management

The script wraps its entries in a marked block:

```
# begin MacArthurVenusSetup
dtparam=spi=on
dtoverlay=macarthur-can
dtoverlay=macarthur-gpio
# end MacArthurVenusSetup
```

`addConfigBlock` always calls `removeConfigBlock` first, making the
operation idempotent (safe to run on every reinstall after a firmware
update).  `removeConfigBlock` uses `sed -i` with the begin/end markers.

Both `/u-boot/config.txt` and `/boot/config.txt` are checked at runtime
(the path differs between VenusOS versions).

#### N2K bridge enable/disable

The `MacArthurN2K` service directory ships with a `down` file, which
instructs s6 to keep the service stopped.  During the interactive `NONE`
phase of setup the user is prompted to enable or disable it.  The choice is
persisted by the presence or absence of `services/MacArthurN2K/down` in the
package directory.  On reinstall the down-file state is preserved (setup does
not touch it during `INSTALL`), so the user's choice survives VenusOS
firmware updates.

#### Service run-script generation

The `MacArthurShutdown/run` and `MacArthurN2K/run` files in the repository
contain a placeholder path (`SCRIPT_PATH_PH`).  During `INSTALL`,
`updateServiceRunScript` overwrites them with the correct absolute path
derived from `$scriptDir` (set by IncludeHelpers):

```bash
# MacArthurShutdown/run
exec /usr/bin/python3 /data/MacArthurVenusSetup/src/shutdown_monitor.py

# MacArthurN2K/run
exec /usr/bin/python3 /data/MacArthurVenusSetup/src/dbus_n2k.py
```

`endScript INSTALL_SERVICES` then copies both service directories to
`/service/`, where s6 picks them up immediately.  The `MacArthurN2K`
service will only start if its `down` file is absent.

This approach avoids hardcoding the package directory name in source
control while still providing an absolute path to s6 (which does not
inherit any shell environment).

### 4.5 NMEA 2000 dbus bridge (`dbus_n2k.py`)

**Installed to:** `src/dbus_n2k.py`
**Run by:** `/service/MacArthurN2K/run`
**Enabled by:** absence of `services/MacArthurN2K/down`

#### Background: why vecan-dbus fails

NMEA 2000 requires each node to perform **address claiming** before
transmitting.  `vecan-dbus` goes through this process at startup and only
registers discovered devices after claiming completes successfully.

The MCP2518FD transceiver on the MacArthur HAT can receive frames correctly,
but any TX attempt causes the bus error counters to climb rapidly to
ERROR-PASSIVE (RX error count ≥ 128).  Once in error-passive mode the
controller silences most subsequent reception.  The address claim is sent
and acknowledged, but the bus degrades immediately afterwards — almost
certainly a TX path issue on the transceiver (likely a missing or wrong
termination resistor, or the STBY pin held in the wrong state).

Result: vecan-dbus sends the address claim, the bus degrades, devices are
never reliably seen, and nothing appears in the VenusOS GUI.

#### Bridge architecture

`dbus_n2k.py` bypasses address claiming entirely:

```
can0 (LISTEN-ONLY)
      │  raw SocketCAN frames (SOCK_RAW, AF_CAN)
      │
      ▼
  _can_reader thread
      │  decode 29-bit NMEA 2000 / J1939 CAN ID
      │  extract PGN + source address (SA)
      │
      ├── PGN 127505 → _decode_127505()
      │       │ instance, fluid_type, level_pct, capacity_m3
      │       ▼
      │  GLib.idle_add(_update_tank, ...)
      │
      ▼
  GLib main loop (dbus main thread)
      │
      ▼
  _update_tank()
      ├── first call: _make_tank_service() → VeDbusService.register()
      │   → com.victronenergy.tank.N2K_can0_<sa>_<inst> on system dbus
      └── subsequent calls: update /Level, /Capacity, /Remaining, /Status
```

The CAN reader runs in a background daemon thread.  Updates are dispatched
to the GLib main loop via `GLib.idle_add` so that all dbus operations
occur on the correct thread.

#### VenusOS dbus service layout

Each discovered tank is registered as a `com.victronenergy.tank.*` service.
The `/DeviceInstance` is set to `20 + instance` to avoid clashing with
VE.Can built-in device instances (0–19 are reserved by convention).

| dbus path | Value |
|-----------|-------|
| `/FluidType` | Mapped from NMEA fluid type to VenusOS integer |
| `/Level` | Percentage (may be negative for a faulty out-of-range sender) |
| `/Capacity` | m³ |
| `/Remaining` | m³ (derived: `level/100 × capacity`) |
| `/Status` | 0 = OK, 1 = out-of-range |

#### Known limitation

Only PGN 127505 (Fluid Level) is currently decoded.  PGNs 127488/127489
(Engine Parameters) are visible on the bus but not yet published to dbus.

---

## 5. SetupHelper integration

### Package files

| File | Purpose |
|------|---------|
| `version` | Single-line version string (e.g. `v1.0`). PackageManager compares this against the installed version to decide whether reinstallation is needed. |
| `gitHubInfo` | `<username>:<branch>` — tells PackageManager where to check for updates. |
| `raspberryPiOnly` | Presence of this file causes PackageManager to skip the package on non-RPi hardware (Cerbo GX, Ekrano GX, etc.). |
| `changes` | Human-readable changelog shown in the PackageManager GUI. |
| `setup` | Entry point for all install/uninstall operations. Must be executable. |

### Boot-time reinstallation

After a VenusOS firmware update the system boots to a fresh partition.
SetupHelper's `reinstallMods` script (hooked into `/data/rcS.local`) runs
at boot and sets a `REINSTALL_PACKAGES` flag.  PackageManager detects the
flag and calls each package's `setup` script with:

```
setup reinstall auto deferReboot
```

`IncludeHelpers` translates these arguments into `scriptAction=INSTALL` and
`runningAtBoot=true`.  The `setup` script recompiles overlays, updates
`config.txt`, reinstalls udev rules, rewrites the service run-script, and
signals `rebootNeeded=true`.  SetupHelper schedules a reboot.

### `endScript INSTALL_SERVICES`

`endScript` from `CommonResources` performs the final bookkeeping:

- **INSTALL:** copies `$scriptDir/services/MacArthurShutdown/` to
  `/service/MacArthurShutdown/`; waits up to 10 s for s6 to start the
  service; records the installed version in
  `/etc/venus/installedVersion-MacArthurVenusSetup`.
- **UNINSTALL:** removes `/service/MacArthurShutdown/`; clears the
  installed-version record.
- Evaluates `rebootNeeded` and exits with the appropriate SetupHelper
  exit code (`123` = reboot required).

---

## 6. Design decisions and trade-offs

### 6.1 Single package vs. depending on VeCanSetup/RpiGpioSetup

| Option | Pro | Con |
|--------|-----|-----|
| Depend on VeCanSetup | Less code duplication; CAN dBus service managed correctly | Extra dependency; install order matters; VeCanSetup's HAT configuration is interactive |
| Standalone (chosen) | Single install step; no interaction required; clearly scoped | Duplicates some overlay work; user must run VeCanSetup separately for NMEA2000 dBus service |

The chosen approach minimises installation friction.  CAN bus bring-up
(overlay + udev) is straightforward; the complex NMEA2000 dBus bridge
configuration is intentionally left to VeCanSetup, which already does it
well.

### 6.2 DTS overlay compiled on device vs. shipped pre-built

| Option | Pro | Con |
|--------|-----|-----|
| Ship pre-built `.dtbo` (chosen) | No dependency on `dtc`; zero install-time build step; matches VeCanSetup's approach | Binary committed to git; must be rebuilt when DTS source changes |
| Compile on device | Always matches the running kernel's DT format | `dtc` is not present on VenusOS by default |

VenusOS does not ship `dtc`.  Pre-compiling the overlays on a development
machine and committing the `.dtbo` binaries alongside the `.dts` sources is
the same pattern used by VeCanSetup.  The `.dts` source files remain in the
repository for auditability; the rule is: **edit the `.dts`, recompile,
commit both**.

### 6.3 sysfs GPIO vs. libgpiod for the shutdown monitor

| Option | Pro | Con |
|--------|-----|-----|
| sysfs (chosen) | No extra packages needed; works on all kernel versions shipped with VenusOS | Deprecated in upstream kernel (not removed); poll-based edge detection has minor latency |
| libgpiod / `python3-gpiod` | Modern API; not deprecated | `python3-gpiod` not guaranteed to be present on VenusOS; adds a dependency |

The sysfs interface, while deprecated in mainline kernel documentation, is
present and functional in every VenusOS kernel version and requires zero
additional packages.

### 6.4 N2K dbus bridge vs. waiting for a hardware fix

| Option | Pro | Con |
|--------|-----|-----|
| Ship the bridge (chosen) | Fuel/engine data works today; no hardware rework needed | Adds a second service; only decodes a subset of PGNs; ships disabled to avoid confusion |
| Require hardware fix first | Correct vecan-dbus path; full PGN support | Transceiver TX path issue may be non-trivial (soldering required); blocks any NMEA 2000 data in the meantime |
| LISTEN-ONLY via VeCanSetup | Would use the existing vecan-dbus infrastructure | VeCanSetup has no listen-only mode; address claiming is hardwired into its startup path |

The bridge is an opt-in workaround, not a permanent replacement.  If the
transceiver TX path is repaired, the bridge can be disabled via `setup` and
VeCanSetup used instead with no package changes.

### 6.5 90-second shutdown timeout

The timeout exists to handle the case where `shutdown -h now` is called but
s6 never delivers `SIGTERM` to the service (e.g. the init system itself
hangs).  Without it, GPIO26 would stay HIGH indefinitely after a failed
shutdown attempt and the HAT could not cut power.

90 s is generous enough for a normal VenusOS shutdown (typically under 15 s)
while being short enough to avoid prolonged power waste on a vessel's
12 V battery system.

---

## 7. File inventory

```
MacArthurVenusSetup/
│
├── setup                     SetupHelper install/uninstall script
├── version                   Package version ("v1.0")
├── gitHubInfo                GitHub username:branch for PackageManager
├── raspberryPiOnly           Marker file – restricts to RPi platforms
├── changes                   Changelog
│
├── overlays/
│   ├── macarthur-can.dts     DT overlay source: MCP2518FD on SPI0 CE1 (GPIO7)
│   └── macarthur-gpio.dts    DT overlay source: GPIO21 (input) + GPIO26 (output)
│
├── udev/
│   └── 42-macarthur.rules    CAN interface bringup: can0 @ 250 kbps
│
├── services/
│   ├── MacArthurShutdown/
│   │   ├── run               s6 run script (rewritten at install time)
│   │   └── log/
│   │       └── run           s6 log run (svlogd → ./main/)
│   └── MacArthurN2K/
│       ├── run               s6 run script (rewritten at install time)
│       ├── down              Present = service disabled (default)
│       └── log/
│           └── run           s6 log run (svlogd → ./main/)
│
├── src/
│   ├── shutdown_monitor.py   Shutdown monitor daemon
│   └── dbus_n2k.py           NMEA 2000 → VenusOS dbus bridge (listen-only)
│
└── docs/
    ├── user-guide.md         End-user installation and operation guide
    └── design.md             This document
```

---

## 8. Testing notes

### Unit testing the shutdown monitor

The daemon can be tested on any Linux machine with sysfs GPIO support
(or a software GPIO simulator):

```bash
# Export GPIOs manually
echo 21 > /sys/class/gpio/export
echo 26 > /sys/class/gpio/export

# Run the monitor
python3 src/shutdown_monitor.py &

# Simulate a shutdown request
echo 0 > /sys/class/gpio/gpio21/value

# Observe GPIO26 going low after shutdown sequence
```

### Integration testing on VenusOS

1. Install the package and reboot.
2. Verify `can0` is up: `ip link show can0`.
3. Verify service is running: `svstat /service/MacArthurShutdown`.
4. Verify GPIO26 is HIGH: `cat /sys/class/gpio/gpio26/value`.
5. Simulate shutdown request: `echo 0 > /sys/class/gpio/gpio21/value`
   (requires temporarily overriding the pull-up with an external ground).
6. Observe graceful shutdown and GPIO26 going LOW.
7. Restore power; verify the RPi reboots and the service reasserts GPIO26 HIGH.
