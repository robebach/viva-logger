import datetime as dt
from logger.sun import (
    natural_sun_curve_fraction,
    CloudCoverModel,
    MotorizedDimmerCalculator,
    NEW_CALEDONIA_LAT,
)


def test_natural_sun_curve():
    now_noon = dt.datetime(2026, 10, 31, 13, 0, 0, tzinfo=dt.timezone.utc)
    start = dt.time(6, 0)
    end = dt.time(20, 0)

    # At noon, sun is high in the sky (peak fraction > 0.8)
    noon_intensity = natural_sun_curve_fraction(
        now=now_noon,
        start_time=start,
        end_time=end,
        latitude_deg=NEW_CALEDONIA_LAT,
    )
    assert 0.7 <= noon_intensity <= 1.0

    # At night (02:00), intensity is 0
    now_night = dt.datetime(2026, 10, 31, 2, 0, 0, tzinfo=dt.timezone.utc)
    night_intensity = natural_sun_curve_fraction(
        now=now_night,
        start_time=start,
        end_time=end,
        latitude_deg=NEW_CALEDONIA_LAT,
    )
    assert night_intensity == 0.0


def test_cloud_cover_simulation():
    model = CloudCoverModel(mode="simulated")
    now = dt.datetime(2026, 10, 31, 12, 0, 0, tzinfo=dt.timezone.utc)
    attenuation = model.get_attenuation_factor(now)
    assert 0.2 <= attenuation <= 1.0


def test_motorized_dimmer_stepper_angle():
    dimmer = MotorizedDimmerCalculator(flicker_threshold_duty=0.20, max_rotation_deg=180.0)

    # 1. 50% target brightness -> Above 20% floor -> electrical duty 0.50, lamp pointed down (0 deg)
    duty, angle = dimmer.compute_pwm_and_rotation(0.50)
    assert duty == 0.50
    assert angle == 0.0

    # 2. 10% target brightness -> Halfway into the 20% floor -> electrical duty held at 0.20, rotated 90 deg
    duty, angle = dimmer.compute_pwm_and_rotation(0.10)
    assert duty == 0.20
    assert abs(angle - 90.0) < 0.1

    # 3. 0% target brightness -> Night mode -> electrical duty held at 0.20, rotated 180 deg (pointing straight up)
    duty, angle = dimmer.compute_pwm_and_rotation(0.0)
    assert duty == 0.20
    assert abs(angle - 180.0) < 0.1


def test_daily_weather_peak_duty():
    model = CloudCoverModel(mode="simulated")
    now = dt.datetime(2026, 10, 31, 12, 0, 0, tzinfo=dt.timezone.utc)

    # In simulated mode, cloud cover is 20% -> 80% clear sky
    # peak duty = 0.80 + (1.00 - 0.80) * 0.80 = 0.96
    duty, meta = model.compute_daily_peak_duty(now, min_peak=0.80, max_peak=1.00)
    assert 0.80 <= duty <= 1.00
    assert meta["min_peak"] == 0.80
    assert meta["max_peak"] == 1.00
    assert "cloud_cover_forecast_pct" in meta


def test_pwm_weather_schedule():
    from logger.models import PWMConfig, PWMScheduleConfig
    from logger.runtime import PWMRuntime

    schedule = PWMScheduleConfig(
        name="test-weather-ramp",
        start_time=dt.time(6, 0),
        end_time=dt.time(20, 0),
        min_duty_cycle=0.0,
        max_duty_cycle=1.0,
        ramp_duration_s=60.0,
        weather_modulation=True,
        weather_min_peak=0.80,
        weather_max_peak=1.00,
    )
    config = PWMConfig(name="test-led", dio=6, schedules=[schedule])
    pwm = PWMRuntime(config=config)

    now_noon = dt.datetime(2026, 10, 31, 12, 0, 0)
    res = pwm.scheduled_duty_cycle(now=now_noon)
    assert res is not None
    duty, status = res
    assert 0.80 <= duty <= 1.00
    assert status["mode"] == "max"
    assert "cloud_cover_forecast_pct" in status or "weather_modulated_peak_duty" in status


