# debug/

Tools for diagnosing the MacArthur HAT CAN bus (NMEA 2000) on a VenusOS device.

---

## Quick start

### 1. Check hardware health (on the device)

```sh
ssh root@<device> bash -s < can_status.sh
```

Prints `ip link` details (error counters, RX/TX stats), relevant `dmesg` lines,
and 20 raw `candump` frames.  No dependencies beyond standard VenusOS tools.

---

### 2. Decode live NMEA 2000 traffic (from your workstation)

**Dependencies** – install once into a Python venv:

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install nmea2000
```

**Terminal 1** – open the SSH tunnel (starts socketcand on the device if needed):

```sh
./setup_tunnel.sh root@<device>
```

**Terminal 2** – decode and summarise:

```sh
python3 n2k_decode.py --summary
```

Or stream raw JSON:

```sh
python3 n2k_decode.py --count 0   # unlimited; Ctrl-C to stop
```

---

## Files

| File | Purpose |
|---|---|
| `can_status.sh` | One-shot hardware health check (run locally or piped via ssh) |
| `setup_tunnel.sh` | Starts socketcand on the device and opens the SSH port-forward |
| `n2k_decode.py` | Connects to socketcand, decodes frames, prints JSON or summary |

---

## What we observed (MacArthur HAT / MCP2518FD)

Bus: `can0` at 250 kbit/s, LISTEN-ONLY mode (correct — the HAT should not transmit
on an NMEA 2000 backbone unless specifically configured to do so).

Single source device (SA 23 / 0x17) transmitting:

| PGN | Message | Notes |
|---|---|---|
| 127488 | Engine Parameters, Rapid Update | RPM = 0.0 (engine off) |
| 127489 | Engine Parameters, Dynamic | Fault flags: Check Engine, Over Temperature, Low Oil Pressure |
| 127501 | Binary Switch Bank Status | Switch 1 = Off |
| 127505 | Fluid Level | Fuel; negative level value indicates out-of-range tank sender |

The fault flags on PGN 127489 and the negative fuel level on PGN 127505 may
warrant further investigation at the sender side.

---

## Troubleshooting notes

**`socketcand: invalid option -- 'b'`** — the `-b` (bind address) flag is not
supported on all builds.  Use `-l <interface>` instead (e.g. `-l lo` to bind to
loopback).

**`nmea2000-cli` bus_kwargs bug** — the `nmea2000-cli can_client --bus-kwargs`
argument is not unpacked correctly into the python-can `SocketCanDaemonBus`
constructor (it passes a nested dict instead of `host=` / `port=` kwargs).
`n2k_decode.py` bypasses the CLI and calls python-can directly to avoid this.

**RX drop counter climbing** — observed ~1700 drops out of ~15 000 frames on
first boot.  Likely caused by the kernel socket buffer filling before userspace
opens the socket.  Not indicative of a hardware problem.
