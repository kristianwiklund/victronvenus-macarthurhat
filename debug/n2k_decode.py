#!/usr/bin/env python3
"""
Receive and decode NMEA 2000 frames from a socketcand server.

Connects to a socketcand daemon (local or via SSH tunnel) using python-can,
decodes each frame with the nmea2000 library, and prints one JSON object
per decoded message to stdout.

Usage:
    python3 n2k_decode.py [--host HOST] [--port PORT] [--channel CHANNEL]
                          [--count N] [--summary]

    --host      socketcand host (default: 127.0.0.1)
    --port      socketcand port (default: 29536)
    --channel   CAN interface name on the remote host (default: can0)
    --count     stop after N decoded messages (default: 50; 0 = unlimited)
    --summary   print a per-PGN summary table instead of raw JSON

Typical setup (VenusOS / Victron Cerbo):
    1. On the target device:
           socketcand -i can0 -l lo -p 29536 -n &

    2. Forward the port to your workstation:
           ssh -N -L 29536:localhost:29536 root@<device-ip>

    3. Run this script:
           python3 n2k_decode.py --summary

Dependencies (install into a venv):
    pip install nmea2000
"""

import argparse
import json
import sys
from collections import defaultdict

import can
from nmea2000 import NMEA2000Decoder


def parse_args():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--host",    default="127.0.0.1", help="socketcand host")
    p.add_argument("--port",    type=int, default=29536, help="socketcand port")
    p.add_argument("--channel", default="can0", help="CAN interface on remote host")
    p.add_argument("--count",   type=int, default=50,
                   help="stop after N decoded messages (0 = unlimited)")
    p.add_argument("--summary", action="store_true",
                   help="print per-PGN summary instead of raw JSON")
    return p.parse_args()


def summarise(messages: dict):
    print(f"\n{'PGN':>7}  {'Message':45}  {'Src':>3}  {'Count':>5}  Fields")
    print("-" * 100)
    for pgn, ms in sorted(messages.items()):
        m = ms[-1]
        fields = {f["id"]: f["value"]
                  for f in m["fields"]
                  if f["value"] is not None and not f["id"].startswith("reserved")}
        print(f"{pgn:>7}  {m['id']:45}  {m['source']:>3}  {len(ms):>5}  {fields}")


def main():
    args = parse_args()

    decoder = NMEA2000Decoder()

    print(f"Connecting to socketcand at {args.host}:{args.port} channel={args.channel} ...",
          file=sys.stderr, flush=True)

    try:
        bus = can.interface.Bus(
            interface="socketcand",
            channel=args.channel,
            host=args.host,
            port=args.port,
        )
    except Exception as e:
        sys.exit(f"Failed to connect: {e}")

    print("Connected. Receiving frames...", file=sys.stderr, flush=True)

    decoded_count = 0
    decode_errors = 0
    messages = defaultdict(list)

    try:
        for msg in bus:
            try:
                decoded = decoder.decode_python_can(msg)
                if decoded is None:
                    continue
                decoded_count += 1
                if args.summary:
                    messages[decoded.PGN].append(json.loads(decoded.to_json()))
                else:
                    print(decoded.to_json(), flush=True)
                if args.count and decoded_count >= args.count:
                    break
            except Exception as e:
                decode_errors += 1
                if decode_errors <= 5:
                    print(f"  [decode error: {e}  raw: {msg}]", file=sys.stderr, flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        bus.shutdown()

    if args.summary:
        summarise(messages)

    print(f"\nDecoded {decoded_count} messages, {decode_errors} decode errors.",
          file=sys.stderr)


if __name__ == "__main__":
    main()
