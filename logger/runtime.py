"""Runtime orchestration for the environmental logger.

This module translates validated configuration into live objects that read
sensors, drive relays and PWM outputs, manage humidity control state, and write
JSONL records. It deliberately keeps hardware register access behind
``LabjackDriver`` and keeps schema validation in ``logger.models``.
"""

from __future__ import annotations

import datetime as dt
import math
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional

from .hardware import LabjackDriver
from .models import (
    ControlState,
    DeviceMeta,
    DeviceTemperatureConfig,
    HumidityControlConfig,
    HumidityScheduleConfig,
    I2CConfig,
    LabjackConfig,
    LogRecord,
    LoggerConfig,
    PWMConfig,
    PWMScheduleConfig,
    RelayConfig,
    SensorReading,
    T9602Config,
    ThermistorConfig,
)


# ----------------------------
# Psychrometric helpers
# ----------------------------

def saturation_pressure_pa(temp_k: float) -> float:
    """Return saturation vapor pressure in pascals for a temperature in kelvin.

    Constants are the same style as your existing ASHRAE-based pws() function.
    """

    c8 = -5.8002206e3
    c9 = 1.3914993
    c10 = -4.8640239e-2
    c11 = 4.1764768e-5
    c12 = -1.4452093e-8
    c13 = 6.5459673

    return math.exp(
        c8 / temp_k
        + c9
        + c10 * temp_k
        + c11 * temp_k**2
        + c12 * temp_k**3
        + c13 * math.log(temp_k)
    )


def dewpoint_c_from_temp_rh(temp_c: float, rh_fraction: float) -> float:
    """Estimate dew point in Celsius from air temperature and relative humidity.

    ``rh_fraction`` is expected as 0..1. Values outside that range are clamped
    away from zero and up to one to avoid taking the logarithm of an invalid
    vapor pressure.
    """

    rh_fraction = max(0.000001, min(1.0, rh_fraction))

    temp_k = temp_c + 273.15
    pws_pa = saturation_pressure_pa(temp_k)
    pw_kpa = pws_pa * rh_fraction * 0.001

    a = math.log(pw_kpa)

    c14 = 6.54
    c15 = 14.526
    c16 = 0.7389
    c17 = 0.09486
    c18 = 0.4569

    return (
        c14
        + c15 * a
        + c16 * a**2
        + c17 * a**3
        + c18 * pw_kpa**0.1984
    )


def c_to_f(temp_c: float) -> float:
    """Convert Celsius to Fahrenheit."""

    return temp_c * 9.0 / 5.0 + 32.0


def vapor_pressure_values_kpa(
    temp_c: float,
    rh_fraction: float,
) -> tuple[float, float, float]:
    """Return saturation vapor pressure, actual vapor pressure, and VPD.

    All returned values are in kilopascals. ``rh_fraction`` is clamped to the
    inclusive 0..1 range before deriving actual vapor pressure.
    """

    rh_fraction = max(0.0, min(1.0, rh_fraction))
    saturation_vapor_pressure_kpa = saturation_pressure_pa(temp_c + 273.15) * 0.001
    actual_vapor_pressure_kpa = saturation_vapor_pressure_kpa * rh_fraction
    vpd_kpa = saturation_vapor_pressure_kpa - actual_vapor_pressure_kpa

    return (
        saturation_vapor_pressure_kpa,
        actual_vapor_pressure_kpa,
        vpd_kpa,
    )


# ----------------------------
# Sensor runtime classes
# ----------------------------

@dataclass
class SensorRuntime(ABC):
    """Abstract adapter that turns one sensor config into live readings."""

    config: DeviceTemperatureConfig | T9602Config | ThermistorConfig

    @property
    def name(self) -> str:
        """Return the configured sensor name used in logs and controllers."""

        return self.config.name

    @property
    def sensor_type(self) -> str:
        """Return the discriminated sensor type from the config model."""

        return self.config.sensor_type

    @abstractmethod
    def read(self, driver: LabjackDriver) -> SensorReading:
        """Read the sensor through ``driver`` and return a serializable sample."""

        raise NotImplementedError


@dataclass
class DeviceTemperatureRuntime(SensorRuntime):
    """Runtime for the LabJack's built-in device temperature sensor."""

    config: DeviceTemperatureConfig

    def read(self, driver: LabjackDriver) -> SensorReading:
        """Read LabJack device temperature and expose it in K, C, and F."""

        temp_k = driver.read_device_temperature_k()
        temp_c = temp_k - 273.15
        temp_f = c_to_f(temp_c)

        return SensorReading(
            name=self.name,
            sensor_type=self.sensor_type,
            values={
                "temp_k": temp_k,
                "temp_c": temp_c,
                "temp_f": temp_f,
            },
            status=0,
        )


@dataclass
class T9602Runtime(SensorRuntime):
    """Runtime decoder for the T9602 temperature/RH sensor."""

    config: T9602Config

    def read(self, driver: LabjackDriver) -> SensorReading:
        """Read T9602 bytes over I2C and calculate derived humidity metrics."""

        i2c: I2CConfig = self.config.i2c

        raw = driver.read_i2c(
            sda=i2c.sda_dio,
            scl=i2c.scl_dio,
            addr=i2c.address,
            rx_len=i2c.rx_len,
        )

        if len(raw) < 4:
            raise ValueError(
                f"T9602 expected 4 bytes but received {len(raw)} bytes."
            )

        status = (raw[0] >> 6) & 0x03

        rh_raw = ((raw[0] & 0x3F) << 8) | raw[1]
        rh_fraction = rh_raw / 16384.0

        temp_raw = (raw[2] << 6) | (raw[3] >> 2)
        temp_c = (temp_raw / 16384.0) * 165.0 - 40.0
        temp_f = c_to_f(temp_c)

        dewpoint_c = dewpoint_c_from_temp_rh(temp_c, rh_fraction)
        dewpoint_f = c_to_f(dewpoint_c)
        saturation_vapor_pressure_kpa, actual_vapor_pressure_kpa, vpd_kpa = (
            vapor_pressure_values_kpa(temp_c, rh_fraction)
        )

        return SensorReading(
            name=self.name,
            sensor_type=self.sensor_type,
            values={
                "rh": rh_fraction,
                "rh_pct": rh_fraction * 100.0,
                "temp_c": temp_c,
                "temp_f": temp_f,
                "dewpoint_c": dewpoint_c,
                "dewpoint_f": dewpoint_f,
                "saturation_vapor_pressure_kpa": saturation_vapor_pressure_kpa,
                "actual_vapor_pressure_kpa": actual_vapor_pressure_kpa,
                "vpd_kpa": vpd_kpa,
            },
            status=status,
            raw_data=raw,
        )


