# MacArthurVenusSetup

[SetupHelper](https://github.com/kwindrem/SetupHelper) package that makes the
**MacArthur navigation HAT** work on a Raspberry Pi running Victron VenusOS.
Installs the MCP2518FD CAN driver overlay, brings up `can0` for NMEA 2000,
configures the shutdown-safe power-management GPIOs, and (optionally) bridges
NMEA 2000 tank data directly to the VenusOS dbus without requiring CAN TX.

Requires **VenusOS ≥ v2.90** and **[SetupHelper](https://github.com/kwindrem/SetupHelper)**.

The optional **MacArthurN2K** service handles a possible hardware issue on the
MacArthur HAT: the CAN transceiver TX path may be faulty, so any transmission
causes the RX error counter to climb to ERROR-PASSIVE and most subsequent
frames are dropped.  This breaks the normal VenusOS NMEA 2000 path
(`vecan-dbus`), which must transmit to complete address claiming before it
will register any devices.  MacArthurN2K works around this by reading raw
CAN frames in listen-only mode — no TX, no address claiming — and publishing
device data (tank levels, …) directly to the VenusOS dbus.  See
[docs/tx-troubleshooting.md](docs/tx-troubleshooting.md) for the full
hardware root-cause analysis.

---

## Documentation

| Document | Contents |
|----------|----------|
| [docs/user-guide.md](docs/user-guide.md) | Installation, configuration, N2K bridge setup, verification, troubleshooting |
| [docs/design.md](docs/design.md) | Architecture, component design, hardware model, design decisions |
| [docs/tx-troubleshooting.md](docs/tx-troubleshooting.md) | Root-cause analysis of the MCP2562 / MCP2518FD TX voltage mismatch |

---

## Quick start

```bash
scp -r MacArthurVenusSetup root@<venus-ip>:/data/
ssh root@<venus-ip> /data/MacArthurVenusSetup/setup
# follow the prompts, then reboot
```

See [docs/user-guide.md](docs/user-guide.md) for full installation options and
post-install verification steps.
