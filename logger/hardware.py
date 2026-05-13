"""LabJack hardware adapters used by the environmental logger.

``LabjackDriver`` is the real LJM-backed implementation. ``MockLabjackDriver``
implements the same small interface in memory so smoke tests and local
development can exercise the runtime without a physical LabJack attached.
"""

from __future__ import annotations

from typing import Optional

from .models import LabjackConfig


def _clamp01(value: float) -> float:
    """Clamp a numeric duty-cycle-like value into the inclusive 0..1 range."""

    return max(0.0, min(1.0, value))


class LabjackDriver:
    """
    Hardware-facing LabJack driver.

    This class should know how to talk to the LabJack.
    It should not know your environmental control policy.
    """

    def __init__(self, config: LabjackConfig):
        """Store connection configuration and initialize driver state."""

        self.config = config

        self.handle: Optional[int] = None
        self.serial_number: Optional[str] = None
        self.connected: bool = False

        self._ljm = None
        self._pwm_roll_values: dict[int, int] = {}

    def connect(self) -> "LabjackDriver":
        """
        Lazily import labjack.ljm so smoke tests can run without the LabJack
        Python package installed.
        """
        from labjack import ljm

        self._ljm = ljm
        self.handle = ljm.openS(
            self.config.device_type,
            self.config.connection_type,
            self.config.serial_number,
        )

        info = ljm.getHandleInfo(self.handle)
        self.serial_number = str(info[2])
        self.connected = True

        self.configure_i2c_defaults()

        print(
            f"Connected to LabJack: "
            f"devType={info[0]}, connType={info[1]}, serial={info[2]}"
        )

        return self

    def close(self) -> None:
        """Close the LJM handle if connected and mark the driver disconnected."""

        if self.connected and self.handle is not None and self._ljm is not None:
            self._ljm.close(self.handle)

        self.connected = False
        self.handle = None

    def _require_connection(self) -> None:
        """Raise if an operation needs an open LabJack connection."""

        if not self.connected or self.handle is None or self._ljm is None:
            raise RuntimeError("LabJack is not connected.")

    def configure_i2c_defaults(self, speed: int = 65536) -> None:
        """Apply process-wide I2C defaults used before individual sensor reads."""

        self._require_connection()

        self._ljm.eWriteName(self.handle, "I2C_SPEED_THROTTLE", speed)
        self._ljm.eWriteName(self.handle, "I2C_OPTIONS", 0)

    def read_device_temperature_k(self) -> float:
        """Read the LabJack's internal device temperature in kelvin."""

        self._require_connection()

        return float(self._ljm.eReadName(self.handle, "TEMPERATURE_DEVICE_K"))

    def read_i2c(
        self,
        *,
        sda: int,
        scl: int,
        addr: int,
        rx_len: int,
    ) -> list[int]:
        """Perform a read-only I2C transaction and return received bytes.

        The logger config supplies the DIO pins, 7-bit address, and receive
        length for each sensor. This method intentionally does no sensor-level
        decoding; runtimes such as ``T9602Runtime`` interpret the returned
        bytes.
        """

        self._require_connection()

        self._ljm.eWriteName(self.handle, "I2C_SDA_DIONUM", sda)
        self._ljm.eWriteName(self.handle, "I2C_SCL_DIONUM", scl)
        self._ljm.eWriteName(self.handle, "I2C_SLAVE_ADDRESS", addr)
        self._ljm.eWriteName(self.handle, "I2C_NUM_BYTES_TX", 0)
        self._ljm.eWriteName(self.handle, "I2C_NUM_BYTES_RX", rx_len)
        self._ljm.eWriteName(self.handle, "I2C_GO", 1)

        raw = self._ljm.eReadNameByteArray(
            self.handle,
            "I2C_DATA_RX",
            rx_len,
        )

        return [int(x) for x in raw]

    def configure_pwm(
        self,
        *,
        dio: int,
        frequency_hz: float = 1000.0,
        duty_cycle: float = 0.0,
        roll_value: Optional[int] = None,
    ) -> None:
        """Configure a DIO_EF PWM output and apply its initial duty cycle."""

        self._require_connection()

        duty_cycle = _clamp01(duty_cycle)

        # LabJack T-series base clock assumption.
        # 80 MHz / 80_000 = 1 kHz.
        roll = roll_value or max(2, int(80_000_000 / frequency_hz))

        self._pwm_roll_values[dio] = roll

        self._ljm.eWriteName(self.handle, f"DIO{dio}_EF_ENABLE", 0)
        self._ljm.eWriteName(self.handle, "DIO_EF_CLOCK0_ENABLE", 0)

        self._ljm.eWriteName(self.handle, "DIO_EF_CLOCK0_DIVISOR", 1)
        self._ljm.eWriteName(self.handle, "DIO_EF_CLOCK0_ROLL_VALUE", roll)
        self._ljm.eWriteName(self.handle, "DIO_EF_CLOCK0_ENABLE", 1)

        self._ljm.eWriteName(self.handle, f"DIO{dio}_EF_INDEX", 0)
        self._ljm.eWriteName(self.handle, f"DIO{dio}_EF_OPTIONS", 0)
        self._ljm.eWriteName(
            self.handle,
            f"DIO{dio}_EF_CONFIG_A",
            int(duty_cycle * roll),
        )

        self._ljm.eWriteName(self.handle, f"DIO{dio}_EF_ENABLE", 1)

    def set_pwm(self, *, dio: int, duty_cycle: float) -> None:
        """Update the duty cycle for a previously configured PWM output."""

        self._require_connection()

        duty_cycle = _clamp01(duty_cycle)
        roll = self._pwm_roll_values.get(dio, 80_000)

        self._ljm.eWriteName(
            self.handle,
            f"DIO{dio}_EF_CONFIG_A",
            int(duty_cycle * roll),
        )

    def set_relay(self, *, dio: int, state: int) -> None:
        """Write a digital output used by a relay board.

        The caller owns the active-low policy. This low-level method simply
        normalizes truthy values to ``1`` and falsey values to ``0`` before
        writing the DIO register.
        """

        self._require_connection()

        state = 1 if state else 0
        self._ljm.eWriteName(self.handle, f"DIO{dio}", state)

    def read_ain(self, *, ain: int) -> float:
        """Read one analog input channel and return volts."""

        self._require_connection()

        return float(self._ljm.eReadName(self.handle, f"AIN{ain}"))


