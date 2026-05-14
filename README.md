# Viva Logger

Python logging and control code for a LabJack-based vivarium controller.

The current package reads environmental sensors, writes JSONL records, and can
drive simple controls such as active-low relays, PWM lighting, and pulsed
humidity control. The real hardware layer is intentionally small so the runtime
can also be exercised with a mock LabJack driver.

## Current Shape

- `logger.models`: Pydantic configuration and log-record schemas.
- `logger.hardware`: real and mock LabJack drivers.
- `logger.runtime`: sensor decoding, control state machines, scheduling,
  logging, and runtime assembly.
- `examples`: runnable configuration examples.
- `tests/penelope.py`: an early Penelope one-shot script. It is useful as a
  hardware sketch, but it does not express the full feature set.

## Installation

This project expects Python 3.11 or newer.

```bash
poetry install
```

For real hardware runs, install the LabJack LJM driver stack for the machine
running the logger and connect the LabJack before starting a runtime.

## Penelope Examples

The Penelope examples use the wiring assumptions from `tests/penelope.py`:

- LabJack T4.
- T9602 temperature/RH sensor on SDA DIO8, SCL DIO9, address `0x28`.
- MistKing/mister relay on DIO12. Relay logic is active-low: `0` is on, `1` is
  off.
- LED dimmer PWM on DIO6 at 1000 Hz.

The examples preserve the script's `dio=6` LED setting. If the physical LED
wire is on a different LabJack terminal, update the DIO number before running
the full control example.

`examples/penelope_minimal.py` mirrors the older one-shot script. It logs the
LabJack temperature, T9602 temperature/RH, relay state, and manual LED duty
cycle. It does not schedule the LED or run humidity control.

```bash
poetry run python -m examples.penelope_minimal
```

`examples/penelope_full.py` is the fuller Penelope configuration:

- LED PWM runs on a daily window from 08:00 to 20:00.
- LED duty cycle ramps from `0.03` to `0.30` over one hour.
- LED duty cycle returns to `0.0` outside the daylight window.
- The mister relay is controlled by `HumidityControlRuntime`.
- Day and night humidity targets use separate schedules.
- Misting happens in bounded pulses with settle time, minimum off time, a hard
  RH guard, and a consecutive-pulse limit.

Run it only after checking wiring and setpoints:

```bash
poetry run python -m examples.penelope_full
```

The setpoints in the example are starting values for software configuration,
not a universal animal-care prescription. Calibrate the sensor location, LED
dimmer response, misting response, and actual husbandry targets before leaving
hardware unattended.

## Configuration Model

A complete runtime starts with `LoggerConfig`:

```python
from logger.runtime import build_runtime
from examples.penelope_full import make_config

config = make_config()
system = build_runtime(config)

system.connect()
system.initialize()
record, path = system.run_once(write=True)
system.close()
```

The main config sections are:

- `device`: LabJack connection settings.
- `sensors`: sensor definitions such as LabJack device temperature, T9602, or
  thermistor inputs.
- `relays`: digital outputs, currently modeled for active-low relay boards.
- `pwm_outputs`: PWM outputs with optional daily schedules and ramping.
- `humidity_controls`: pulse-based RH controllers that read one RH sensor and
  drive one relay.

## Runtime Behavior

Each sample pass:

1. Applies scheduled PWM state.
2. Reads sensors.
3. Updates humidity controllers from the latest RH readings.
4. Captures relay, PWM, and humidity-control snapshots.
5. Writes one JSON object line to `LOG_DIR/YYYY-MM-DD.jsonl`.

Errors from individual sensors or controls are captured in the record's
`errors` list so one failed read does not necessarily stop the whole sample.

## Dashboard

The package includes a local Dash app for watching a JSONL log file while the
logger writes to it.

```bash
poetry run python -m logger.dashboard --log VIV-LOGS/2026-05-13.jsonl
```

Then open:

```text
http://127.0.0.1:8050
```

The dashboard has a readout tab for the newest record and a trends tab for
sensor values, relay states, PWM state, humidity-controller state, and errors.
It refreshes every 5 seconds by default.

On a Raspberry Pi, bind to all interfaces if you want to view it from another
computer on the same network:

```bash
poetry run python -m logger.dashboard --host 0.0.0.0 --log VIV-LOGS/2026-05-13.jsonl
```

Then browse to `http://penelope-pi.local:8050` or the Pi's IP address.

## Development Checks

Basic syntax check:

```bash
python -m compileall logger examples
```

The mock driver in `logger.hardware.MockLabjackDriver` can be used for local
experiments that should not touch real hardware.

## Status

This is still active, practical workbench code. Expect the configuration shape
and runtime details to keep evolving as the vivarium hardware becomes more
settled.

## License

MIT License
