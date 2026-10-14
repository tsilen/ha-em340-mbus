# EM340 M-Bus for Home Assistant

A Home Assistant app repository containing **EM340 M-Bus**: a read-only reader for Carlo Gavazzi EM330/EM340 energy meters over wired M-Bus. It polls the meter through a USB M-Bus master and publishes energy, power, voltage, current, power factor and frequency to MQTT using Home Assistant MQTT discovery.

**Read-only:** the app only sends the M-Bus `SND_NKE` and `REQ_UD2` short frames, enforced by a write guard. It never changes meter configuration, address, baud rate or record selection.

## Requirements

- Home Assistant OS / Supervised with the **Mosquitto broker** app installed and running, and the MQTT integration set up
- A USB M-Bus master (e.g. a USB-to-M-Bus adapter) connected to the meter's M-Bus terminals

## Installation

1. In Home Assistant go to **Settings → Apps → App store**, open the ⋮ menu → **Repositories**.
2. Add `https://github.com/tsilen/ha-em340-mbus`.
3. Install **EM340 M-Bus**, select your serial port in the configuration, enable **Start on boot** and **Watchdog**, then start it.

See [em340_mbus/DOCS.md](em340_mbus/DOCS.md) for options, entities and troubleshooting.

## Credits

Record map based on the Carlo Gavazzi *EM330/EM340 M-Bus communication protocol* document.

## License

MIT