@dataclass
class ThermistorRuntime(SensorRuntime):
    """Runtime placeholder for analog thermistor readings."""

    config: ThermistorConfig

    def read(self, driver: LabjackDriver) -> SensorReading:
        """Read raw thermistor voltage until temperature conversion is added."""

        voltage = driver.read_ain(ain=self.config.ain)

        return SensorReading(
            name=self.name,
            sensor_type=self.sensor_type,
            values={
                "voltage": voltage,
                "note": "Thermistor conversion not implemented yet.",
            },
            status=0,
            raw_data=[voltage],
        )


# ----------------------------
# Control runtime classes
# ----------------------------

@dataclass
class RelayRuntime:
    """Stateful wrapper around one active-low relay output."""

    config: RelayConfig
    state: Literal[0, 1] = field(init=False)

    def __post_init__(self) -> None:
        """Initialize runtime state from configuration."""

        self.state = self.config.initial_state

    @property
    def name(self) -> str:
        """Return the relay name used by controllers and snapshots."""

        return self.config.name

    def apply(self, driver: LabjackDriver) -> None:
        """Write the current relay state to the hardware driver."""

        driver.set_relay(
            dio=self.config.dio,
            state=self.state,
        )

    def set_state(
        self,
        state: Literal[0, 1],
        driver: Optional[LabjackDriver] = None,
    ) -> None:
        """Set relay state and optionally apply it immediately.

        For this active-low relay board, ``0`` is on and ``1`` is off. The
        method still accepts truthy/falsy values and normalizes them to those
        two literal states.
        """

        self.state = 1 if state else 0

        if driver is not None:
            self.apply(driver)

    def toggle(self, driver: Optional[LabjackDriver] = None) -> None:
        """Flip the relay state and optionally write it to hardware."""

        self.set_state(
            0 if self.state else 1,
            driver=driver,
        )

    def snapshot(self) -> ControlState:
        """Return a serializable snapshot for log records."""

        return ControlState(
            name=self.name,
            control_type="relay",
            values={
                "dio": self.config.dio,
                "onoff": self.state,
            },
        )


