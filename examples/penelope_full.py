"""Fuller Penelope LED and mister configuration.

This example uses the same basic hardware map as ``tests/penelope.py`` but
turns on the runtime features that the minimal script does not express:

* the LED is a scheduled PWM output with a daytime ramp;
* the mister relay is owned by a humidity controller;
* humidity targets change between daytime and nighttime windows;
* safety limits keep misting in bounded pulses.

The duty cycles and humidity targets here are example starting points, not a
claim that these exact values are right for every enclosure. Calibrate the
sensor placement, dimmer response, misting response, and animal-care targets
before leaving real hardware unattended.
"""

from __future__ import annotations

import datetime as dt
import time
from pathlib import Path

from logger.models import (
    DeviceTemperatureConfig,
    HumidityControlConfig,
    HumidityScheduleConfig,
    I2CConfig,
    LabjackConfig,
    LoggerConfig,
    PWMConfig,
    PWMScheduleConfig,
    RelayConfig,
    T9602Config,
)
from logger.runtime import build_runtime


def make_config(log_dir: Path = Path("VIV-LOGS")) -> LoggerConfig:
    """Build the full Penelope logger/control configuration."""

    return LoggerConfig(
        log_dir=log_dir,
        sample_interval_s=5.0,
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
                duty_cycle=0.0,
                enabled=True,
                schedules=[
                    PWMScheduleConfig(
                        name="daylight-ramp",
                        start_time=dt.time(8, 0),
                        end_time=dt.time(20, 0),
                        min_duty_cycle=0.03,
                        max_duty_cycle=0.40,
                        ramp_duration_s=3600.0,
                        outside_duty_cycle=0.0,
                    ),
                ],
            ),
        ],
        humidity_controls=[
            HumidityControlConfig(
                name="penelope-mister",
                rh_sensor="temp-rh",
                relay="misting-pump-eio4",
                enabled=True,
                max_pulse_s=20.0,
                settle_s=60.0*5,
                min_off_s=30.0,
                default_hysteresis_rh_pct=4.0,
                hard_max_rh_pct=85.0,
                max_consecutive_pulses=6,
                low_response_delta_rh_pct=0.5,
                schedules=[
                    HumidityScheduleConfig(
                        name="day-background",
                        start_time=dt.time(6, 0),
                        end_time=dt.time(20, 0),    
                        target_rh_pct=50.0,
                        trigger_below_rh_pct=40.0,
                    ),
                    HumidityScheduleConfig(
                        name="night-humidity",
                        start_time=dt.time(20, 0),
                        end_time=dt.time(6, 0),
                        target_rh_pct=70.0,
                        trigger_below_rh_pct=60.0,
                    ),
                ],
            ),
        ],
    )


def run_forever() -> None:
    """Run the full Penelope configuration until interrupted."""

    system = build_runtime(make_config())

    try:
        system.connect()
        system.initialize()

        while True:
            if system.scheduler.due():
                record, path = system.run_once(write=True)
                print(
                    f"{record.timestamp.isoformat()} "
                    f"errors={len(record.errors)} path={path}"
                )

            time.sleep(0.2)

    finally:
        system.close()


if __name__ == "__main__":
    run_forever()
