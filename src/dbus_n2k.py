#!/usr/bin/env python3
"""
MacArthur HAT – NMEA 2000 to VenusOS dbus bridge
=================================================

Reads NMEA 2000 frames from can0 (LISTEN-ONLY mode, no address claiming
required) and publishes device data to the VenusOS dbus.

Why this exists instead of relying on vecan-dbus
-------------------------------------------------
The MCP2518FD CAN controller on the MacArthur HAT can receive on the
NMEA 2000 bus without errors, but any transmission attempt causes the
rx error counter to climb to ERROR-PASSIVE (128), after which most
frames are missed.  Root cause is likely a transceiver TX path issue.

vecan-dbus requires completing address claiming (which involves TX) before
it will register any discovered devices.  This service bypasses that by
reading raw frames directly and publishing to dbus without participating
in NMEA 2000 address claiming.

PGNs handled
------------
  127505  Fluid Level  →  com.victronenergy.tank.N2K_can0_<sa>_<inst>
"""

import sys
import os
import socket
import struct
import signal
import logging
from threading import Thread

# VenusOS velib_python – present on every VenusOS installation.
sys.path.insert(1, '/opt/victronenergy/dbus-systemcalc-py/ext/velib_python')

import dbus
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib
from vedbus import VeDbusService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger("N2KDbus")

VERSION   = "1.0"
CAN_IFACE = "can0"

# NMEA 2000 fluid type → VenusOS /FluidType integer
# NMEA: 0=fuel 1=fresh water 2=gray water 3=live well 4=oil 5=black water 6=gasoline
# Venus: 0=fuel 1=fresh water 2=waste water 3=live well 4=oil 5=black water 11=gasoline
_FLUID_TYPE_MAP = {0: 0, 1: 1, 2: 2, 3: 3, 4: 4, 5: 5, 6: 11}
_FLUID_NAMES    = {
    0: "Fuel", 1: "Fresh Water", 2: "Waste Water",
    3: "Live Well", 4: "Oil", 5: "Black Water", 6: "Gasoline",
}

# Active tank services: (sa, instance) → VeDbusService
_tanks: dict = {}
_dbus_conn    = None


# ── NMEA 2000 frame decoding ──────────────────────────────────────────────────

def _extract_pgn(can_id_29: int):
    """Return (pgn, sa) from a 29-bit NMEA 2000 / J1939 CAN ID."""
    sa  =  can_id_29 & 0xFF
    ps  = (can_id_29 >>  8) & 0xFF
    pf  = (can_id_29 >> 16) & 0xFF
    dp  = (can_id_29 >> 24) & 0x01
    # PDU2 (pf >= 0xF0): PS is a group extension, part of the PGN.
    # PDU1 (pf <  0xF0): PS is the destination address, NOT part of the PGN.
    pgn = (dp << 16) | (pf << 8) | (ps if pf >= 0xF0 else 0)
    return pgn, sa


def _decode_127505(data: bytes):
    """
    Decode PGN 127505 Fluid Level (single-frame, 8 bytes).
    Returns (instance, fluid_type, level_pct, capacity_m3) or None on error.

    Wire format (little-endian):
      byte  0      bits 3-0 = fluid instance (0-15)
                   bits 7-4 = fluid type (0=fuel … 6=gasoline)
      bytes 1-2    signed int16: level, resolution 0.004 % → 25000 = 100 %
      bytes 3-6    uint32: tank capacity, resolution 0.1 L
      byte  7      reserved (0xFF)
    """
    if len(data) < 7:
        return None
    instance     =  data[0] & 0x0F
    fluid_type   = (data[0] >> 4) & 0x0F
    level_raw    = struct.unpack_from('<h', data, 1)[0]   # signed
    capacity_raw = struct.unpack_from('<I', data, 3)[0]   # unsigned
    level_pct    = level_raw    / 250.0          # 0.004 % / LSB
    capacity_m3  = capacity_raw * 0.1 / 1000.0  # 0.1 L / LSB → m³
    return instance, fluid_type, level_pct, capacity_m3


# ── VenusOS dbus publishing ───────────────────────────────────────────────────

