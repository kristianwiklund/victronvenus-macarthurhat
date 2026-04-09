# Community Reports — MacArthur HAT NMEA 2000

Community-reported working configurations and known issues with the MacArthur HAT on OpenPlotter and VenusOS.
Sources are OpenMarine forum threads unless noted.

---

## Confirmed Working Configurations

### RX-only: AIS over NMEA 2000 → Signal-K → chart plotters

**Hardware:** Raspberry Pi 5, MacArthur HAT, OpenPlotter 4, EM Trak 953 AIS transponder on the N2K bus.

**Result:** AIS targets confirmed visible in OpenCPN, AvNav, and iSailor.

**Required beyond basic CAN setup:**

- CAN-H and CAN-L wired correctly (easy to swap — go by label, not wire colour).
- Two Signal-K plugins from the appstore:
  - `signalk-n2kais-to-nmea0183` — converts N2K AIS PGNs to NMEA 0183
  - An AIS forwarding plugin to push data to connected plotters.

**Note:** The HAT's AIS/GPS LEDs indicate only a Maiana module, not NMEA 2000 bus activity.

Source: https://forum.openmarine.net/showthread.php?tid=6351

---

### TX: Engine data and fuel manager → Raymarine Axiom via Signal-K

**Hardware:** Raspberry Pi 5, MacArthur HAT, OpenPlotter 4, NVMe SSD, Raymarine Axiom MFD, ESP32 running SensESPv3.

**Data flow:**

```
ESP32 sensors → WiFi/WebSocket → Signal-K server
                                       ↓
                          signalk-to-nmea2000 plugin
                                       ↓
                          MacArthur HAT CAN (TX)
                                       ↓
                          Raymarine Axiom (NMEA 2000)
```

Engine sensor data (RPM, temperatures, fuel flow rate) is collected by an ESP32
running SensESPv3 and published into Signal-K over WiFi — **not** via NMEA 2000.
Signal-K then retransmits it onto the NMEA 2000 bus using the
`signalk-to-nmea2000` plugin, through the MacArthur HAT. The Axiom reads PGNs
including battery data (127508), charge status (127506), engine parameters, and
fuel flow.

**What this scenario does and does not confirm:**

- Confirms the `signalk-to-nmea2000` + MacArthur HAT TX path can produce frames
  readable by a Raymarine Axiom in at least some conditions.
- Does **not** confirm correct CAN-level operation: no `candump` output, TEC/REC
  counter checks, or bus error logs were published. The Axiom may tolerate
  frames with elevated error counts that would be rejected by stricter nodes.
- The data source is WiFi, not a CAN RX node — so the RXD loopback path is only
  exercised during ACK transmission of received frames, not during data TX.

**Why this TX may work when others report TX failure:**

The v1.2 schematic includes a level-shifting circuit (U9/74LVC1G125 +
U10/74AHCT1G125) specifically designed to bridge the 5 V MCP2562 and the
3.3 V MCP2518FD. Two signal paths exist, selected by 0 Ω resistors:

- **Direct path** (R29/R30/R31 populated, U9/U10 bypassed): 5 V RXD hits the
  MCP2518FD RXCAN directly → loopback errors → TX fails.
- **Level-shifted path** (R33/R35 populated, U9/U10 fitted): signals are
  translated to the correct voltage before reaching each IC → TX should work.

The Baileys board may have the level-shifted path correctly populated. Boards
where TX fails likely have the direct path active. Neither state has been
independently verified against a physical board. See `docs/tx-troubleshooting.md`
for the full analysis and physical verification steps.

**Plugins required:**

- `signalk-to-nmea2000` with `@canboat/canboatjs` installed (see Issue 7)
- SensESPv3 firmware on the ESP32 engine monitor

Sources: https://boatingwiththebaileys.com/openplotter/,
https://signalk.org/2023/battery-engine-fuel-flow/,
https://github.com/Boatingwiththebaileys/SensESPv3_Engine_code

---

### Supported platforms

