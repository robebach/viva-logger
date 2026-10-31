"""Pydantic models for logger configuration, readings, and JSONL records.

The runtime keeps device behavior in ``logger.runtime`` and hardware I/O in
``logger.hardware``. This module is deliberately declarative: it defines the
validated shapes used to build the system and the serializable shapes written
to log files.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Annotated, Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    """Base model that rejects unknown fields in configuration and records."""

    model_config = ConfigDict(extra="forbid")


# ----------------------------
# Device config
# ----------------------------

class LabjackConfig(StrictModel):
    """Connection settings passed to ``labjack.ljm.openS``."""

    device_type: Literal["T4", "T7", "ANY"] = "ANY"
    serial_number: str = "ANY"
    connection_type: str = "ANY"


class I2CConfig(StrictModel):
    """I2C bus settings for sensors connected through LabJack DIO pins."""

    sda_dio: int = Field(..., ge=0)
    scl_dio: int = Field(..., ge=0)
    address: int = Field(..., ge=0x00, le=0x7F)
    rx_len: int = Field(4, gt=0)
    speed: int = Field(65536, gt=0)


# ----------------------------
# Control config
# ----------------------------

class RelayConfig(StrictModel):
    """Configuration for one digital relay output.

    This setup uses active-low relay boards, so ``0`` means energized/on and
    ``1`` means de-energized/off.
    """

    name: str
    dio: int = Field(..., ge=0)
    # Relay boards for this setup are active-low: logic 1 = off.
    initial_state: Literal[0, 1] = 1

    cycle_times: list[dt.time] = Field(default_factory=list)
    cycle_duration_s: float = Field(30.0, ge=0.0)


class PWMScheduleConfig(StrictModel):
    """Daily schedule for a PWM output.

    The schedule defines a local-time window, a minimum and maximum duty cycle,
    and an optional ramp at both the start and end of the active window.
    """

    name: str = "default"

    # Daily local-time window when the PWM output should be active.
    # Windows may cross midnight, e.g. start_time=20:00, end_time=02:00.
    start_time: dt.time
    end_time: dt.time

    min_duty_cycle: float = Field(0.03, ge=0.0, le=1.0)
    max_duty_cycle: float = Field(0.4, ge=0.0, le=1.0)
    ramp_duration_s: float = Field(3600.0, ge=0.0)

    # If omitted, the PWM output returns to min_duty_cycle outside
    # the scheduled window.
    outside_duty_cycle: Optional[float] = Field(None, ge=0.0, le=1.0)


class PWMConfig(StrictModel):
    """Configuration for one LabJack DIO_EF PWM output."""

    name: str
    dio: int = Field(..., ge=0)

    frequency_hz: float = Field(1000.0, gt=0.0)
    duty_cycle: float = Field(0.0, ge=0.0, le=1.0)
    enabled: bool = True

    schedules: list[PWMScheduleConfig] = Field(default_factory=list)

    # For LabJack T-series DIO_EF PWM clocking.
    # If None, hardware layer can calculate from frequency_hz.
    roll_value: Optional[int] = Field(None, ge=2)


class HumidityScheduleConfig(StrictModel):
    """Daily humidity target window.

    A schedule supplies the target RH and optional lower trigger. The runtime
    starts misting only below the trigger and then repeats short pulses until
    the target is reached or a safety limit stops the sequence.
    """

    name: str = "default"

    # Daily local-time window for this humidity target.
    # Windows may cross midnight, e.g. start_time=18:00, end_time=02:00.
    start_time: dt.time
    end_time: dt.time
    enabled: bool = True

    # Controller mists only when RH is below trigger_below_rh_pct.
    # Once misting starts, it pulses until target_rh_pct is reached,
    # subject to pulse, settling, and safety limits.
    target_rh_pct: float = Field(..., ge=0.0, le=100.0)
    trigger_below_rh_pct: Optional[float] = Field(None, ge=0.0, le=100.0)
    # Optional per-window override for HumidityControlConfig.max_pulse_s.
    max_pulse_s: Optional[float] = Field(None, gt=0.0)


class HumidityControlConfig(StrictModel):
    """Pulse-based humidity controller settings.

    The controller reads one RH sensor and drives one active-low relay. Timing
    and safety fields keep misting bounded and give the logger enough metadata
    to diagnose weak mist response or rapid drying.
    """

    name: str

    # Name of the RH sensor in LoggerConfig.sensors, e.g. "canopy".
    rh_sensor: str

    # Name of the relay that drives the mister. Relay logic remains
    # active-low for this setup: 0 = on, 1 = off.
    relay: str

    enabled: bool = True
    schedules: list[HumidityScheduleConfig] = Field(default_factory=list)

    # Safety and behavior tuning.
    max_pulse_s: float = Field(30.0, gt=0.0)
    settle_s: float = Field(60.0, ge=0.0)
    min_off_s: float = Field(15.0, ge=0.0)
    startup_grace_period_s: float = Field(180.0, ge=0.0)
    default_hysteresis_rh_pct: float = Field(2.0, ge=0.0, le=100.0)
    hard_max_rh_pct: float = Field(85.0, ge=0.0, le=100.0)
    max_consecutive_pulses: int = Field(8, ge=1)

    # Used only for diagnostics/logging. A small delta after a mist pulse
    # can indicate poor mixing, an empty mister, excess ventilation, or a
    # very dry system absorbing water quickly.
    low_response_delta_rh_pct: float = Field(0.5, ge=0.0, le=100.0)
    # Optional drying alarm. The alarm fires when RH is this far below target
    # and the falling-RH EMA exceeds the configured rate.
    drying_alarm_rate_rh_pct_per_hour: Optional[float] = Field(None, gt=0.0)
    drying_alarm_below_target_rh_pct: float = Field(5.0, ge=0.0, le=100.0)


# ----------------------------
# Sensor config
# ----------------------------

class DeviceTemperatureConfig(StrictModel):
    """Built-in LabJack device temperature sensor configuration."""

    sensor_type: Literal["device_temperature"] = "device_temperature"
    name: str = "labjack"


class T9602Config(StrictModel):
    """T9602 temperature/RH sensor configuration."""

    sensor_type: Literal["t9602"] = "t9602"
    name: str
    i2c: I2CConfig


class ThermistorConfig(StrictModel):
    """Analog thermistor input configuration.

    The runtime currently logs raw voltage only; the thermistor constants are
    kept here so resistance/temperature conversion can be added without
    changing the configuration schema.
    """

    sensor_type: Literal["thermistor"] = "thermistor"
    name: str

    ain: int = Field(..., ge=0)
    nominal_resistance_ohm: float = Field(10_000.0, gt=0.0)
    nominal_temperature_c: float = 25.0
    beta: Optional[float] = None
    pullup_resistance_ohm: Optional[float] = None


SensorConfig = Annotated[
    Union[
        DeviceTemperatureConfig,
        T9602Config,
        ThermistorConfig,
    ],
    Field(discriminator="sensor_type"),
]


# ----------------------------
# Top-level config
# ----------------------------

class LoggerConfig(StrictModel):
    """Top-level configuration used to assemble a complete logger runtime."""

    log_dir: Path = Path("LOGS")
    state_path: Optional[Path] = None
    sample_interval_s: float = Field(1.0, gt=0.0)

    device: LabjackConfig
    sensors: list[SensorConfig] = Field(default_factory=list)
    relays: list[RelayConfig] = Field(default_factory=list)
    pwm_outputs: list[PWMConfig] = Field(default_factory=list)
    humidity_controls: list[HumidityControlConfig] = Field(default_factory=list)


# ----------------------------
# State / reading models
# ----------------------------

class DeviceMeta(StrictModel):
    """Connection metadata captured in each log record."""

    device_type: str
    serial_number: Optional[str] = None
    connected: bool = False


class SensorReading(StrictModel):
    """One sensor sample and any raw/status data needed to debug it."""

    name: str
    sensor_type: str

    values: dict[str, Any] = Field(default_factory=dict)
    status: Optional[int] = None
    raw_data: Optional[list[Any]] = None


class ControlState(StrictModel):
    """Serializable snapshot of one control output or control loop."""

    name: str
    control_type: Literal["relay", "pwm", "humidity"]

    values: dict[str, Any] = Field(default_factory=dict)


class LogRecord(StrictModel):
    """One JSONL row written by the logger."""

    version: Literal["0.1"] = "0.1"

    timestamp: dt.datetime
    device: DeviceMeta

    sensors: list[SensorReading] = Field(default_factory=list)
    controls: list[ControlState] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
