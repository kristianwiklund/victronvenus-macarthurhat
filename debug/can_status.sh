#!/usr/bin/env bash
# Print a quick health summary of can0 on a VenusOS / Linux device.
# Useful as a first-pass diagnostic before pulling live frames.
#
# Usage (local):   ./can_status.sh
# Usage (remote):  ssh root@<host> bash -s < can_status.sh

IFACE="${1:-can0}"

echo "=== ip link: $IFACE ==="
ip -details -statistics link show "$IFACE" 2>&1

echo ""
echo "=== dmesg (mcp / can / spi) ==="
dmesg 2>/dev/null | grep -iE "mcp|can[^_]|spi" | tail -20

echo ""
echo "=== candump (20 frames) ==="
candump -n 20 "$IFACE" 2>&1