| Platform | Status |
|----------|--------|
| Raspberry Pi 5 | Confirmed working |
| Raspberry Pi 4 | Confirmed working |
| Raspberry Pi 3 | **Not supported** — openplotter-can tabs absent; MCP251xfd oscillator field not present in OpenPlotter 3. Workaround: use Pi 5 as primary, connect Pi 3 to its Wi-Fi stream. |

Requires `openplotter-can` ≥ v4.0.4 and OpenPlotter 4.

---

### RX vs TX

All confirmed RX reports involve the HAT passively reading from the NMEA 2000
bus. The one TX report (Boating With The Baileys) has not been verified at the
CAN layer (no candump, TEC/REC counter logs).

Whether TX works on a given board depends on which signal path the 0 Ω
resistors select: the direct path (R29/R30/R31) causes voltage mismatch and TX
failure; the level-shifted path (R33/R35 with U9/U10 fitted) should work. The
schematic designs for the latter, but assembly may vary. See
`docs/tx-troubleshooting.md` for verification steps.

Software-level TX failures unrelated to hardware are also common — see Issues
3, 7, and 8.

---

## Known Issues

---

## Issue 1 — GPIO numbering confusion

**Symptom:** GPIO 25 interrupt option is greyed out in the OpenPlotter CAN app;
`candump` shows no `can0` device; NMEA 2000 LED shows unconfigured state.

**Cause:** Users confuse BCM GPIO numbering with physical header pin numbering.
GPIO 25 is physical pin 22, not physical pin 25.

**Fix:** In the OpenPlotter CAN app, always use BCM numbering. Select
**GPIO25** (which maps to physical pin 22).

See the GPIO pin reference in `docs/user-guide.md` for the full mapping.

Source: https://forum.openmarine.net/showthread.php?tid=6053

---

## Issue 2 — MCP251xfd device disappears after reboot on Raspberry Pi OS Bookworm

**Symptom:** The device is added successfully but disappears after reboot.
`candump` reports:

```
SIOCGIFINDEX: No such device
```

No CAN LEDs flicker. Re-adding the device in the CAN app fails with:

```
GPIO25 (pin 22) is in use by CAN MCP251xfd
```

**Cause:** Raspberry Pi OS Bookworm moved `config.txt` to a new location.
The `openplotter-can` script continued reading the old path, so the overlay
was not loaded at boot.

**Fix:** Update to `openplotter-can` v4.0.4 or later. Remove any manual edits
to `config.txt` and reconfigure entirely via the OpenPlotter CAN app. Do not
mix manual `config.txt` edits with app-managed configuration.

Source: https://forum.openmarine.net/showthread.php?tid=5187

---

## Issue 3 — socketcan native module missing or broken after Signal-K update

**Symptom:** No data in Signal-K despite correct hardware wiring and confirmed
termination. One of two errors appears in Signal-K server logs:

```
unable to load native socketcan interface
```
```
socketcan/build/Release/can.node: file too short
```

The second form ("file too short") means the binary was written but truncated
during a failed build — it is present on disk but unusable.

**Cause:** `socketcan` is an optional dependency of the Signal-K server.
During package installation or upgrade, if the Node.js version is below
v18.17.0 or v20.5.0, the native build fails silently and socketcan is skipped
with no error at install time. The failure only surfaces at runtime.

Affected Signal-K versions: 2.13.0 and later, including 2.18.0.

**Fix:**

Option 1 — rebuild socketcan in place:

```bash
cd /usr/lib/node_modules/signalk-server/
sudo npm install socketcan
sudo systemctl restart signalk.service
```

Option 2 — reinstall the server with build output visible (surfaces errors):

```bash
sudo npm install -g signalk-server --foreground-scripts
```

> **Note:** This is a software packaging issue distinct from the hardware TX
> bug described in `docs/tx-troubleshooting.md`. That document covers a
> hardware-level MCP2562/MCP2518FD voltage mismatch.

Sources: https://forum.openmarine.net/showthread.php?tid=6550,
https://forum.openmarine.net/showthread.php?tid=6581,
https://github.com/SignalK/signalk-server/issues/1870