@dataclass
class PWMRuntime:
    """Stateful PWM output with optional daily scheduling.

    ``duty_cycle`` represents the desired logical duty cycle while
    ``output_duty_cycle`` accounts for whether the output is enabled. Scheduled
    updates can ramp from a configured minimum to maximum and can fall back to
    a defined outside-window duty cycle.
    """

    config: PWMConfig

    duty_cycle: float = field(init=False)
    enabled: bool = field(init=False)
    schedule_status: dict[str, object] = field(init=False, default_factory=dict)

    def __post_init__(self) -> None:
        """Initialize live PWM state from configuration defaults."""

        self.duty_cycle = self.config.duty_cycle
        self.enabled = self.config.enabled
        self.schedule_status = {
            "scheduled": bool(self.config.schedules),
            "mode": "manual",
        }

    @property
    def name(self) -> str:
        """Return the PWM output name used in logs."""

        return self.config.name

    def configure_and_apply(self, driver: LabjackDriver) -> None:
        """Configure hardware PWM and apply the current output duty cycle."""

        driver.configure_pwm(
            dio=self.config.dio,
            frequency_hz=self.config.frequency_hz,
            duty_cycle=self.output_duty_cycle,
            roll_value=self.config.roll_value,
        )

    @property
    def output_duty_cycle(self) -> float:
        """Return the hardware duty cycle after enable/disable clamping."""

        if not self.enabled:
            return 0.0

        return max(0.0, min(1.0, self.duty_cycle))

    def apply(self, driver: LabjackDriver) -> None:
        """Write the current output duty cycle to hardware."""

        driver.set_pwm(
            dio=self.config.dio,
            duty_cycle=self.output_duty_cycle,
        )

    def set_duty_cycle(
        self,
        duty_cycle: float,
        driver: Optional[LabjackDriver] = None,
    ) -> None:
        """Manually set duty cycle and mark scheduling as overridden."""

        self.duty_cycle = max(0.0, min(1.0, duty_cycle))
        self.schedule_status = {
            "scheduled": bool(self.config.schedules),
            "mode": "manual_override",
        }

        if driver is not None:
            self.apply(driver)

    def set_enabled(
        self,
        enabled: bool,
        driver: Optional[LabjackDriver] = None,
    ) -> None:
        """Enable or disable PWM output and optionally apply immediately."""

        self.enabled = enabled

        if driver is not None:
            self.apply(driver)

    def apply_schedule(
        self,
        *,
        now: Optional[dt.datetime] = None,
        driver: Optional[LabjackDriver] = None,
    ) -> bool:
        """Apply the active schedule for ``now`` if this PWM has schedules.

        Returns ``True`` when a scheduled duty cycle was selected and stored.
        Returns ``False`` when no schedules are configured, leaving any manual
        duty-cycle state untouched.
        """

        scheduled = self.scheduled_duty_cycle(now=now)

        if scheduled is None:
            return False

        duty_cycle, status = scheduled
        self.duty_cycle = duty_cycle
        self.schedule_status = status

        if driver is not None:
            self.apply(driver)

        return True

    def scheduled_duty_cycle(
        self,
        *,
        now: Optional[dt.datetime] = None,
    ) -> Optional[tuple[float, dict[str, object]]]:
        """Calculate the scheduled duty cycle and diagnostic status.

        Each configured schedule is interpreted as a daily local-time window.
        Windows may cross midnight. If more than one schedule is active, the
        highest resulting duty cycle wins. If none are active, the highest
        outside-window duty cycle wins so overlapping background schedules still
        behave predictably.
        """

        if not self.config.schedules:
            return None

        now = now or dt.datetime.now().astimezone()
        now = self._normalize_now(now)

        active_candidates: list[tuple[float, dict[str, object]]] = []
        outside_candidates: list[tuple[float, dict[str, object]]] = []

        for schedule in self.config.schedules:
            start_at, end_at = self._window_for(schedule, now)
            is_active = start_at <= now < end_at

            min_duty = self._clamp01(schedule.min_duty_cycle)
            max_duty = self._clamp01(schedule.max_duty_cycle)

            if max_duty < min_duty:
                min_duty, max_duty = max_duty, min_duty

            outside_duty = schedule.outside_duty_cycle
            if outside_duty is None:
                outside_duty = min_duty
            outside_duty = self._clamp01(outside_duty)

            outside_candidates.append((
                outside_duty,
                {
                    "scheduled": True,
                    "mode": "outside_schedule",
                    "active_schedule": None,
                    "selected_schedule": schedule.name,
                    "window_start": start_at.isoformat(),
                    "window_end": end_at.isoformat(),
                    "ramp_duration_s": schedule.ramp_duration_s,
                    "min_duty_cycle": min_duty,
                    "max_duty_cycle": max_duty,
                },
            ))

            if not is_active:
                continue

            elapsed_s = max(0.0, (now - start_at).total_seconds())
            remaining_s = max(0.0, (end_at - now).total_seconds())
            ramp_duration_s = max(0.0, schedule.ramp_duration_s)

            if ramp_duration_s > 0.0 and elapsed_s < ramp_duration_s:
                ramp_fraction = elapsed_s / ramp_duration_s
                duty_cycle = min_duty + ((max_duty - min_duty) * ramp_fraction)
                mode = "ramping_up"
            else:
                ramp_fraction = 1.0
                duty_cycle = max_duty
                mode = "max"

            active_candidates.append((
                self._clamp01(duty_cycle),
                {
                    "scheduled": True,
                    "mode": mode,
                    "active_schedule": schedule.name,
                    "selected_schedule": schedule.name,
                    "window_start": start_at.isoformat(),
                    "window_end": end_at.isoformat(),
                    "elapsed_s": elapsed_s,
                    "remaining_s": remaining_s,
                    "ramp_duration_s": ramp_duration_s,
                    "ramp_fraction": ramp_fraction,
                    "min_duty_cycle": min_duty,
                    "max_duty_cycle": max_duty,
                },
            ))

        if active_candidates:
            return max(active_candidates, key=lambda item: item[0])

        return max(outside_candidates, key=lambda item: item[0])

    @staticmethod
    def _normalize_now(now: dt.datetime) -> dt.datetime:
        """Normalize aware datetimes to local time while preserving naive ones."""

        if now.tzinfo is None:
            return now

        return now.astimezone()

    @staticmethod
    def _window_for(
        schedule: PWMScheduleConfig,
        now: dt.datetime,
    ) -> tuple[dt.datetime, dt.datetime]:
        """Return the concrete schedule window containing or nearest ``now``."""

        start_at = dt.datetime.combine(now.date(), schedule.start_time)
        end_at = dt.datetime.combine(now.date(), schedule.end_time)

        if now.tzinfo is not None:
            start_at = start_at.replace(tzinfo=now.tzinfo)
            end_at = end_at.replace(tzinfo=now.tzinfo)

        if end_at <= start_at:
            if now < end_at:
                start_at -= dt.timedelta(days=1)
            else:
                end_at += dt.timedelta(days=1)

        return start_at, end_at

    @staticmethod
    def _clamp01(value: float) -> float:
        """Clamp a duty cycle to the inclusive 0..1 range."""

        return max(0.0, min(1.0, value))

    def snapshot(self) -> ControlState:
        """Return a serializable PWM state snapshot for the log record."""

        return ControlState(
            name=self.name,
            control_type="pwm",
            values={
                "dio": self.config.dio,
                "frequency_hz": self.config.frequency_hz,
                "duty_cycle": self.duty_cycle,
                "enabled": self.enabled,
                "output_duty_cycle": self.output_duty_cycle,
                "schedule": self.schedule_status,
            },
        )



