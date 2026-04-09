# NMEA 2000 / CAN TX Troubleshooting

## Symptom

High receive error count (REC) on the MCP2518FD after transmission attempts. Other devices on the bus operate normally. The bus is correctly terminated (2× 120 Ω), STBY is tied to GND on the transceiver, and all other CAN nodes remain healthy.

> **Note:** The analysis below is based on the v1.2 schematic (2024-02-10)
> from [OpenMarine/MacArthur-HAT](https://github.com/OpenMarine/MacArthur-HAT).
> The schematic includes a level-shifting circuit designed to handle the
> 5 V / 3.3 V mismatch described here.  Whether that circuit is correctly
> populated on a given physical board is the key variable — see
> [Level-shifting circuit](#level-shifting-circuit-v12-schematic) below.

## Hardware Configuration

| Component | Part |
|-----------|------|
| CAN FD controller | MCP2518FD (per schematic) |
| CAN transceiver | MCP2562 (non-FD, per schematic) |
| Crystal | 20 MHz |
| SPI | spi0.1 (GPIO7 / CE1) |
| Interrupt | GPIO25 |
| TXD/RXD coupling | 0 Ω resistors |
| STBY | GND (normal/active mode) |

## Level-shifting circuit (v1.2 schematic)

The v1.2 schematic shows that the design **does** account for the 5 V / 3.3 V
mismatch between the MCP2562 (VDD = 5 V) and the MCP2518FD (VDD = 3.3 V).
Two buffer ICs bridge the logic levels:

| IC | Part | Power | Direction | Net names |
|----|------|-------|-----------|-----------|
| U9 | 74LVC1G125 | +3V3 | 5 V → 3.3 V | `CAN_RX` → `CAN_RX_3V3` |
| U10 | 74AHCT1G125 | +5V | 3.3 V → 5 V | `CAN_TX_3V3` → `CAN_TX` |

The MCP2518FD is wired exclusively to the `_3V3` nets; the MCP2562 is wired
exclusively to the 5 V nets.  The buffers are the bridge.

### 0 Ω resistor configuration

Two signal paths exist and are selected by which 0 Ω resistors are populated:

| Resistors | Path | Use when |
|-----------|------|----------|
| R29, R30, R31 (+ C3) | **Direct** — `CAN_TX_3V3` tied to `CAN_TX`, `CAN_RX` tied to `CAN_RX_3V3` with no buffering | CAN driver has 3.3 V I/O (e.g. TJA1462A) |
| R33 + R35 | **Level-shifted** — enables U9 (OE active-low) and U10 for RX and TX respectively | **CAN driver has 5 V I/O (MCP2562)** |

Schematic note (verbatim):
> *Level-shifting for NMEA2000: Populate only when using CAN drivers with 5 V I/O*
> *Do not populate R29, R30, R31, C3 for 5 V CAN driver*
> *Populate R33, R35 when using 74xxx1G125 (OE low)*

**If a board ships with R29/R30/R31 populated (direct path) and U9/U10 not
fitted, the level-shifting circuit is bypassed entirely.** This is the
assembly state that produces the failure described below.

### MCP2562 Vio pin (pin 5)

The MCP2562 has a VIO logic-supply reference pin.  If VIO is tied to +3V3,
the RXD output swings to 3.3 V rather than 5 V, and TXD input thresholds
become 3.3 V-compatible — which alone would eliminate the mismatch without
external buffers.  The schematic does not show VIO explicitly tied to +3V3;
the design relies on U9/U10 for level shifting instead.

**Physical board verification** — measure U2 pin 5 to GND with a multimeter:
- 3.3 V → VIO is tied to +3V3; RXD is 3.3 V-compatible; TX may work without U9/U10.
- 5 V or floating → VIO is at VDD or undriven; the board depends on U9/U10.

---

## Root Cause: Voltage Mismatch on RXD (when level-shifting is absent)

When the direct path is active (R29/R30/R31 populated, U9/U10 absent or
disabled) and VIO is not at 3.3 V:

The **MCP2562** RXD output swings to VDD (5 V) for a recessive bit. The
**MCP2518FD** RXCAN input is rated for 3.3 V.

The CAN protocol requires every transmitting node to read back its own
transmitted bits via the TXD→bus→RXD loopback path. On every recessive bit
looped back:

1. MCP2518FD drives TXD high (recessive)
2. MCP2562 drives bus recessive, RXD output → **5 V**
3. 5 V is applied to the MCP2518FD RXCAN input, exceeding its 3.3 V rating
4. Input protection diodes clamp and conduct; edge timing is distorted
5. Controller samples an ambiguous or wrong bit value at the sample point
6. Bit Error declared → error frame transmitted → **TEC += 8**
7. All other nodes see the error frame → **REC += 1** per node per error

This manifests as a high REC specifically during and after TX attempts, because
the loopback path is only active when the node is transmitting.

## Secondary Factor: Non-FD Transceiver with FD Controller

The MCP2562 is rated to 1 Mbps (classic CAN only) and has an asymmetric propagation delay:

| Transition | Delay |
|------------|-------|
| TXD → RXD dominant (TXD low) | ~125 ns |
| TXD → RXD recessive (TXD high) | ~235 ns |

The ~110 ns asymmetry is uncharacterized against the MCP2518FD's internal timing. The MCP2562FD (CAN FD-rated) specifies loop delay symmetry within ±10 % at 2 Mbps and has a maximum propagation delay of 120 ns — both required for reliable operation with a CAN FD controller.

If the driver configures any CAN FD data phase, Transmitter Delay Compensation (TDC) will be miscalibrated against the MCP2562's unspecified loop delay, causing bit errors in the data phase on every frame.

## Non-Factors

- **STBY = GND** — correct for normal operation; not a cause.
- **0 Ω resistors on TXD/RXD** — fine for co-located chips on a short PCB trace; not a cause. Series resistors (33–47 Ω) are good EMC practice but do not fix the root cause.
- **Bus termination** — confirmed correct; not a cause.
- **Bit timing at 250 kbps / 20 MHz** — TSEG1 is large enough at this speed to absorb the transceiver loop delay; not a cause.

## Kernel / Driver Configuration

`/boot/config.txt`:
```
dtoverlay=mcp251xfd,spi0-1,oscillator=20000000,interrupt=25
```

This is correct for the hardware. The `mcp251xfd` driver exposes a CAN FD-capable interface but will run classic CAN if brought up without `fd on`.

NMEA 2000 is classic CAN 2.0 at 250 kbps. Bring up the interface as:
```bash
ip link set can0 up type can bitrate 250000
```

Do **not** use `dbitrate` or `fd on` with the MCP2562 transceiver.

Verify the interface is not in FD mode:
```bash
ip link show can0
# Should not contain "fd" in flags
```

## Fix

### Step 0 — Check what is actually on your board

Before reworking anything, determine which signal path is active:

1. Check whether U9 (74LVC1G125) and U10 (74AHCT1G125) are fitted.
2. Check whether R29, R30, R31 are populated (0 Ω links).
3. Measure voltage on U2 pin 5 (MCP2562 VIO) relative to GND.

If U9/U10 are fitted and R29/R30/R31 are absent, the board is assembled
correctly per the schematic and TX should work.  Investigate software
configuration (bitrate, FD mode, socketcan) before assuming a hardware fault.

If R29/R30/R31 are populated and U9/U10 are absent or have their OE pins
floating high (disabled), the direct path is active and the voltage mismatch
applies.  Proceed to the fixes below.

### Option A — Enable the on-board level-shifting circuit

If U9 and U10 are fitted but not enabled:

1. Populate R33 and R35 with 0 Ω links to pull U9's OE pin low (74LVC1G125,
   OE active-low).
2. Remove R29, R30, R31 to break the direct path.
3. Verify: `ip -s link show can0` should show zero or near-zero error counts
   after a TX burst.

### Option B (next board spin) — Replace MCP2562 with MCP2562FD

Replace **MCP2562** with **MCP2562FD**. The MCP2562FD is pin-compatible except for the added VIO pin:

- Connect **VIO to 3.3 V** — this sets RXD output swing to 3.3 V, safe for the MCP2518FD input
- All other pins are identical
- Supports up to 8 Mbps, loop delay symmetry specified, fully characterized for use with CAN FD controllers

### Option C — RXD voltage divider (workaround on existing hardware)

A resistor voltage divider on the RXD line (MCP2562 output → MCP2518FD input) can reduce the 5 V recessive level to ~3 V. This is fragile and not recommended for production use. The MCP2562FD is the correct fix.

Example divider (approximate, adjust for input impedance of MCP2518FD RXD pin):
- R_top = 220 Ω (between MCP2562 RXD and MCP2518FD RXD)
- R_bot = 470 Ω (between MCP2518FD RXD and GND)
- Result: 5 V × 470/(220+470) ≈ 3.4 V — marginal; verify against MCP2518FD VIH/VIL specs

A better workaround is a dedicated 5 V→3.3 V logic level translator IC on the RXD line only (TXD is driven by the 3.3 V MCP2518FD and will correctly drive the 5 V-referenced MCP2562 TXD input).
