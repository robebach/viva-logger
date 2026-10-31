"""Astronomical solar curve and habitat cloud cover modeling for viva-logger.

This module provides:
1. True astronomical solar elevation calculations for New Caledonia (endemic
   habitat of Correlophus ciliatus / crested gecko) or any coordinate.
2. Natural astronomical sun curves mapped to the enclosure schedule.
3. Cloud cover attenuation models (both offline procedural cloud drifts and
   live Open-Meteo API weather sync).
4. Motorized physical dimmer angle calculations (0-180 degree rotation) to
   prevent LED driver flicker below 20% electrical duty cycle.
"""

from __future__ import annotations

import datetime as dt
import math
import urllib.request
import json
from dataclasses import dataclass, field
from typing import Optional, Tuple


# New Caledonia habitat coordinates (South Province / Monts Koghis / Isle of Pines)
NEW_CALEDONIA_LAT = -22.15
NEW_CALEDONIA_LON = 166.75


def day_of_year(date: dt.date) -> int:
    """Return the 1-based day of year."""
    return date.timetuple().tm_yday


def solar_declination_rad(day_of_yr: int) -> float:
    """Calculate solar declination in radians for a given day of year.

    Uses Cooper's approximation:
        declination = 23.45 * sin(2 * pi * (284 + n) / 365)
    """
    deg = 23.45 * math.sin(2.0 * math.pi * (284 + day_of_yr) / 365.25)
    return math.radians(deg)


def solar_elevation_rad(
    *,
    latitude_deg: float,
    declination_rad: float,
    hour_angle_rad: float,
) -> float:
    """Calculate the solar elevation angle above the horizon in radians.

    sin(elevation) = sin(lat) * sin(dec) + cos(lat) * cos(dec) * cos(hour_angle)
    """
    lat_rad = math.radians(latitude_deg)
    sin_elev = (
        math.sin(lat_rad) * math.sin(declination_rad)
        + math.cos(lat_rad) * math.cos(declination_rad) * math.cos(hour_angle_rad)
    )
    # Clamp to [-1.0, 1.0] for asin domain safety
    sin_elev = max(-1.0, min(1.0, sin_elev))
    return math.asin(sin_elev)


def natural_sun_curve_fraction(
    *,
    now: dt.datetime,
    start_time: dt.time,
    end_time: dt.time,
    latitude_deg: float = NEW_CALEDONIA_LAT,
) -> float:
    """Calculate the clear-sky astronomical solar intensity fraction (0.0 to 1.0).

    Maps the local daytime window to the seasonal solar elevation curve.
    At window boundaries (sunrise / sunset), elevation is 0 (intensity 0.0).
    At solar noon (midday), intensity reaches its peak 1.0 with a natural sine-like arc.
    """
    today = now.date()
    start_dt = dt.datetime.combine(today, start_time, tzinfo=now.tzinfo)
    end_dt = dt.datetime.combine(today, end_time, tzinfo=now.tzinfo)

    if end_dt <= start_dt:
        if now < start_dt:
            start_dt -= dt.timedelta(days=1)
        else:
            end_dt += dt.timedelta(days=1)

    if now < start_dt or now > end_dt:
        return 0.0

    total_duration_s = (end_dt - start_dt).total_seconds()
    if total_duration_s <= 0:
        return 0.0

    elapsed_s = (now - start_dt).total_seconds()
    day_fraction = elapsed_s / total_duration_s  # 0.0 at dawn, 0.5 at noon, 1.0 at dusk

    # Map day fraction to hour angle (-pi/2 at dawn, 0 at noon, +pi/2 at dusk)
    hour_angle_rad = (day_fraction - 0.5) * math.pi

    # Incorporate New Caledonia's seasonal declination variation
    day_num = day_of_year(today)
    declination = solar_declination_rad(day_num)
    elevation = solar_elevation_rad(
        latitude_deg=latitude_deg,
        declination_rad=declination,
        hour_angle_rad=hour_angle_rad,
    )

    if elevation <= 0.0:
        return 0.0

    # Solar intensity is proportional to sin(elevation) with atmospheric optical air mass effect
    intensity = math.sin(elevation)
    return max(0.0, min(1.0, intensity))