@dataclass
class HumidityControlRuntime:
    """
    Pulse-based humidity controller for an active-low mister relay.

    The controller never leaves the mister on longer than max_pulse_s.
    After each pulse it waits for the air to mix/settle, records the RH
    delta, and then decides whether another pulse is needed to reach the
    active schedule target.
    """

    config: HumidityControlConfig
    relay: RelayRuntime

    phase: str = field(init=False, default="idle")
    active_window_key: Optional[str] = field(init=False, default=None)
    pulse_count_in_window: int = field(init=False, default=0)

    pulse_started_at: Optional[dt.datetime] = field(init=False, default=None)
    pulse_started_rh_pct: Optional[float] = field(init=False, default=None)
    pulse_target_rh_pct: Optional[float] = field(init=False, default=None)
    pulse_trigger_rh_pct: Optional[float] = field(init=False, default=None)

    last_pulse_ended_at: Optional[dt.datetime] = field(init=False, default=None)
    last_pulse_duration_s: Optional[float] = field(init=False, default=None)
    last_pulse_end_rh_pct: Optional[float] = field(init=False, default=None)
    last_event: Optional[dict[str, object]] = field(init=False, default=None)

    last_update_at: Optional[dt.datetime] = field(init=False, default=None)
    last_rh_pct: Optional[float] = field(init=False, default=None)
    rh_rate_pct_per_hour: Optional[float] = field(init=False, default=None)
    drying_rate_rh_pct_per_hour_ema: Optional[float] = field(
        init=False,
        default=None,
    )

    status: dict[str, object] = field(init=False, default_factory=dict)

    @property
    def name(self) -> str:
        """Return the humidity controller name used in snapshots."""

        return self.config.name

    def initialize(
        self,
        driver: LabjackDriver,
        *,
        now: Optional[dt.datetime] = None,
    ) -> None:
        """Reset controller state and force the mister relay off."""

        self.phase = "idle"
        self.active_window_key = None
        self.pulse_count_in_window = 0
        self.relay.set_state(1, driver=driver)
        self.status = {
            "enabled": self.config.enabled,
            "mode": "initialized_off",
            "phase": self.phase,
            "relay_name": self.relay.name,
            "relay_state": self.relay.state,
        }

    def update(
        self,
        driver: LabjackDriver,
        sensor_readings: list[SensorReading],
        *,
        now: Optional[dt.datetime] = None,
    ) -> None:
        """Advance the humidity control state machine by one sample.

        The update uses the latest RH reading, the active schedule, and the
        current phase to decide whether to start a pulse, continue misting,
        settle after a pulse, or stay off. Every exit path updates
        ``self.status`` for logging.
        """

        now = now or dt.datetime.now().astimezone()
        now = self._normalize_now(now)

        rh_pct = self._find_rh_pct(sensor_readings)
        active = self._active_schedule(now)

        if rh_pct is not None:
            self._update_rh_rate(now=now, rh_pct=rh_pct)

        if not self.config.enabled:
            self._force_off(driver, mode="disabled")
            self._set_status(
                mode="disabled",
                now=now,
                rh_pct=rh_pct,
                schedule_info=active,
            )
            return

        if rh_pct is None:
            self._force_off(driver, mode="sensor_unavailable")
            self._set_status(
                mode="sensor_unavailable",
                now=now,
                rh_pct=None,
                schedule_info=active,
            )
            return

        if active is None:
            self._force_off(driver, mode="outside_schedule")
            self._set_status(
                mode="outside_schedule",
                now=now,
                rh_pct=rh_pct,
                schedule_info=None,
            )
            return

        schedule, start_at, end_at, target_rh_pct, trigger_below_rh_pct = active
        window_key = f"{schedule.name}:{start_at.isoformat()}:{end_at.isoformat()}"

        if window_key != self.active_window_key:
            self.active_window_key = window_key
            self.pulse_count_in_window = 0
            self.phase = "idle"
            self.pulse_started_at = None
            self.pulse_started_rh_pct = None

        schedule_info = active

        if rh_pct >= self.config.hard_max_rh_pct:
            self._force_off(driver, mode="hard_max_guard")
            self.pulse_count_in_window = 0
            self._set_status(
                mode="hard_max_guard",
                now=now,
                rh_pct=rh_pct,
                schedule_info=schedule_info,
                target_rh_pct=target_rh_pct,
                trigger_below_rh_pct=trigger_below_rh_pct,
            )
            return

        if self.phase == "misting":
            self._update_misting(
                driver=driver,
                now=now,
                rh_pct=rh_pct,
                target_rh_pct=target_rh_pct,
                trigger_below_rh_pct=trigger_below_rh_pct,
                schedule_info=schedule_info,
            )
            return

        if self.phase == "settling":
            if self._update_settling(
                driver=driver,
                now=now,
                rh_pct=rh_pct,
                target_rh_pct=target_rh_pct,
                trigger_below_rh_pct=trigger_below_rh_pct,
                schedule_info=schedule_info,
            ):
                return

        self._force_off(driver, mode="idle")

        if rh_pct >= target_rh_pct:
            self.pulse_count_in_window = 0
            self._set_status(
                mode="target_reached",
                now=now,
                rh_pct=rh_pct,
                schedule_info=schedule_info,
                target_rh_pct=target_rh_pct,
                trigger_below_rh_pct=trigger_below_rh_pct,
            )
            return

        if self.pulse_count_in_window >= self.config.max_consecutive_pulses:
            self._set_status(
                mode="max_consecutive_pulses",
                now=now,
                rh_pct=rh_pct,
                schedule_info=schedule_info,
                target_rh_pct=target_rh_pct,
                trigger_below_rh_pct=trigger_below_rh_pct,
            )
            return

        if rh_pct < trigger_below_rh_pct:
            self._start_pulse(
                driver=driver,
                now=now,
                rh_pct=rh_pct,
                target_rh_pct=target_rh_pct,
                trigger_below_rh_pct=trigger_below_rh_pct,
                schedule_info=schedule_info,
                reason="below_trigger",
            )
            return

        self._set_status(
            mode="waiting_below_trigger",
            now=now,
            rh_pct=rh_pct,
            schedule_info=schedule_info,
            target_rh_pct=target_rh_pct,
            trigger_below_rh_pct=trigger_below_rh_pct,
        )

    def _update_misting(
        self,
        *,
        driver: LabjackDriver,
        now: dt.datetime,
        rh_pct: float,
        target_rh_pct: float,
        trigger_below_rh_pct: float,
        schedule_info: tuple[
            HumidityScheduleConfig,
            dt.datetime,
            dt.datetime,
            float,
            float,
        ],
    ) -> None:
        """Handle one control tick while the mister relay should be on."""

        if self.pulse_started_at is None:
            self._finish_pulse(
                driver=driver,
                now=now,
                rh_pct=rh_pct,
                reason="missing_start_time",
            )
            return

        elapsed_s = max(0.0, (now - self.pulse_started_at).total_seconds())
        current_delta = self._current_pulse_delta(rh_pct)

        if rh_pct >= target_rh_pct:
            self._finish_pulse(
                driver=driver,
                now=now,
                rh_pct=rh_pct,
                reason="target_reached",
            )
            self._set_status(
                mode="settling_after_target",
                now=now,
                rh_pct=rh_pct,
                schedule_info=schedule_info,
                target_rh_pct=target_rh_pct,
                trigger_below_rh_pct=trigger_below_rh_pct,
                current_pulse_elapsed_s=elapsed_s,
                current_pulse_delta_rh_pct=current_delta,
            )
            return

        if elapsed_s >= self.config.max_pulse_s:
            self._finish_pulse(
                driver=driver,
                now=now,
                rh_pct=rh_pct,
                reason="max_pulse_s",
            )
            self._set_status(
                mode="settling_after_max_pulse",
                now=now,
                rh_pct=rh_pct,
                schedule_info=schedule_info,
                target_rh_pct=target_rh_pct,
                trigger_below_rh_pct=trigger_below_rh_pct,
                current_pulse_elapsed_s=elapsed_s,
                current_pulse_delta_rh_pct=current_delta,
            )
            return

        self.relay.set_state(0, driver=driver)
        self._set_status(
            mode="misting",
            now=now,
            rh_pct=rh_pct,
            schedule_info=schedule_info,
            target_rh_pct=target_rh_pct,
            trigger_below_rh_pct=trigger_below_rh_pct,
            current_pulse_elapsed_s=elapsed_s,
            current_pulse_delta_rh_pct=current_delta,
        )

    def _update_settling(
        self,
        *,
        driver: LabjackDriver,
        now: dt.datetime,
        rh_pct: float,
        target_rh_pct: float,
        trigger_below_rh_pct: float,
        schedule_info: tuple[
            HumidityScheduleConfig,
            dt.datetime,
            dt.datetime,
            float,
            float,
        ],
    ) -> bool:
        """Handle the post-pulse wait period.

        Returns ``True`` when the settling phase consumed this update. Returns
        ``False`` when settling state was invalid or complete and the caller
        should continue with normal idle decisions.
        """

        self.relay.set_state(1, driver=driver)

        if self.last_pulse_ended_at is None:
            self.phase = "idle"
            return False

        settle_elapsed_s = max(0.0, (now - self.last_pulse_ended_at).total_seconds())
        required_wait_s = max(self.config.settle_s, self.config.min_off_s)
        settled_delta = self._current_pulse_delta(rh_pct)

        if settle_elapsed_s < required_wait_s:
            self._set_status(
                mode="settling",
                now=now,
                rh_pct=rh_pct,
                schedule_info=schedule_info,
                target_rh_pct=target_rh_pct,
                trigger_below_rh_pct=trigger_below_rh_pct,
                settle_elapsed_s=settle_elapsed_s,
                required_wait_s=required_wait_s,
                settled_delta_rh_pct=settled_delta,
            )
            return True

        self._finalize_settle(now=now, rh_pct=rh_pct)

        if rh_pct >= target_rh_pct:
            self.phase = "idle"
            self.pulse_count_in_window = 0
            self._set_status(
                mode="target_reached_after_settle",
                now=now,
                rh_pct=rh_pct,
                schedule_info=schedule_info,
                target_rh_pct=target_rh_pct,
                trigger_below_rh_pct=trigger_below_rh_pct,
                settle_elapsed_s=settle_elapsed_s,
                required_wait_s=required_wait_s,
                settled_delta_rh_pct=settled_delta,
            )
            return True

        if self.pulse_count_in_window >= self.config.max_consecutive_pulses:
            self.phase = "idle"
            self._set_status(
                mode="max_consecutive_pulses",
                now=now,
                rh_pct=rh_pct,
                schedule_info=schedule_info,
                target_rh_pct=target_rh_pct,
                trigger_below_rh_pct=trigger_below_rh_pct,
                settle_elapsed_s=settle_elapsed_s,
                required_wait_s=required_wait_s,
                settled_delta_rh_pct=settled_delta,
            )
            return True

        # Once a misting sequence starts, use the target as the repeat
        # threshold. The lower trigger only decides when to begin a sequence.
        if rh_pct < target_rh_pct:
            self._start_pulse(
                driver=driver,
                now=now,
                rh_pct=rh_pct,
                target_rh_pct=target_rh_pct,
                trigger_below_rh_pct=trigger_below_rh_pct,
                schedule_info=schedule_info,
                reason="below_target_after_settle",
            )
            return True

        self.phase = "idle"
        return False

    def _start_pulse(
        self,
        *,
        driver: LabjackDriver,
        now: dt.datetime,
        rh_pct: float,
        target_rh_pct: float,
        trigger_below_rh_pct: float,
        schedule_info: tuple[
            HumidityScheduleConfig,
            dt.datetime,
            dt.datetime,
            float,
            float,
        ],
        reason: str,
    ) -> None:
        """Start a bounded misting pulse and capture its starting conditions."""

        self.phase = "misting"
        self.pulse_count_in_window += 1
        self.pulse_started_at = now
        self.pulse_started_rh_pct = rh_pct
        self.pulse_target_rh_pct = target_rh_pct
        self.pulse_trigger_rh_pct = trigger_below_rh_pct
        self.relay.set_state(0, driver=driver)

        self._set_status(
            mode="misting",
            now=now,
            rh_pct=rh_pct,
            schedule_info=schedule_info,
            target_rh_pct=target_rh_pct,
            trigger_below_rh_pct=trigger_below_rh_pct,
            current_pulse_elapsed_s=0.0,
            current_pulse_delta_rh_pct=0.0,
            start_reason=reason,
        )

    def _finish_pulse(
        self,
        *,
        driver: LabjackDriver,
        now: dt.datetime,
        rh_pct: float,
        reason: str,
    ) -> None:
        """Stop the mister and record the pulse event for diagnostics."""

        started_at = self.pulse_started_at
        start_rh_pct = self.pulse_started_rh_pct
        duration_s = None
        delta_rh_pct = None

        if started_at is not None:
            duration_s = max(0.0, (now - started_at).total_seconds())

        if start_rh_pct is not None:
            delta_rh_pct = rh_pct - start_rh_pct

        self.relay.set_state(1, driver=driver)
        self.phase = "settling"
        self.last_pulse_ended_at = now
        self.last_pulse_duration_s = duration_s
        self.last_pulse_end_rh_pct = rh_pct
        self.last_event = {
            "event": "mist_pulse",
            "started_at": started_at.isoformat() if started_at else None,
            "ended_at": now.isoformat(),
            "duration_s": duration_s,
            "start_rh_pct": start_rh_pct,
            "end_rh_pct": rh_pct,
            "pulse_delta_rh_pct": delta_rh_pct,
            "target_rh_pct": self.pulse_target_rh_pct,
            "trigger_below_rh_pct": self.pulse_trigger_rh_pct,
            "stop_reason": reason,
            "pulse_number_in_window": self.pulse_count_in_window,
        }

    def _finalize_settle(self, *, now: dt.datetime, rh_pct: float) -> None:
        """Attach settled RH response details to the most recent pulse event."""

        if not self.last_event:
            return

        start_rh_pct = self.pulse_started_rh_pct
        settled_delta = None
        if start_rh_pct is not None:
            settled_delta = rh_pct - start_rh_pct

        settle_duration_s = None
        if self.last_pulse_ended_at is not None:
            settle_duration_s = max(
                0.0,
                (now - self.last_pulse_ended_at).total_seconds(),
            )

        self.last_event.update({
            "settled_at": now.isoformat(),
            "settle_duration_s": settle_duration_s,
            "settled_rh_pct": rh_pct,
            "settled_delta_rh_pct": settled_delta,
            "low_response": (
                settled_delta is not None
                and settled_delta < self.config.low_response_delta_rh_pct
            ),
            "drying_rate_rh_pct_per_hour_ema": (
                self.drying_rate_rh_pct_per_hour_ema
            ),
        })

    def _force_off(self, driver: LabjackDriver, *, mode: str) -> None:
        """Turn the mister relay off and clear active phases when appropriate."""

        self.relay.set_state(1, driver=driver)
        if self.phase == "misting":
            self.phase = "idle"
        elif mode in {
            "disabled",
            "sensor_unavailable",
            "outside_schedule",
            "hard_max_guard",
        }:
            self.phase = "idle"

    def _set_status(
        self,
        *,
        mode: str,
        now: dt.datetime,
        rh_pct: Optional[float],
        schedule_info: Optional[
            tuple[HumidityScheduleConfig, dt.datetime, dt.datetime, float, float]
        ],
        target_rh_pct: Optional[float] = None,
        trigger_below_rh_pct: Optional[float] = None,
        **extra: object,
    ) -> None:
        """Build the diagnostic status payload emitted in control snapshots."""

        schedule_name = None
        window_start = None
        window_end = None

        if schedule_info is not None:
            schedule, start_at, end_at, active_target, active_trigger = schedule_info
            schedule_name = schedule.name
            window_start = start_at.isoformat()
            window_end = end_at.isoformat()
            target_rh_pct = active_target if target_rh_pct is None else target_rh_pct
            trigger_below_rh_pct = (
                active_trigger
                if trigger_below_rh_pct is None
                else trigger_below_rh_pct
            )

        self.status = {
            "enabled": self.config.enabled,
            "mode": mode,
            "phase": self.phase,
            "sensor": self.config.rh_sensor,
            "rh_pct": rh_pct,
            "target_rh_pct": target_rh_pct,
            "trigger_below_rh_pct": trigger_below_rh_pct,
            "hard_max_rh_pct": self.config.hard_max_rh_pct,
            "active_schedule": schedule_name,
            "window_start": window_start,
            "window_end": window_end,
            "relay_name": self.relay.name,
            "relay_state": self.relay.state,
            "max_pulse_s": self.config.max_pulse_s,
            "settle_s": self.config.settle_s,
            "min_off_s": self.config.min_off_s,
            "pulse_count_in_window": self.pulse_count_in_window,
            "max_consecutive_pulses": self.config.max_consecutive_pulses,
            "rh_rate_pct_per_hour": self.rh_rate_pct_per_hour,
            "drying_rate_rh_pct_per_hour_ema": (
                self.drying_rate_rh_pct_per_hour_ema
            ),
            "last_event": self.last_event,
            "updated_at": now.isoformat(),
        }
        self.status.update(extra)

    def _active_schedule(
        self,
        now: dt.datetime,
    ) -> Optional[tuple[HumidityScheduleConfig, dt.datetime, dt.datetime, float, float]]:
        """Return the active humidity schedule and derived target/trigger.

        Disabled schedules are ignored. Overlapping active schedules resolve to
        the highest target RH so a deliberate humidity peak can override a
        lower background target.
        """

        candidates: list[
            tuple[HumidityScheduleConfig, dt.datetime, dt.datetime, float, float]
        ] = []

        for schedule in self.config.schedules:
            if not schedule.enabled:
                continue

            start_at, end_at = self._window_for(schedule, now)
            if not (start_at <= now < end_at):
                continue

            target_rh_pct = self._clamp_pct(schedule.target_rh_pct)
            target_rh_pct = min(target_rh_pct, self.config.hard_max_rh_pct)

            if schedule.trigger_below_rh_pct is None:
                trigger_below_rh_pct = (
                    target_rh_pct - self.config.default_hysteresis_rh_pct
                )
            else:
                trigger_below_rh_pct = schedule.trigger_below_rh_pct

            trigger_below_rh_pct = self._clamp_pct(trigger_below_rh_pct)
            trigger_below_rh_pct = min(trigger_below_rh_pct, target_rh_pct)

            candidates.append((
                schedule,
                start_at,
                end_at,
                target_rh_pct,
                trigger_below_rh_pct,
            ))

        if not candidates:
            return None

        # If schedules overlap, prefer the higher target. That makes an
        # evening humidity peak override a drier background schedule.
        return max(candidates, key=lambda item: item[3])

    def _find_rh_pct(self, sensor_readings: list[SensorReading]) -> Optional[float]:
        """Extract this controller's RH percentage from sensor readings."""

        for reading in sensor_readings:
            if reading.name != self.config.rh_sensor:
                continue

            if "rh_pct" in reading.values:
                return float(reading.values["rh_pct"])

            if "rh" in reading.values:
                rh = float(reading.values["rh"])
                return rh * 100.0 if rh <= 1.0 else rh

        return None

    def _update_rh_rate(self, *, now: dt.datetime, rh_pct: float) -> None:
        """Update instantaneous RH rate and drying-rate exponential average."""

        if self.last_update_at is not None and self.last_rh_pct is not None:
            dt_s = (now - self.last_update_at).total_seconds()
            if dt_s > 0.0:
                self.rh_rate_pct_per_hour = (
                    (rh_pct - self.last_rh_pct) / (dt_s / 3600.0)
                )

                if self.phase != "misting" and self.rh_rate_pct_per_hour < 0.0:
                    drying_rate = -self.rh_rate_pct_per_hour
                    alpha = 0.2
                    if self.drying_rate_rh_pct_per_hour_ema is None:
                        self.drying_rate_rh_pct_per_hour_ema = drying_rate
                    else:
                        self.drying_rate_rh_pct_per_hour_ema = (
                            alpha * drying_rate
                            + (1.0 - alpha) * self.drying_rate_rh_pct_per_hour_ema
                        )

        self.last_update_at = now
        self.last_rh_pct = rh_pct

    def _current_pulse_delta(self, rh_pct: float) -> Optional[float]:
        """Return RH change since the current pulse started, if known."""

        if self.pulse_started_rh_pct is None:
            return None

        return rh_pct - self.pulse_started_rh_pct

    def snapshot(self) -> ControlState:
        """Return the latest humidity-controller status for the log record."""

        return ControlState(
            name=self.name,
            control_type="humidity",
            values=self.status,
        )

    @staticmethod
    def _normalize_now(now: dt.datetime) -> dt.datetime:
        """Normalize aware datetimes to local time while preserving naive ones."""

        if now.tzinfo is None:
            return now

        return now.astimezone()

    @staticmethod
    def _window_for(
        schedule: HumidityScheduleConfig,
        now: dt.datetime,
    ) -> tuple[dt.datetime, dt.datetime]:
        """Return the concrete humidity window containing or nearest ``now``."""

        start_at = dt.datetime.combine(now.date(), schedule.start_time)
        end_at = dt.datetime.combine(now.date(), schedule.end_time)

        if now.tzinfo is not None:
            start_at = start_at.replace(tzinfo=now.tzinfo)
            end_at = end_at.replace(tzinfo=now.tzinfo)

        if end_at <= start_at:
            if now < end_at:
                start_at -= dt.timedelta(days=1)
            else:
                end_at += dt.timedelta(days=1)

        return start_at, end_at

    @staticmethod
    def _clamp_pct(value: float) -> float:
        """Clamp a relative-humidity percentage to the inclusive 0..100 range."""

        return max(0.0, min(100.0, value))


