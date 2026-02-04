# Vivarium Controller (viva-logger)

An early-stage vivarium monitoring and control project built around a **LabJack T-Series DAQ** and a **Raspberry Pi**.

The near-term focus is **reliable data logging** and a clean foundation for future control + visualization (local network dashboard, alerts, etc.). This repository is a work in progress and will evolve as the system matures.

---

## Why This Exists

I like LabJack devices.

They’re capable, reasonably priced (for what they are), and—critically—not painful to learn. The **LJM** library is straightforward and easy to work with, and I’ve used LabJacks in a lab setting at work. That familiarity made them an easy choice for a small, reliable data acquisition system.

This project started when my son asked for a **crested gecko** for his birthday.

Six months later, I’m debugging climate control.

The current setup includes:
- A **DC fan** ducted to the base enclosure for fresh air exchange
- A **MistKing** misting system for humidity control
- A simple temperature + relative humidity sensor array (currently a mix of **I²C devices** and **10k thermistors**)

This isn’t the absolute cheapest way to do things, but it’s robust, understandable, and extensible. While this project is built around a LabJack DAQ, the overall software structure is intentionally generic and could be adapted to other DAQ hardware with minimal changes.

---

## Overview

At a high level, the system consists of:

- **LabJack T-Series DAQ** for sensor inputs (and eventually outputs)
- **Raspberry Pi** running Python for acquisition + logging
- A software structure intended to grow incrementally without becoming a spaghetti monster

Right now, the emphasis is on logging and reliability over UI polish.

---

## Quick Start

### Prerequisites
- Python >=3.11
- A LabJack **T-Series** device (the default script config uses a `T4`)
- LabJack drivers + **LJM** installed (and the device connected)

### Install
This project is configured via `pyproject.toml`.

```bash
git clone <your-repo-url>
cd viva-logger
```

If you use Poetry:

```bash
poetry install
```

### Create the log directory
The logger writes into a local `LOGS/` folder and expects it to already exist.

```bash
mkdir -p LOGS
```

### Run
`viva-logger.py` is runnable as a script via its `__main__` section. It constructs a `logger_config`,
instantiates `Logger(**logger_config)`, and calls `logger.run()`.

```bash
# if using poetry:
poetry run python viva-logger.py

# otherwise:
python viva-logger.py
```

When running, it streams newline-delimited JSON to:

- `LOGS/YYYY-MM-DD.jsonl`

It also rolls over to a new file at local midnight.

Stop with Ctrl+C.

### Configure sensors / read rate
Edit the `logger_config` inside `if __name__ == "__main__":` in `viva-logger.py`.

Current defaults (as shipped):
- device type: `T4`
- sample interval: `htz: 15` (seconds between reads)
- sensors: `t6902` temp/RH sensor, with placeholders for 10k thermistors

---

## Current Features

- Reads sensor data via LabJack T-Series DAQ
- Logs time-series data locally as **JSONL** (one record per line)
- Daily log rotation at local midnight
- Designed for long-running, headless operation on a Raspberry Pi
- Structured to support multiple sensor channels

---

## TODO / Roadmap (Top of Mind)

**Reliability**
- [ ] Add robust reconnect behavior when the LabJack connection drops (USB / wired comms hiccups)
- [ ] Improve error handling + retry/backoff so the logger can run unattended for long periods

**Deployment**
- [ ] Run the logger natively on the Raspberry Pi (headless, as a service)
- [ ] Document SSH-based workflow for managing the Pi
- [ ] Explore secure remote access from outside the local network (VPN / tunnel approach)

**Visibility / UI**
- [ ] Build a local network dashboard (Plotly Dash) to view live status + historical logs
- [ ] Add basic health indicators (DAQ connected, last sample time, sensor sanity checks)

**Sensors / Actuators**
- [ ] Integrate ceramic heater control (and decide on control strategy + safety limits)
- [ ] Finish wiring / cleanup and finalize a “known-good” deployment configuration

**Media / Logging**
- [ ] Add a camera for visual logging (especially nighttime gecko activity)
- [ ] Decide on storage strategy (clips vs timelapse vs motion-triggered)

**Docs / Sharing**
- [ ] Publish a schematic / wiring diagram of the actual vivarium setup
- [ ] Write up the build story: how the vivarium and the control system evolved together

---

## Hardware

- Raspberry Pi (tested on Raspberry Pi OS)
- LabJack T-Series DAQ
- Environmental sensors compatible with LabJack analog inputs
- (Optional / planned) camera + heater integration

---

## Software

- Python >=3.11 (see `pyproject.toml` for exact pins)

Key dependencies (from `pyproject.toml`):

**DAQ / Hardware**
- `labjack-ljm`

**Data + Analysis**
- `pandas`
- `scikit-learn`
- `scipy`
- `shapely`

**Validation / Schemas**
- `jsonschema`
- `pydantic`

**Visualization / Export**
- `kaleido`
- `plotly`
- `xlsxwriter`

**Database / Remote Access**
- `paramiko`
- `psycopg`
- `sshtunnel`

**Other**
- `datetime`

---

## Status

This project is in **active early development**.  
Expect rough edges, evolving structure, and changing assumptions.

---

## License

MIT License
