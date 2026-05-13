"""Minimal Penelope hardware example.

This mirrors the basic shape of ``tests/penelope.py``: one LabJack device
temperature reading, one T9602 temperature/RH sensor, one active-low mister
relay, and one manual PWM LED output.

It is intentionally not the full feature set. It does not include PWM
schedules or humidity-control schedules; see ``examples.penelope_full`` for the
fuller configuration.
"""

from __future__ import annotations

from pathlib import Path

from logger.models import (
    DeviceTemperatureConfig,
    I2CConfig,
    LabjackConfig,
    LoggerConfig,
    PWMConfig,
    RelayConfig,
    T9602Config,
)
from logger.runtime import build_runtime


def make_config(log_dir: Path = Path("LOGS")) -> LoggerConfig:
    """Build the minimal Penelope logger configuration."""

    return LoggerConfig(
        log_dir=log_dir,
        sample_interval_s=1.0,
        device=LabjackConfig(
            device_type="T4",
            serial_number="ANY",
            connection_type="ANY",
        ),
        sensors=[
            DeviceTemperatureConfig(
                name="labjack",
            ),
            T9602Config(
                name="temp-rh",
                i2c=I2CConfig(
                    sda_dio=8,  # EIO0
                    scl_dio=9,  # EIO1
                    address=0x28,
                    rx_len=4,
                ),
            ),
        ],
        relays=[
            RelayConfig(
                name="misting-pump-eio4",
                dio=12,  # EIO4
                initial_state=1,
            ),
        ],
        pwm_outputs=[
            PWMConfig(
                name="led-0-10v",
                dio=6,  # Preserves the DIO number from tests/penelope.py.
                frequency_hz=1000.0,
                duty_cycle=0.30,
                enabled=True,
            ),
        ],
    )


def main() -> None:
    """Connect to hardware, collect one record, and write it to JSONL."""

    system = build_runtime(make_config())

    try:
        system.connect()
        system.initialize()

        record, path = system.run_once(write=True)
        print(record.model_dump_json(indent=2))
        print(f"\nWrote JSONL record to: {path}")

    finally:
        system.close()


if __name__ == "__main__":
    main()