def _make_tank_service(sa: int, instance: int, fluid_type: int) -> VeDbusService:
    """Create and register a com.victronenergy.tank dbus service."""
    svc_name   = f"com.victronenergy.tank.N2K_{CAN_IFACE}_{sa}_{instance}"
    fluid_name = _FLUID_NAMES.get(fluid_type, f"Tank {instance}")
    ve_fluid   = _FLUID_TYPE_MAP.get(fluid_type, 0)
    # Use NMEA 2000 device instance offset by 20 to avoid clashing with
    # VE.Can built-in device instances (0-19 are reserved by VenusOS convention).
    dev_instance = 20 + instance

    svc = VeDbusService(svc_name, bus=_dbus_conn, register=False)
    svc.add_path('/Mgmt/ProcessName',    os.path.basename(__file__))
    svc.add_path('/Mgmt/ProcessVersion', VERSION)
    svc.add_path('/Mgmt/Connection',     f'NMEA 2000 {CAN_IFACE} SA {sa}')
    svc.add_path('/DeviceInstance',      dev_instance)
    svc.add_path('/ProductName',         'NMEA 2000 Tank Sensor')
    svc.add_path('/ProductId',           0)
    svc.add_path('/FirmwareVersion',     0)
    svc.add_path('/HardwareVersion',     0)
    svc.add_path('/Serial',              f'N2K-{CAN_IFACE}-{sa}-{instance}')
    svc.add_path('/Connected',           1)
    svc.add_path('/FluidType',           ve_fluid)
    svc.add_path('/Level',               None)
    svc.add_path('/Remaining',           None)
    svc.add_path('/Capacity',            None)
    svc.add_path('/Status',              0)
    svc.add_path('/CustomName',          fluid_name)
    svc.register()
    log.info("Registered %s (fluid=%s, devinstance=%d)",
             svc_name, fluid_name, dev_instance)
    return svc


def _update_tank(sa: int, instance: int, fluid_type: int,
                 level_pct: float, capacity_m3: float):
    """Create-or-update a tank service.  Runs in the GLib main loop."""
    key = (sa, instance)
    if key not in _tanks:
        try:
            _tanks[key] = _make_tank_service(sa, instance, fluid_type)
        except Exception as exc:
            log.error("Failed to create tank service for SA %d inst %d: %s", sa, instance, exc)
            _tanks[key] = None   # sentinel: don't retry
    if _tanks[key] is None:

        return False
    svc = _tanks[key]
    svc['/Level']    = round(level_pct,    2)
    svc['/Capacity'] = round(capacity_m3,  4)
    remaining = (level_pct / 100.0) * capacity_m3
    svc['/Remaining'] = round(remaining, 4) if capacity_m3 > 0 else None
    # Status: 0 = OK, 1 = out-of-range (e.g. negative level from faulty sender)
    svc['/Status'] = 0 if 0.0 <= level_pct <= 100.0 else 1
    return False   # do not reschedule from GLib.idle_add


# ── CAN reader thread ─────────────────────────────────────────────────────────

_CAN_EFF_FLAG = 0x80000000   # Linux socketCAN: extended frame bit in can_id
_AF_CAN       = 29
_CAN_RAW      = 1
_FRAME_FMT    = "<IB3x8s"    # can_id (LE uint32), dlc, 3-byte pad, 8-byte data
_FRAME_SZ     = struct.calcsize(_FRAME_FMT)  # 16


def _can_reader(mainloop: GLib.MainLoop) -> None:
    """Background thread: read raw CAN frames, dispatch via GLib.idle_add."""
    sock = socket.socket(_AF_CAN, socket.SOCK_RAW, _CAN_RAW)
    try:
        sock.bind((CAN_IFACE,))
    except OSError as exc:
        log.error("Cannot bind CAN socket to %s: %s", CAN_IFACE, exc)
        GLib.idle_add(mainloop.quit)
        return

    log.info("Listening on %s for NMEA 2000 frames", CAN_IFACE)

    while True:
        try:
            raw = sock.recv(_FRAME_SZ)
        except OSError as exc:
            log.error("CAN recv error: %s", exc)
            GLib.idle_add(mainloop.quit)
            break

        if len(raw) < _FRAME_SZ:
            continue

        can_id, dlc, data = struct.unpack(_FRAME_FMT, raw)

        # Only process 29-bit extended frames (NMEA 2000 always uses EFF).
        if not (can_id & _CAN_EFF_FLAG):
            continue

        pgn, sa = _extract_pgn(can_id & 0x1FFFFFFF)

        if pgn == 0x1F211:   # PGN 127505 – Fluid Level
            decoded = _decode_127505(data[:dlc])
            if decoded:
                GLib.idle_add(_update_tank, sa, *decoded)


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    global _dbus_conn
    log.info("MacArthur N2K dbus bridge v%s starting on %s", VERSION, CAN_IFACE)

    DBusGMainLoop(set_as_default=True)
    _dbus_conn = dbus.SystemBus()
    mainloop   = GLib.MainLoop()

    def _on_signal(signum, _frame):
        log.info("Signal %d received – stopping", signum)
        GLib.idle_add(mainloop.quit)

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT,  _on_signal)

    reader = Thread(target=_can_reader, args=(mainloop,), daemon=True)
    reader.start()

    try:
        mainloop.run()
    except KeyboardInterrupt:
        pass
    log.info("Exiting")


if __name__ == "__main__":
    main()
