#!/usr/bin/env python3
"""
MacArthur HAT – Shutdown Monitor for VenusOS
=============================================

GPIO roles
----------
GPIO26  DCDC_EN   Output.  MUST be held HIGH by the RPi to keep the HAT's
                  DC-DC converter enabled.  Driven LOW only after the system
                  has fully shut down (filesystems unmounted) so the HAT can
                  safely cut 12 V power.

GPIO21  SHUTDOWN_REQ  Input, active-low, internal pull-up (set by the
                      macarthur-gpio device-tree overlay).  The HAT drives
                      this pin LOW to request a graceful shutdown.

Sequence
--------
1. Service starts → GPIO26 driven HIGH immediately (DCDC stays on).
2. GPIO21 monitored via sysfs edge-detection (poll POLLPRI).
3. Falling edge on GPIO21 → initiate "shutdown -h now".
4. SIGTERM received from the init system (s6) during any shutdown →
   GPIO26 driven LOW → HAT may cut power.
5. Safety fallback: if we are still alive 90 s after requesting shutdown,
   drive GPIO26 LOW anyway.
"""

import os
import sys
import time
import select
import signal
import logging
import subprocess

# ---------------------------------------------------------------------------
# Logging – goes to stdout which s6/svlogd captures and rotates.
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger("MacArthurShutdown")

# ---------------------------------------------------------------------------
# Hardware constants
# ---------------------------------------------------------------------------
SHUTDOWN_GPIO = 21   # SHUTDOWN_REQ: HAT pulls LOW to request shutdown
DCDC_EN_GPIO  = 26   # DCDC_EN:      RPi holds HIGH to keep power on

SYSFS_GPIO = "/sys/class/gpio"

# ---------------------------------------------------------------------------
# sysfs GPIO helpers
# ---------------------------------------------------------------------------

def _write(path: str, value) -> None:
    with open(path, "w") as fh:
        fh.write(str(value))


def _read(path: str) -> str:
    with open(path, "r") as fh:
        return fh.read().strip()


def gpio_export(pin: int) -> None:
    node = f"{SYSFS_GPIO}/gpio{pin}"
    if not os.path.exists(node):
        _write(f"{SYSFS_GPIO}/export", pin)
        deadline = time.monotonic() + 2.0
        while not os.path.exists(node):
            if time.monotonic() > deadline:
                raise RuntimeError(f"Timed out waiting for GPIO{pin} sysfs node")
            time.sleep(0.05)


def gpio_unexport(pin: int) -> None:
    if os.path.exists(f"{SYSFS_GPIO}/gpio{pin}"):
        try:
            _write(f"{SYSFS_GPIO}/unexport", pin)
        except OSError:
            pass


def gpio_direction(pin: int, direction: str) -> None:
    """Set direction: 'in' or 'out'."""
    _write(f"{SYSFS_GPIO}/gpio{pin}/direction", direction)


def gpio_edge(pin: int, edge: str) -> None:
    """Set edge detection: 'none', 'rising', 'falling', or 'both'."""
    _write(f"{SYSFS_GPIO}/gpio{pin}/edge", edge)


def gpio_write(pin: int, value: int) -> None:
    _write(f"{SYSFS_GPIO}/gpio{pin}/value", value)


def gpio_read(pin: int) -> int:
    return int(_read(f"{SYSFS_GPIO}/gpio{pin}/value"))

# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

def release_dcdc(reason: str = "") -> None:
    """Drive GPIO26 (DCDC_EN) LOW – HAT is now permitted to cut power."""
    try:
        log.info("GPIO%d (DCDC_EN) → LOW: %s", DCDC_EN_GPIO, reason or "shutdown complete")
        gpio_write(DCDC_EN_GPIO, 0)
    except Exception as exc:
        log.error("Failed to release DCDC_EN: %s", exc)


def _cleanup_gpios() -> None:
    gpio_unexport(SHUTDOWN_GPIO)
    gpio_unexport(DCDC_EN_GPIO)


def on_signal(signum, _frame) -> None:
    """
    Called when s6 (or the kernel) sends SIGTERM/SIGINT during shutdown.
    This is the normal path: filesystems are already unmounted when s6
    tears down services.  Drive DCDC_EN LOW so the HAT can cut power.
    """
    name = signal.Signals(signum).name
    log.info("Received %s – releasing DCDC_EN and exiting", name)
    release_dcdc(f"service terminated ({name})")
    _cleanup_gpios()
    sys.exit(0)

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    log.info("MacArthur HAT Shutdown Monitor starting")

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT,  on_signal)

    # ── DCDC_EN (GPIO26): output, drive HIGH immediately ──────────────────
    gpio_export(DCDC_EN_GPIO)
    gpio_direction(DCDC_EN_GPIO, "out")
    gpio_write(DCDC_EN_GPIO, 1)
    log.info("GPIO%d (DCDC_EN) → HIGH: DC-DC converter enabled", DCDC_EN_GPIO)

    # ── SHUTDOWN_REQ (GPIO21): input, falling-edge detection ──────────────
    gpio_export(SHUTDOWN_GPIO)
    gpio_direction(SHUTDOWN_GPIO, "in")
    gpio_edge(SHUTDOWN_GPIO, "falling")
    log.info("GPIO%d (SHUTDOWN_REQ) configured – monitoring for shutdown request", SHUTDOWN_GPIO)

    value_path = f"{SYSFS_GPIO}/gpio{SHUTDOWN_GPIO}/value"
    with open(value_path, "r") as gpio_fd:
        # Drain any stale edge event
        gpio_fd.read()

        poller = select.poll()
        poller.register(gpio_fd.fileno(), select.POLLPRI | select.POLLERR)
        log.info("Monitoring GPIO%d for shutdown request (active-low)…", SHUTDOWN_GPIO)

        while True:
            events = poller.poll(30_000)   # 30 s heartbeat

            if not events:
                log.debug(
                    "Heartbeat – GPIO%d=%d, GPIO%d=%d",
                    SHUTDOWN_GPIO, gpio_read(SHUTDOWN_GPIO),
                    DCDC_EN_GPIO,  gpio_read(DCDC_EN_GPIO),
                )
                continue

            # Re-read after edge interrupt
            gpio_fd.seek(0)
            raw = gpio_fd.read().strip()
            log.info("Edge event on GPIO%d, value=%s", SHUTDOWN_GPIO, raw)

            if raw == "0":
                log.warning(
                    "Shutdown request received from MacArthur HAT (GPIO%d LOW)",
                    SHUTDOWN_GPIO,
                )
                # Keep DCDC_EN HIGH – the system still needs power to shut down
                # cleanly.  SIGTERM from s6 will drive it LOW when done.
                log.info("Initiating graceful system shutdown…")
                subprocess.run(
                    ["/sbin/shutdown", "-h", "now",
                     "MacArthur HAT requested graceful shutdown"],
                    check=False,
                )

                # Safety fallback: if we haven't received SIGTERM within
                # 90 s something went wrong – release power anyway.
                log.info("Waiting for init system to terminate this service…")
                time.sleep(90)
                log.warning("Shutdown timeout – forcing DCDC_EN LOW as safety measure")
                release_dcdc("shutdown timeout (90 s)")
                _cleanup_gpios()
                return


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log.exception("Unexpected error in shutdown monitor")
        release_dcdc("unexpected error – releasing power as safety measure")
        sys.exit(1)
