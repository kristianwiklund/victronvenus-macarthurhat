#!/usr/bin/env bash
# Set up an SSH port-forward tunnel to socketcand on a VenusOS device,
# and optionally start socketcand on the remote side if it is not running.
#
# Usage:
#   ./setup_tunnel.sh <user@host>
#
# Example:
#   ./setup_tunnel.sh root@192.168.1.100
#
# The tunnel maps local port 29536 → remote localhost:29536.
# Leave the terminal open (Ctrl-C to close), then in another terminal run:
#   python3 n2k_decode.py --summary

set -euo pipefail

TARGET="${1:-}"
if [[ -z "$TARGET" ]]; then
    echo "Usage: $0 <user@host>" >&2
    exit 1
fi

PORT=29536
CHANNEL="can0"

echo ">>> Starting socketcand on $TARGET (if not already running)..."
ssh "$TARGET" "
    if ! pgrep -x socketcand >/dev/null 2>&1; then
        socketcand -i $CHANNEL -l lo -p $PORT -n &
        sleep 1
        echo 'socketcand started.'
    else
        echo 'socketcand already running.'
    fi
"

echo ">>> Opening SSH tunnel: local $PORT -> remote localhost:$PORT"
echo "    Press Ctrl-C to close the tunnel."
ssh -N -L "${PORT}:localhost:${PORT}" "$TARGET"
