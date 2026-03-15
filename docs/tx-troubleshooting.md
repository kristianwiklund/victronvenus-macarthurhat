# NMEA 2000 / CAN TX Troubleshooting

## Symptom

High receive error count (REC) on the MCP2518FD after transmission attempts. Other devices on the bus operate normally. The bus is correctly terminated (2× 120 Ω), STBY is tied to GND on the transceiver, and all other CAN nodes remain healthy.

## Hardware Configuration

| Component | Part |
|-----------|------|
| CAN FD controller | MCP2518FD |
| CAN transceiver | MCP2562 (non-FD) |
| Crystal | 20 MHz |
| SPI | spi0.1 (GPIO7 / CE1) |
| Interrupt | GPIO25 |
| TXD/RXD coupling | 0 Ω resistors |
| STBY | GND (normal/active mode) |

## Root Cause: Voltage Mismatch on RXD

The **MCP2562** (non-FD variant) is a 5 V logic device. Its RXD output swings to VDD (5 V) for a recessive bit. The **MCP2518FD** is a 3.3 V device with 3.3 V-rated inputs.

The CAN protocol requires every transmitting node to read back its own transmitted bits via the TXD→bus→RXD loopback path and compare them against what was sent. On every recessive bit looped back:

1. MCP2518FD drives TXD high (recessive)
2. MCP2562 drives bus recessive, RXD output → **5 V**
3. 5 V is applied to the MCP2518FD RXD input, exceeding its 3.3 V rating
4. Input protection diodes clamp and conduct; edge timing is distorted
5. Controller samples an ambiguous or wrong bit value at the sample point
6. Bit Error declared → error frame transmitted → **TEC += 8**
7. All other nodes see the error frame → **REC += 1** per node per error

This manifests as a high REC specifically during and after TX attempts, because the loopback path is only active when the node is transmitting.

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

### Permanent (next board spin)

Replace **MCP2562** with **MCP2562FD**. The MCP2562FD is pin-compatible except for the added VIO pin:

- Connect **VIO to 3.3 V** — this sets RXD output swing to 3.3 V, safe for the MCP2518FD input
- All other pins are identical
- Supports up to 8 Mbps, loop delay symmetry specified, fully characterized for use with CAN FD controllers

### Workaround on Existing Hardware

A resistor voltage divider on the RXD line (MCP2562 output → MCP2518FD input) can reduce the 5 V recessive level to ~3 V. This is fragile and not recommended for production use. The MCP2562FD is the correct fix.

Example divider (approximate, adjust for input impedance of MCP2518FD RXD pin):
- R_top = 220 Ω (between MCP2562 RXD and MCP2518FD RXD)
- R_bot = 470 Ω (between MCP2518FD RXD and GND)
- Result: 5 V × 470/(220+470) ≈ 3.4 V — marginal; verify against MCP2518FD VIH/VIL specs

A better workaround is a dedicated 5 V→3.3 V logic level translator IC on the RXD line only (TXD is driven by the 3.3 V MCP2518FD and will correctly drive the 5 V-referenced MCP2562 TXD input).
