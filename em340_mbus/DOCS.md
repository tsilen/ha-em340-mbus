# EM340 M-Bus

Reads a Carlo Gavazzi EM330/EM340 energy meter over wired M-Bus (USB M-Bus master)
and publishes the values to Home Assistant via MQTT discovery.

**Read-only:** the app only sends `SND_NKE` and `REQ_UD2` short frames; a write guard
refuses anything else. It never changes meter configuration, address, baud rate or record selection.

## Requirements
- Mosquitto broker app and the MQTT integration.
- A USB M-Bus master connected to the meter's M-Bus terminals.

## Options
| Option | Default | |
|---|---|---|
| `serial_port` | | M-Bus master serial device |
| `baud_rate` | 2400 | Meter M-Bus baud rate (300/2400/9600) |
| `address` | 1 | Meter primary address |
| `poll_interval` | 10 | Seconds between reads |

Values are published only when they change (retained).

## Entities
Enabled by default: import/export energy (kWh, usable in the Energy dashboard), total and
per-phase active power, power factor, phase voltages, phase currents, frequency.
Disabled by default (enable in the entity settings): per-phase and tariff energies, partial
energy, reactive energies, reactive/apparent power, per-phase PF, L-L voltages, system
voltages, demand values.

## Troubleshooting
- No values: check the serial port, the meter's M-Bus primary address and baud rate
  (meter defaults: address 1, 2400 baud), and the M-Bus wiring. The app log shows read errors.
- The decoder can be run outside Home Assistant for diagnostics:
  `python3 em340_mbus.py --port <serial device> --dry-run --raw` prints raw frames and decoded values
  without publishing to MQTT. Requires `pyserial`.
- Tests: `python3 em340_test.py` (offline, no hardware needed).