def test_pwm_ramp_down_to_min_duty():
    from logger.models import PWMConfig, PWMScheduleConfig
    from logger.runtime import PWMRuntime

    schedule = PWMScheduleConfig(
        name="test-ramp",
        start_time=dt.time(6, 0),
        end_time=dt.time(20, 0),
        min_duty_cycle=0.20,
        max_duty_cycle=1.00,
        ramp_duration_s=3600.0,
        outside_duty_cycle=0.0,
    )
    config = PWMConfig(name="test-led", dio=6, schedules=[schedule])
    pwm = PWMRuntime(config=config)

    # 1. 06:00:00 (start of dawn ramp): should start at min_duty (0.20)
    now_dawn = dt.datetime(2026, 11, 4, 6, 0, 0)
    duty, status = pwm.scheduled_duty_cycle(now=now_dawn)
    assert duty == 0.20
    assert status["mode"] == "ramping_up"

    # 2. 19:30:00 (halfway through sunset ramp 19:00 - 20:00): should ramp halfway between 1.0 and 0.20 -> 0.60
    now_sunset = dt.datetime(2026, 11, 4, 19, 30, 0)
    duty, status = pwm.scheduled_duty_cycle(now=now_sunset)
    assert abs(duty - 0.60) < 0.01
    assert status["mode"] == "ramping_down"

    # 3. 19:59:59 (end of sunset ramp): should approach min_duty (0.20)
    now_dusk = dt.datetime(2026, 11, 4, 19, 59, 59)
    duty, status = pwm.scheduled_duty_cycle(now=now_dusk)
    assert abs(duty - 0.20) < 0.01
    assert status["mode"] == "ramping_down"

    # 4. 20:00:01 (outside active window): should be outside_duty (0.0)
    now_night = dt.datetime(2026, 11, 4, 20, 0, 1)
    duty, status = pwm.scheduled_duty_cycle(now=now_night)
    assert duty == 0.0
    assert status["mode"] == "outside_schedule"


def test_labjack_driver_pwm_zero_clamp():
    from unittest.mock import MagicMock
    from logger.hardware import LabjackDriver
    from logger.models import LabjackConfig

    driver = LabjackDriver(LabjackConfig(device_type="T4"))
    mock_ljm = MagicMock()
    driver._ljm = mock_ljm
    driver.handle = 1
    driver.connected = True

    # Initial configure with duty 0.0: should disable EF and write DIO6 = 0
    driver.configure_pwm(dio=6, duty_cycle=0.0)
    mock_ljm.eWriteName.assert_any_call(1, "DIO6_EF_ENABLE", 0)
    mock_ljm.eWriteName.assert_any_call(1, "DIO6", 0)

    # Calling set_pwm with 0.0 again: should be deduplicated (no redundant writes)
    mock_ljm.eWriteName.reset_mock()
    driver.set_pwm(dio=6, duty_cycle=0.0)
    assert mock_ljm.eWriteName.call_count == 0

    # Calling set_pwm with > 0.0: should enable EF and write CONFIG_A
    driver.set_pwm(dio=6, duty_cycle=0.5)
    mock_ljm.eWriteName.assert_any_call(1, "DIO6_EF_ENABLE", 1)
    mock_ljm.eWriteName.assert_any_call(1, "DIO6_EF_CONFIG_A", 40000)

    # Calling set_pwm with 0.0: should clamp off (EF_ENABLE = 0 and DIO6 = 0)
    mock_ljm.eWriteName.reset_mock()
    driver.set_pwm(dio=6, duty_cycle=0.0)
    mock_ljm.eWriteName.assert_any_call(1, "DIO6_EF_ENABLE", 0)
    mock_ljm.eWriteName.assert_any_call(1, "DIO6", 0)