# ----------------------------
# Managers / orchestration
# ----------------------------

@dataclass
class AcquisitionManager:
    """Owns sensor runtimes and collects a batch of sensor readings."""

    sensors: list[SensorRuntime] = field(default_factory=list)

    def collect(self, driver: LabjackDriver) -> list[SensorReading]:
        """Read every configured sensor through the provided driver."""

        return [
            sensor.read(driver)
            for sensor in self.sensors
        ]


@dataclass
class ControlManager:
    """Coordinates relay, PWM, and humidity-control runtime objects."""

    relays: list[RelayRuntime] = field(default_factory=list)
    pwm_outputs: list[PWMRuntime] = field(default_factory=list)
    humidity_controls: list[HumidityControlRuntime] = field(default_factory=list)

    def initialize(
        self,
        driver: LabjackDriver,
        *,
        now: Optional[dt.datetime] = None,
    ) -> None:
        """Initialize controls into a safe hardware state.

        Relays are forced off before controller initialization. PWM outputs are
        scheduled before hardware configuration so startup does not briefly
        apply a stale duty cycle.
        """

        now = now or dt.datetime.now().astimezone()

        # Startup safety: the relay boards for this setup are active-low,
        # so logic 1 is the off state. Force every relay off before anything
        # else starts running.
        for relay in self.relays:
            relay.set_state(1, driver=driver)

        # Initialize controller state after the relay safety pass so any
        # controlled relay, including the mister, begins off.
        for humidity_control in self.humidity_controls:
            humidity_control.initialize(driver, now=now)

        # Initialize scheduled PWM outputs directly to their current scheduled
        # state instead of briefly applying a stale/default duty cycle first.
        for pwm in self.pwm_outputs:
            pwm.apply_schedule(now=now)
            pwm.configure_and_apply(driver)

    def update_schedules(
        self,
        driver: LabjackDriver,
        *,
        now: Optional[dt.datetime] = None,
    ) -> None:
        """Apply scheduled PWM updates for the current sample time."""

        now = now or dt.datetime.now().astimezone()

        for pwm in self.pwm_outputs:
            if pwm.apply_schedule(now=now):
                pwm.apply(driver)

    def update_humidity_controls(
        self,
        driver: LabjackDriver,
        sensor_readings: list[SensorReading],
        *,
        now: Optional[dt.datetime] = None,
    ) -> None:
        """Advance every humidity controller using the latest sensor readings."""

        now = now or dt.datetime.now().astimezone()

        for humidity_control in self.humidity_controls:
            humidity_control.update(
                driver,
                sensor_readings,
                now=now,
            )

    def snapshots(self) -> list[ControlState]:
        """Collect serializable state from all controls."""

        states: list[ControlState] = []

        for relay in self.relays:
            states.append(relay.snapshot())

        for pwm in self.pwm_outputs:
            states.append(pwm.snapshot())

        for humidity_control in self.humidity_controls:
            states.append(humidity_control.snapshot())

        return states



