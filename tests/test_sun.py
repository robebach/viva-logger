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