class MockLabjackDriver(LabjackDriver):
    """
    No-hardware fake LabJack for smoke tests.

    This lets you validate config parsing, runtime assembly, sensor decoding,
    control snapshots, and JSONL writing without touching real hardware.
    """

    def __init__(self, config: LabjackConfig):
        """Create an in-memory driver with deterministic default readings."""

        super().__init__(config)

        self.relay_states: dict[int, int] = {}
        self.pwm_states: dict[int, float] = {}
        self.i2c_read_count: int = 0
        self.mock_rh_fraction: float = 0.52
        self.mock_temp_c: float = 23.0

    def connect(self) -> "MockLabjackDriver":
        """Pretend to open a LabJack connection."""

        self.handle = 1
        self.serial_number = "MOCK-LJ-0001"
        self.connected = True

        print("Connected to mock LabJack.")

        return self

    def close(self) -> None:
        """Mark the mock driver disconnected."""

        self.connected = False
        self.handle = None

    def configure_i2c_defaults(self, speed: int = 65536) -> None:
        """Accept I2C default configuration without touching hardware."""

        return None

    def read_device_temperature_k(self) -> float:
        """Return a stable fake device temperature in kelvin."""

        return 296.15

    def read_i2c(
        self,
        *,
        sda: int,
        scl: int,
        addr: int,
        rx_len: int,
    ) -> list[int]:
        """Return encoded T9602-style bytes for the configured mock RH/temp."""

        self.i2c_read_count += 1

        # Slight variation so repeated reads are not identical.
        temp_c = self.mock_temp_c + (self.i2c_read_count * 0.1)
        rh_fraction = self.mock_rh_fraction

        data = self._encode_t9602_bytes(
            rh_fraction=rh_fraction,
            temp_c=temp_c,
            status=0,
        )

        return data[:rx_len]

    def configure_pwm(
        self,
        *,
        dio: int,
        frequency_hz: float = 1000.0,
        duty_cycle: float = 0.0,
        roll_value: Optional[int] = None,
    ) -> None:
        """Record PWM configuration in memory."""

        roll = roll_value or max(2, int(80_000_000 / frequency_hz))

        self._pwm_roll_values[dio] = roll
        self.pwm_states[dio] = _clamp01(duty_cycle)

    def set_pwm(self, *, dio: int, duty_cycle: float) -> None:
        """Record a PWM duty-cycle update in memory."""

        self.pwm_states[dio] = _clamp01(duty_cycle)

    def set_relay(self, *, dio: int, state: int) -> None:
        """Record a relay DIO state in memory."""

        self.relay_states[dio] = 1 if state else 0

    def read_ain(self, *, ain: int) -> float:
        """Return a deterministic fake analog input voltage."""

        # Fake voltage for future thermistor smoke tests.
        return 1.234

    @staticmethod
    def _encode_t9602_bytes(
        *,
        rh_fraction: float,
        temp_c: float,
        status: int = 0,
    ) -> list[int]:
        """Encode RH and temperature using the T9602 4-byte output format."""

        rh_fraction = _clamp01(rh_fraction)

        rh_raw = int(rh_fraction * 16384)
        rh_raw = max(0, min(16383, rh_raw))

        temp_raw = int(((temp_c + 40.0) / 165.0) * 16384)
        temp_raw = max(0, min(16383, temp_raw))

        b0 = ((status & 0x03) << 6) | ((rh_raw >> 8) & 0x3F)
        b1 = rh_raw & 0xFF
        b2 = (temp_raw >> 6) & 0xFF
        b3 = (temp_raw & 0x3F) << 2

        return [b0, b1, b2, b3]