@dataclass
class LogWriter:
    """Append log records to one JSONL file per local date."""

    log_dir: Path
    current_path: Optional[Path] = None

    def path_for_today(self) -> Path:
        """Return the JSONL path for today's local date."""

        today = dt.date.today().isoformat()
        return self.log_dir / f"{today}.jsonl"

    def write(self, record: LogRecord) -> Path:
        """Append one record as a JSON object line and return the path used."""

        self.log_dir.mkdir(parents=True, exist_ok=True)

        path = self.path_for_today()
        self.current_path = path

        with path.open("a", encoding="utf-8") as f:
            f.write(record.model_dump_json() + "\n")

        return path


@dataclass
class Scheduler:
    """Monotonic interval scheduler for periodic sampling."""

    sample_interval_s: float
    next_sample_at: Optional[float] = None

    def reset(self) -> None:
        """Make the scheduler due immediately."""

        self.next_sample_at = time.monotonic()

    def due(self) -> bool:
        """Return whether the current monotonic time has reached the next sample."""

        if self.next_sample_at is None:
            self.reset()

        assert self.next_sample_at is not None

        return time.monotonic() >= self.next_sample_at

    def mark_sample_complete(self) -> None:
        """Advance ``next_sample_at`` by whole intervals until it is future."""

        now = time.monotonic()

        if self.next_sample_at is None:
            self.next_sample_at = now + self.sample_interval_s
            return

        while self.next_sample_at <= now:
            self.next_sample_at += self.sample_interval_s


