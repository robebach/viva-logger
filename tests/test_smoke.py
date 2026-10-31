from pathlib import Path

from logger.hardware import MockLabjackDriver
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


def make_smoke_config() -> LoggerConfig:
    return LoggerConfig(
        log_dir=Path("LOGS_SMOKE"),
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
                name="top-rh",
                i2c=I2CConfig(
                    sda_dio=8,
                    scl_dio=9,
                    address=0x28,
                    rx_len=4,
                ),
            ),
            T9602Config(
                name="low-rh",
                i2c=I2CConfig(
                    sda_dio=10,
                    scl_dio=11,
                    address=0x28,
                    rx_len=4,
                ),
            ),
        ],
        relays=[
            RelayConfig(
                name="mist-a",
                dio=12,
                initial_state=0,
            ),
            RelayConfig(
                name="mist-b",
                dio=13,
                initial_state=0,
            ),
        ],
        pwm_outputs=[
            PWMConfig(
                name="led-dimmer",
                dio=6,
                frequency_hz=1000.0,
                duty_cycle=0.5,
                enabled=True,
            ),
        ],
    )


def main() -> None:
    config = make_smoke_config()

    driver = MockLabjackDriver(config.device)
    system = build_runtime(
        config,
        driver=driver,
    )
    
    try:
        system.connect()
        system.initialize()

        record, path = system.run_once(write=True)

        print("\nSmoke record:")
        print(record.model_dump_json(indent=2))

        print(f"\nWrote JSONL record to: {path}")

        assert record.errors == []
        assert len(record.sensors) == 3
        assert len(record.controls) == 3
        assert path is not None
        assert path.exists()

        print("\nSmoke test passed.")

    finally:
        system.close()


def test_smoke() -> None:
    main()


if __name__ == "__main__":
    main()