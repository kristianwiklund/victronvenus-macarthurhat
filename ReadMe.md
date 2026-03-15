# MacArthurVenusSetup

VenusOS package for the **MacArthur navigation HAT** on Raspberry Pi.

Configures the HAT's CAN bus interface for NMEA2000, sets up the GPIO lines
for power management, and installs a shutdown monitor daemon that ensures a
graceful shutdown before the HAT cuts 12 V power to the RPi.

Compatible with and requires **[SetupHelper](https://github.com/kwindrem/SetupHelper)**
by Kevin Windrem.

---

## What this package installs

| Component | Detail |
|---|---|
| `macarthur-can.dtbo` | Device-tree overlay for MCP2518FD on SPI0 CE1 (GPIO7), 20 MHz oscillator, IRQ on GPIO25 |
| `macarthur-gpio.dtbo` | Device-tree overlay configuring GPIO26 as output (DCDC_EN) and GPIO21 as pull-up input (SHUTDOWN_REQ) |
| `/u-boot/config.txt` entries | Enables SPI0 and loads both overlays at boot |
| `/etc/udev/rules.d/42-macarthur.rules` | Brings up `can0` at 250 kbit/s (NMEA2000) when the MCP2518FD interface appears |
| `MacArthurShutdown` s6 service | Shutdown monitor daemon (see below) |
| `MacArthurN2K` s6 service | Optional NMEA 2000 → dbus bridge (see below); ships disabled by default |

---

## GPIO assignments

| GPIO | Pin | Direction | Function |
|---|---|---|---|
| GPIO26 | 37 | Output | **DCDC_EN** – held HIGH by the RPi to keep the HAT's DC-DC converter enabled. Driven LOW after graceful shutdown so the HAT may safely cut 12 V supply. |
| GPIO21 | 40 | Input (pull-up) | **SHUTDOWN_REQ** – the HAT drives this LOW (e.g. on loss of 12 V) to request a graceful shutdown. |
| GPIO25 | 22 | Input | MCP2518FD interrupt (wired to CAN controller, handled by kernel driver) |
| GPIO7  | 26 | SPI CE1 | MCP2518FD SPI chip-select (Linux `spi0.1`) |

---

## Shutdown sequence

1. **Boot** – the `MacArthurShutdown` service starts and drives GPIO26 HIGH
   immediately, keeping the HAT's DC-DC converter enabled.
2. **Shutdown request** – the HAT drives GPIO21 LOW (e.g. on low-voltage
   detection). The service detects the falling edge and calls
   `shutdown -h now`.
3. **Graceful shutdown** – VenusOS unmounts filesystems and s6 terminates
   services. When `MacArthurShutdown` receives SIGTERM it drives GPIO26 LOW.
4. **Power cut** – the HAT detects GPIO26 LOW and cuts 12 V to the RPi.

> **Safety fallback**: if the init system does not send SIGTERM within 90 s
> after `shutdown -h now` is issued, the service drives GPIO26 LOW anyway.

---

## CAN / NMEA2000

The `mcp251xfd` kernel driver creates a standard Linux SocketCAN interface
(`can0`).  The udev rule brings it up at **250 kbit/s** with automatic
bus-off recovery.

### vecan-dbus (normal path)

For full NMEA2000 integration in the VenusOS GUI (device list, tank/GPS
data) you can additionally install
[VeCanSetup](https://github.com/kwindrem/VeCanSetup) and configure `can0`
as an NMEA2000 port.  This package ensures the hardware is working so
VeCanSetup can detect and use it.

> **Known hardware limitation – CAN TX path:** The MCP2518FD transceiver
> on the MacArthur HAT can **receive** on the NMEA 2000 bus without errors,
> but any transmission attempt causes the RX error counter to climb to
> ERROR-PASSIVE (≥ 128), after which most subsequent frames are dropped.
> Root cause is a CAN transceiver TX path issue on the HAT (likely a
> termination or STBY-pin problem).
>
> `vecan-dbus` requires completing NMEA 2000 address claiming (which
> involves TX) before it will register any discovered devices.  If your
> transceiver has this issue, devices will never appear through vecan-dbus.

### MacArthurN2K dbus bridge (TX-impaired workaround)

The optional `MacArthurN2K` service (`src/dbus_n2k.py`) provides a
workaround.  It opens a raw SocketCAN socket in **LISTEN-ONLY** mode (zero
bus participation) and publishes discovered NMEA 2000 devices directly to
the VenusOS dbus without ever transmitting on the bus.

Currently decoded PGNs:

| PGN | Description | VenusOS service |
|-----|-------------|-----------------|
| 127505 | Fluid Level | `com.victronenergy.tank.N2K_can0_<sa>_<inst>` |

The service ships **disabled** (a `down` file is present).  To enable it,
run `setup` and answer **Yes** to the N2K bridge prompt:

```
Enable NMEA 2000 dbus bridge (workaround for CAN TX issues)?
```

If the TX path is later repaired (transceiver swap, termination fix),
disable the bridge via the same prompt and configure VeCanSetup instead.

---

## Requirements

* Raspberry Pi 4 (or Pi 5 – see note below) running VenusOS ≥ v2.90
* [SetupHelper](https://github.com/kwindrem/SetupHelper) installed in
  `/data/SetupHelper/`

### Raspberry Pi 5 note

On Pi 5 the UART numbering changes (UART2/4 → UART3/5) but the SPI0 and
GPIO assignments used by this package remain the same.  No changes needed.

---

## Installation

### Via SetupHelper PackageManager (recommended)

1. Install SetupHelper if not already present.
2. Copy / extract this repository to `/data/MacArthurVenusSetup/` on the
   Venus device.
3. Run `/data/MacArthurVenusSetup/setup` or use the PackageManager GUI.
4. Reboot.

### Manual

```bash
scp -r MacArthurVenusSetup root@<venus-ip>:/data/
ssh root@<venus-ip> /data/MacArthurVenusSetup/setup
# follow the prompts, then reboot
```

### Via removable media (offline)

Create a zip archive of this directory, place it on a USB stick or SD card,
and connect it to the Venus device.  SetupHelper's PackageManager will detect
and offer to install it automatically when `AUTO_INSTALL_PACKAGES` is set.

---

## Uninstall

```bash
/data/MacArthurVenusSetup/setup
# choose Uninstall at the prompt, then reboot
```

Or via the SetupHelper PackageManager GUI.

---

## Directory layout

```
MacArthurVenusSetup/
├── setup                        SetupHelper install/uninstall script
├── version                      Package version
├── gitHubInfo                   GitHub repo info for PackageManager updates
├── raspberryPiOnly              Marks package as RPi-only
├── changes                      Changelog
├── overlays/
│   ├── macarthur-can.dts        MCP2515 CAN controller device-tree source
│   └── macarthur-gpio.dts       GPIO26/GPIO21 device-tree source
├── udev/
│   └── 42-macarthur.rules       CAN interface bringup udev rule
├── services/
│   ├── MacArthurShutdown/
│   │   ├── run                  s6 service run script (rewritten at install)
│   │   └── log/
│   │       └── run              s6 log service (svlogd)
│   └── MacArthurN2K/
│       ├── run                  s6 service run script (rewritten at install)
│       ├── down                 Present = service disabled (default)
│       └── log/
│           └── run              s6 log service (svlogd)
└── src/
    ├── shutdown_monitor.py      Shutdown monitor daemon
    └── dbus_n2k.py              NMEA 2000 → VenusOS dbus bridge
```
