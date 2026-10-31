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


config = LoggerConfig(
    log_dir=Path("LOGS"),
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
                sda_dio=8,   # EIO0
                scl_dio=9,   # EIO1
                address=0x28,
                rx_len=4,
            ),
        ),
    ],
    relays=[
        RelayConfig(
            name="misting-pump-eio4",
            dio=12,          # EIO4
            initial_state=1,
        )
    ],
    pwm_outputs=[
        PWMConfig(
            name="led-0-10v",
            dio=6,           # FIO7
            frequency_hz=1000.0,
            duty_cycle=0.3,
            enabled=True,
        ),
    ],
)


def main() -> None:
   
    system = build_runtime(config)
    
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