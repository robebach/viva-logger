import datetime as dt
from pathlib import Path
import tempfile

from logger.hardware import MockLabjackDriver
from logger.models import (
    DeviceTemperatureConfig,
    HumidityControlConfig,
    HumidityScheduleConfig,
    I2CConfig,
    LabjackConfig,
    LoggerConfig,
    RelayConfig,
    T9602Config,
)
from logger.runtime import build_runtime


def test_humidity_state_persistence_and_grace_period():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        log_dir = tmp_path / "LOGS"
        state_file = tmp_path / "state.json"

        config = LoggerConfig(
            log_dir=log_dir,
            state_path=state_file,
            sample_interval_s=1.0,
            device=LabjackConfig(device_type="T4"),
            sensors=[
                T9602Config(
                    name="temp-rh",
                    i2c=I2CConfig(sda_dio=8, scl_dio=9, address=0x28),
                ),
            ],
            relays=[
                RelayConfig(name="mister", dio=12, initial_state=1),
            ],
            humidity_controls=[
                HumidityControlConfig(
                    name="penelope-mister",
                    rh_sensor="temp-rh",
                    relay="mister",
                    enabled=True,
                    max_pulse_s=10.0,
                    settle_s=60.0,
                    min_off_s=30.0,
                    startup_grace_period_s=120.0,
                    schedules=[
                        HumidityScheduleConfig(
                            name="all-day",
                            start_time=dt.time(0, 0),
                            end_time=dt.time(23, 59, 59),
                            target_rh_pct=60.0,
                            trigger_below_rh_pct=50.0,
                        ),
                    ],
                ),
            ],
        )

        driver = MockLabjackDriver(config.device)
        driver.mock_rh_fraction = 0.40  # 40% RH (below 50% trigger)

        system = build_runtime(config, driver=driver)
        now = dt.datetime(2026, 10, 31, 12, 0, 0, tzinfo=dt.timezone.utc)

        system.connect()
        system.initialize(now=now)

        # 1. On initial boot, relay is 1 (off)
        assert driver.relay_states.get(12) == 1

        # 2. First tick: RH is 40% (below trigger 50%), but startup grace period is 120s!
        # It must NOT pulse!
        record, _ = system.run_once(write=True, now=now)
        assert driver.relay_states.get(12) == 1
        ctrl_snap = record.controls[1]
        assert ctrl_snap.values["mode"] == "startup_grace_period"
        assert state_file.exists()

        # 3. Advance time by 60s (still within 120s grace period)
        now_60s = now + dt.timedelta(seconds=60)
        system.controls.update_humidity_controls(driver, record.sensors, now=now_60s)
        assert driver.relay_states.get(12) == 1
        assert system.controls.humidity_controls[0].status["mode"] == "startup_grace_period"

        # 4. Advance time past grace period (130s) -> now it SHOULD start misting!
        now_130s = now + dt.timedelta(seconds=130)
        system.controls.update_humidity_controls(driver, record.sensors, now=now_130s)
        assert driver.relay_states.get(12) == 0  # Relay ON (active-low 0)
        assert system.controls.humidity_controls[0].phase == "misting"

        # 5. Finish pulse after 10s and enter settling
        now_140s = now + dt.timedelta(seconds=140)
        driver.mock_rh_fraction = 0.55  # Rose to 55%
        system.run_once(write=True, now=now_140s)
        assert driver.relay_states.get(12) == 1  # Relay OFF
        assert system.controls.humidity_controls[0].phase == "settling"

        # Save state check
        system.save_state()
        assert state_file.exists()

        # 6. Simulate sudden reboot / restart at now_150s (10s into the 60s settle period)
        now_150s = now + dt.timedelta(seconds=150)
        driver2 = MockLabjackDriver(config.device)
        driver2.mock_rh_fraction = 0.40  # Sensor reads low again
        system2 = build_runtime(config, driver=driver2)
        system2.connect()
        system2.initialize(now=now_150s)

        # It must restore that it was in settling with the remaining settle time!
        hc2 = system2.controls.humidity_controls[0]
        assert hc2.phase == "settling"
        assert hc2.pulse_count_in_window == 1
        assert driver2.relay_states.get(12) == 1  # Safe off

        # Run update: it should stay in settling, NOT start a new mist pulse
        system2.run_once(write=True, now=now_150s)
        assert driver2.relay_states.get(12) == 1
        assert hc2.phase == "settling"

        system.close()
        system2.close()