@dataclass
class SystemRuntime:
    """Top-level runtime facade used by scripts and tests."""

    config: LoggerConfig
    driver: LabjackDriver

    acquisition: AcquisitionManager
    controls: ControlManager
    log_writer: LogWriter
    scheduler: Scheduler

    def connect(self) -> "SystemRuntime":
        """Open the hardware driver connection and return ``self``."""

        self.driver.connect()
        return self

    def initialize(
        self,
        *,
        now: Optional[dt.datetime] = None,
    ) -> "SystemRuntime":
        """Initialize controls and sampling schedule after connecting."""

        self.controls.initialize(self.driver, now=now)
        self.scheduler.reset()
        return self

    def collect_record(self) -> LogRecord:
        """Collect one complete log record without writing it to disk.

        Sensor and control errors are captured inside the returned record so a
        single failed read does not stop the rest of the sampling pass.
        """

        errors: list[str] = []
        sensor_readings: list[SensorReading] = []
        now = dt.datetime.now().astimezone()

        try:
            self.controls.update_schedules(self.driver, now=now)
        except Exception as exc:
            errors.append(f"controls: {type(exc).__name__}: {exc}")

        for sensor in self.acquisition.sensors:
            try:
                sensor_readings.append(sensor.read(self.driver))
            except Exception as exc:
                errors.append(f"{sensor.name}: {type(exc).__name__}: {exc}")

        try:
            self.controls.update_humidity_controls(
                self.driver,
                sensor_readings,
                now=dt.datetime.now().astimezone(),
            )
        except Exception as exc:
            errors.append(f"humidity_controls: {type(exc).__name__}: {exc}")

        device = DeviceMeta(
            device_type=self.config.device.device_type,
            serial_number=self.driver.serial_number,
            connected=self.driver.connected,
        )

        return LogRecord(
            timestamp=dt.datetime.now(dt.timezone.utc),
            device=device,
            sensors=sensor_readings,
            controls=self.controls.snapshots(),
            errors=errors,
        )

    def write_record(self, record: LogRecord) -> Path:
        """Write an already-collected record to the configured log file."""

        return self.log_writer.write(record)

    def run_once(self, *, write: bool = True) -> tuple[LogRecord, Optional[Path]]:
        """Collect one sample, optionally write it, and advance the scheduler."""

        record = self.collect_record()

        path = None
        if write:
            path = self.write_record(record)

        self.scheduler.mark_sample_complete()

        return record, path

    def close(self) -> None:
        """Close the underlying hardware driver."""

        self.driver.close()


