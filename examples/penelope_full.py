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

import argparse
import datetime as dt
import time
from pathlib import Path
from typing import Optional

from logger.config_files import load_logger_config, save_logger_config
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
from logger.runtime import SystemRuntime, build_runtime


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "VIV-CONFIG" / "penelope.json"


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
                duty_cycle=0.03,
                enabled=True,
                schedules=[
                    PWMScheduleConfig(
                        name="daylight-ramp",
                        start_time=dt.time(6, 0),
                        end_time=dt.time(20, 0),
                        min_duty_cycle=0.03,
                        max_duty_cycle=1.00,
                        ramp_duration_s=60.0,
                        outside_duty_cycle=0.03,
                        weather_modulation=True,
                        weather_min_peak=0.80,
                        weather_max_peak=1.00,
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
                max_pulse_s=30.0,
                settle_s=300.0,
                min_off_s=30.0,
                default_hysteresis_rh_pct=4.0,
                hard_max_rh_pct=85.0,
                max_consecutive_pulses=6,
                low_response_delta_rh_pct=0.5,
                drying_alarm_rate_rh_pct_per_hour=75.0,
                drying_alarm_below_target_rh_pct=6.0,
                schedules=[
                    HumidityScheduleConfig(
                        name="day-background",
                        start_time=dt.time(5, 0),
                        end_time=dt.time(20, 0),
                        target_rh_pct=52.0,
                        trigger_below_rh_pct=42.0,
                        max_pulse_s=10.0,
                    ),
                    HumidityScheduleConfig(
                        name="night-humidity",
                        start_time=dt.time(20, 0),
                        end_time=dt.time(5, 0),
                        target_rh_pct=72.0,
                        trigger_below_rh_pct=65.0,
                        max_pulse_s=30.0,
                    ),
                ],
            ),
        ],
    )


def load_or_create_config(config_path: Path) -> LoggerConfig:
    """Load Penelope config from JSON, creating it from defaults if missing."""

    if config_path.exists():
        return load_logger_config(config_path)

    config = make_config()
    save_logger_config(config, config_path)
    print(f"Created default config: {config_path}", flush=True)
    return config


def resolve_project_path(path: Path) -> Path:
    """Resolve relative Penelope paths from the project root."""

    return path if path.is_absolute() else PROJECT_ROOT / path


def resolve_runtime_paths(config: LoggerConfig) -> LoggerConfig:
    """Return a runtime config with relative output paths anchored consistently."""

    return config.model_copy(update={"log_dir": resolve_project_path(config.log_dir)})


def connect_and_initialize(
    system: SystemRuntime,
    *,
    retry_s: float,
    startup_pwm_ramp: bool = True,
) -> SystemRuntime:
    """Connect and initialize, retrying transient LabJack startup failures."""

    while True:
        try:
            system.connect()
            system.initialize(startup_pwm_ramp=startup_pwm_ramp)
            return system
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            system.close()
            if retry_s <= 0.0:
                raise

            print(
                f"Connect/init failed: {type(exc).__name__}: {exc}; "
                f"retrying in {retry_s:.1f}s",
                flush=True,
            )
            time.sleep(retry_s)


def run_forever(
    *,
    config_path: Optional[Path] = DEFAULT_CONFIG_PATH,
    hot_reload: bool = True,
    connect_retry_s: float = 5.0,
) -> None:
    """Run the full Penelope configuration until interrupted."""

    if config_path is None:
        config = make_config()
        config_mtime_ns = None
    else:
        config_path = resolve_project_path(config_path)
        config = load_or_create_config(config_path)
        config_mtime_ns = config_path.stat().st_mtime_ns

    config = resolve_runtime_paths(config)
    system = build_runtime(config)

    try:
        system = connect_and_initialize(
            system,
            retry_s=connect_retry_s,
            startup_pwm_ramp=True,
        )

        consecutive_dev_errors = 0
        while True:
            if (
                hot_reload
                and config_path is not None
                and config_path.exists()
            ):
                next_mtime_ns = config_path.stat().st_mtime_ns
                if next_mtime_ns != config_mtime_ns:
                    try:
                        next_config = load_logger_config(config_path)
                    except Exception as exc:
                        print(
                            f"Config reload failed: {type(exc).__name__}: {exc}",
                            flush=True,
                        )
                        config_mtime_ns = next_mtime_ns
                    else:
                        print(f"Reloading config: {config_path}", flush=True)
                        next_config = resolve_runtime_paths(next_config)
                        next_system = build_runtime(
                            next_config,
                            driver=system.driver,
                        )
                        next_system.initialize(startup_pwm_ramp=False)
                        system = next_system
                        config = next_config
                        config_mtime_ns = next_mtime_ns

            if system.scheduler.due():
                try:
                    record, path = system.run_once(write=True)
                    print(
                        f"{record.timestamp.isoformat()} "
                        f"errors={len(record.errors)} path={path}"
                    )
                    # Detect if communication with the device was completely severed
                    if record.errors and any("LJME" in err or "Labjack" in err or "device_temperature" in err for err in record.errors):
                        consecutive_dev_errors += 1
                        if consecutive_dev_errors >= 3:
                            print(
                                f"Multiple device read errors detected ({consecutive_dev_errors}); attempting reconnect...",
                                flush=True,
                            )
                            system.close()
                            system = connect_and_initialize(
                                system,
                                retry_s=connect_retry_s,
                                startup_pwm_ramp=False,
                            )
                            consecutive_dev_errors = 0
                    else:
                        consecutive_dev_errors = 0
                except KeyboardInterrupt:
                    raise
                except Exception as exc:
                    print(
                        f"Sample cycle failed with exception: {type(exc).__name__}: {exc}; "
                        f"attempting reconnect...",
                        flush=True,
                    )
                    system.close()
                    time.sleep(connect_retry_s)
                    system = connect_and_initialize(
                        system,
                        retry_s=connect_retry_s,
                        startup_pwm_ramp=False,
                    )
                    consecutive_dev_errors = 0

            time.sleep(0.2)

    finally:
        system.close()


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    """Parse command-line options for the Penelope runtime."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="JSON config file to load, create, and hot-reload.",
    )
    parser.add_argument(
        "--no-config-file",
        action="store_true",
        help="Run from the Python defaults instead of a JSON config file.",
    )
    parser.add_argument(
        "--no-hot-reload",
        action="store_true",
        help="Do not reload the JSON config while running.",
    )
    parser.add_argument(
        "--connect-retry-s",
        type=float,
        default=5.0,
        help="Seconds to wait before retrying a failed LabJack connect/init.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> None:
    """Run Penelope from command-line arguments."""

    args = parse_args(argv)
    run_forever(
        config_path=None if args.no_config_file else args.config,
        hot_reload=args.no_hot_reload,
        connect_retry_s=args.connect_retry_s,
    )


if __name__ == "__main__":
    main()