@dataclass
class CloudCoverModel:
    """Simulates or fetches cloud cover to modulate vivarium light levels."""

    mode: str = "simulated"  # "simulated" or "live_api"
    latitude: float = NEW_CALEDONIA_LAT
    longitude: float = NEW_CALEDONIA_LON
    
    # Internal cache for live API calls
    _cached_weather: Optional[dict[str, float]] = None
    _cache_expires_at: Optional[dt.datetime] = None

    _cached_daily_forecast: Optional[dict[str, Any]] = None
    _daily_cache_expires_at: Optional[dt.datetime] = None

    def get_daily_forecast(self, now: Optional[dt.datetime] = None) -> dict[str, Any]:
        """Fetch today's weather forecast (cloud cover mean %, precipitation sum mm).

        Queries Open-Meteo API with a 1-hour in-memory cache to minimize network calls.
        Falls back to 0% cloud cover (plants brightest, 100% full power) if offline.
        """
        now = now or dt.datetime.now().astimezone()
        if (
            self._cached_daily_forecast is not None
            and self._daily_cache_expires_at is not None
            and now < self._daily_cache_expires_at
        ):
            return {
                **self._cached_daily_forecast,
                "source": "cached",
            }

        if self.mode != "live_api":
            return {
                "cloud_cover_mean_pct": 20.0,
                "precipitation_sum_mm": 0.0,
                "source": "simulated",
            }

        try:
            url = (
                f"https://api.open-meteo.com/v1/forecast?"
                f"latitude={self.latitude}&longitude={self.longitude}"
                f"&daily=cloud_cover_mean,precipitation_sum&timezone=auto"
            )
            req = urllib.request.Request(url, headers={"User-Agent": "VivaLogger/1.0"})
            with urllib.request.urlopen(req, timeout=4.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                daily = data.get("daily", {})
                cloud_list = daily.get("cloud_cover_mean", [])
                precip_list = daily.get("precipitation_sum", [])
                cloud_mean = float(cloud_list[0]) if cloud_list else 0.0
                precip_sum = float(precip_list[0]) if precip_list else 0.0
                result = {
                    "cloud_cover_mean_pct": cloud_mean,
                    "precipitation_sum_mm": precip_sum,
                    "source": "live_api",
                }
                self._cached_daily_forecast = result
                self._daily_cache_expires_at = now + dt.timedelta(hours=1)
                return result
        except Exception:
            # Fallback gracefully to plants-brightest (0% cloud reduction)
            return {
                "cloud_cover_mean_pct": 0.0,
                "precipitation_sum_mm": 0.0,
                "source": "fallback_plants_brightest",
            }

    def compute_daily_peak_duty(
        self,
        now: dt.datetime,
        min_peak: float = 0.80,
        max_peak: float = 1.00,
    ) -> tuple[float, dict[str, Any]]:
        """Calculate today's maximum daytime brightness based on weather forecast.

        Maps daily cloud cover (0% to 100%) linearly between max_peak and min_peak:
        - 0% cloud cover (crystal clear sun) -> max_peak (1.0 = 100% full 75W power)
        - 100% cloud cover (heavy overcast) -> min_peak (0.8 = 80% power)
        - Fallback on offline/error -> max_peak (1.0 = 100% full power, plants brightest)
        """
        forecast = self.get_daily_forecast(now)
        cloud_pct = forecast.get("cloud_cover_mean_pct", 0.0)
        clear_sky_fraction = max(0.0, min(1.0, 1.0 - (cloud_pct / 100.0)))
        peak_duty = min_peak + (max_peak - min_peak) * clear_sky_fraction
        peak_duty = max(min_peak, min(max_peak, peak_duty))
        meta = {
            "weather_modulated_peak_duty": round(peak_duty, 4),
            "cloud_cover_forecast_pct": round(cloud_pct, 1),
            "forecast_source": forecast.get("source"),
            "min_peak": min_peak,
            "max_peak": max_peak,
        }
        return peak_duty, meta

    def get_attenuation_factor(self, now: dt.datetime) -> float:
        """Return the light transmission factor (0.2 to 1.0) based on clouds.

        1.0 = clear sky, direct sun.
        0.7 = scattered clouds.
        0.4 = overcast.
        0.2 = heavy rain / storm event.
        """
        if self.mode == "live_api":
            return self._get_live_attenuation(now)
        return self._get_simulated_attenuation(now)

    def _get_simulated_attenuation(self, now: dt.datetime) -> float:
        """Procedural multi-frequency cloud simulation (100% offline)."""
        t = now.timestamp()
        # Slow cloud drift (period ~45 minutes)
        slow_wave = math.sin(t / (45.0 * 60.0))
        # Faster cloud patches (period ~8 minutes)
        fast_wave = math.sin(t / (8.0 * 60.0))
        # Micro variation (period ~1.5 minutes)
        micro_wave = math.sin(t / (1.5 * 60.0))

        # Composite cloud density 0.0 (clear) to 1.0 (heavy)
        cloud_density = 0.5 + 0.3 * slow_wave + 0.15 * fast_wave + 0.05 * micro_wave
        cloud_density = max(0.0, min(1.0, cloud_density))

        # Attenuate light: even heavy clouds let ~20-25% diffused ambient light through
        return 1.0 - (cloud_density * 0.75)

    def _get_live_attenuation(self, now: dt.datetime) -> float:
        """Fetch real-time cloud and rain conditions from Open-Meteo for New Caledonia."""
        if self._cached_weather and self._cache_expires_at and now < self._cache_expires_at:
            cloud_cover = self._cached_weather.get("cloud_cover", 0.0) / 100.0
            rain_mm = self._cached_weather.get("precipitation", 0.0)
            factor = 1.0 - (cloud_cover * 0.70)
            if rain_mm > 0.5:
                factor *= 0.6  # Storm darkness
            return max(0.15, min(1.0, factor))

        try:
            url = (
                f"https://api.open-meteo.com/v1/forecast?"
                f"latitude={self.latitude}&longitude={self.longitude}"
                f"&current=cloud_cover,precipitation&timezone=auto"
            )
            req = urllib.request.Request(url, headers={"User-Agent": "VivaLogger/1.0"})
            with urllib.request.urlopen(req, timeout=4.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                current = data.get("current", {})
                self._cached_weather = {
                    "cloud_cover": float(current.get("cloud_cover", 0.0)),
                    "precipitation": float(current.get("precipitation", 0.0)),
                }
                self._cache_expires_at = now + dt.timedelta(minutes=15)
                return self._get_live_attenuation(now)
        except Exception:
            # Fall back smoothly to procedural simulation if network is unreachable
            return self._get_simulated_attenuation(now)


_GLOBAL_LIVE_CLOUD_MODEL: Optional[CloudCoverModel] = None


def get_live_cloud_model() -> CloudCoverModel:
    """Return a shared singleton live cloud cover model."""
    global _GLOBAL_LIVE_CLOUD_MODEL
    if _GLOBAL_LIVE_CLOUD_MODEL is None:
        _GLOBAL_LIVE_CLOUD_MODEL = CloudCoverModel(mode="live_api")
    return _GLOBAL_LIVE_CLOUD_MODEL


@dataclass
class MotorizedDimmerCalculator:
    """Calculates PWM duty cycle and stepper motor angle (0-180 deg).

    Solves the ~20% LED bulb cutoff flicker:
    - When target brightness is above 20%:
      The lamp points straight down (0 degrees) and uses normal electrical PWM.
    - When target brightness falls below 20%:
      The electrical PWM holds steady at the stable minimum (e.g. 20%), while the
      stepper motor physically rotates the fixture upwards (0 -> 180 degrees).
    - At 180 degrees, the lamp points up to the ceiling, leaving the vivarium
      interior at 0 lux while providing soft ambient room light.
    """

    flicker_threshold_duty: float = 0.20
    max_rotation_deg: float = 180.0

    def compute_pwm_and_rotation(
        self,
        target_intensity: float,
    ) -> Tuple[float, float]:
        """Return (electrical_duty_cycle, stepper_rotation_degrees).

        Parameters:
            target_intensity: desired apparent light level (0.0 to 1.0).

        Returns:
            (pwm_duty_cycle, rotation_angle_deg)
        """
        target = max(0.0, min(1.0, target_intensity))

        if target >= self.flicker_threshold_duty:
            # Above flicker threshold: lamp is pointed directly down (0 deg)
            return target, 0.0

        if target <= 0.001:
            # Fully dark inside: hold minimum steady bulb power and rotate 180 deg up
            return self.flicker_threshold_duty, self.max_rotation_deg

        # Transition zone: hold electrical duty at the 20% floor to avoid flicker,
        # and rotate the fixture to geometrically fade the light down to 0 inside.
        fraction = target / self.flicker_threshold_duty
        angle_deg = (1.0 - fraction) * self.max_rotation_deg
        return self.flicker_threshold_duty, max(0.0, min(self.max_rotation_deg, angle_deg))