# ----------------------------
# Builders
# ----------------------------

def build_sensor_runtime(
    config: DeviceTemperatureConfig | T9602Config | ThermistorConfig,
) -> SensorRuntime:
    """Create the appropriate sensor runtime for a sensor config model."""

    if isinstance(config, DeviceTemperatureConfig):
        return DeviceTemperatureRuntime(config=config)

    if isinstance(config, T9602Config):
        return T9602Runtime(config=config)

    if isinstance(config, ThermistorConfig):
        return ThermistorRuntime(config=config)

    raise TypeError(f"Unsupported sensor config: {config!r}")


def build_runtime(
    config: LoggerConfig,
    *,
    driver: Optional[LabjackDriver] = None,
) -> SystemRuntime:
    """Build a complete ``SystemRuntime`` from validated logger config."""

    driver = driver or LabjackDriver(config.device)

    sensors = [
        build_sensor_runtime(sensor_config)
        for sensor_config in config.sensors
    ]

    relays = [
        RelayRuntime(config=relay_config)
        for relay_config in config.relays
    ]

    relay_by_name: dict[str, RelayRuntime] = {}
    for relay in relays:
        if relay.name in relay_by_name:
            raise ValueError(f"Duplicate relay name: {relay.name}")
        relay_by_name[relay.name] = relay

    pwm_outputs = [
        PWMRuntime(config=pwm_config)
        for pwm_config in config.pwm_outputs
    ]

    humidity_controls: list[HumidityControlRuntime] = []
    for humidity_config in config.humidity_controls:
        relay = relay_by_name.get(humidity_config.relay)
        if relay is None:
            raise ValueError(
                f"Humidity control {humidity_config.name!r} references "
                f"unknown relay {humidity_config.relay!r}."
            )

        humidity_controls.append(
            HumidityControlRuntime(
                config=humidity_config,
                relay=relay,
            )
        )

    return SystemRuntime(
        config=config,
        driver=driver,
        acquisition=AcquisitionManager(sensors=sensors),
        controls=ControlManager(
            relays=relays,
            pwm_outputs=pwm_outputs,
            humidity_controls=humidity_controls,
        ),
        log_writer=LogWriter(log_dir=config.log_dir),
        scheduler=Scheduler(sample_interval_s=config.sample_interval_s),
    )