---

## Issue 7 — TX silently broken: missing `@canboat/canboatjs`

**Symptom:** Signal-K logs show PGNs being generated by the
`signalk-to-nmea2000` plugin, but `candump can0` shows nothing transmitted on
the bus. `ifconfig can0` shows RX packets incrementing but TX packets remain
at zero.

**Cause:** `@canboat/canboatjs` is not installed. Without it the server falls
back silently to the legacy `canboat` interface, which cannot transmit on
native socketCAN interfaces. No error is emitted at startup or during
operation.

**Fix:**

```bash
cd /usr/lib/node_modules/signalk-server/
npm install @canboat/canboatjs
sudo systemctl restart signalk.service
```

Source: https://github.com/SignalK/signalk-server/issues/1726

---

## Issue 4 — CAN0 config conflict from prior PiCAN-M installation

**Symptom:** HAT power-cycles repeatedly; NMEA 2000 network not recognised
(LEDs steady, no comms). A separate Yacht Devices USB CAN adapter on the same
network works correctly, confirming the bus itself is healthy.

**Cause:** Leftover `can0` configuration from a previously installed PiCAN-M
card conflicted with the MacArthur HAT overlay.

**Fix:** Full OS rebuild. Remove all prior CAN-related `config.txt` entries
and `dtoverlay` lines before configuring the MacArthur HAT.

Source: https://forum.openmarine.net/showthread.php?tid=5536

---

## Issue 5 — Non-isolated NMEA 2000 circuit requires shared ground

**Symptom:** Silent or intermittent CAN bus failures with no obvious error
messages or error counter activity.

**Cause:** The HAT's NMEA 2000 circuit is **not galvanically isolated**. If
the NMEA 2000 network and the Raspberry Pi are powered from different batteries
or supplies, a ground potential difference exists between them. This causes
unreliable communication and can damage the transceiver.

**Fix:** Power both the NMEA 2000 network and the Raspberry Pi from the same
battery or power supply.

Source: https://macarthur-hat-documentation.readthedocs.io/en/latest/nmea2000.html

---

## Issue 6 — J1939 engines not supported natively

**Symptom:** No engine data from pre-2007 Volvo Penta engines (and other older
engines) that use J1939 rather than NMEA 2000.

**Cause:** The HAT implements NMEA 2000 (ISO 11783 / SAE J1939-derived). It
does not perform raw J1939 protocol translation. Older Volvo Penta and similar
engines speak J1939 directly and are not compatible without an intermediary.

**Fix (community workarounds):**

- Use an ESP32-based DIY J1939→NMEA 2000 bridge; see open-boat-projects.org
  for schematics and firmware.
- Use a Python decoder that reads raw J1939 frames and feeds data into
  Signal-K directly.

Source: https://forum.openmarine.net/showthread.php?tid=5184

---

## Issue 8 — MCP2518FD IRQ handler errors under high bus load on Pi 5

**Symptom:** TX overruns at high bus utilisation (≥ ~40%). Kernel log shows:

```
IRQ handler mcp251xfd_handle_tefif() returned -22 (intf=0x...)
```

CAN communication becomes unreliable or stops under sustained load.

**Cause:** A kernel bug in the `mcp251xfd` driver's TEF (Transmit Event FIFO)
IRQ handler. Confirmed on Linux 6.6.74+rpt-rpi-2712 and 6.13.0-v8-16k+.
Affects any hardware using the MCP2518FD, including the MacArthur HAT. Not
reproducible at low bus utilisation.

**Status:** No upstream fix as of early 2026. Not an OpenPlotter or Signal-K
issue — the bug is in the Raspberry Pi kernel.

**Workaround:** Keep bus utilisation low. Avoid polling at high rates or
connecting many high-frequency NMEA 2000 sources simultaneously. Monitor
`ip -s link show can0` for TX error and drop counts.

Source: https://github.com/raspberrypi/linux/issues/6644
