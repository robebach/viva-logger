# import datetime as dt
# from pathlib import Path
# from typing import Literal, Optional, Annotated, Union

# from pydantic import BaseModel, Field, PrivateAttr


# # ----------------------------
# # Config models
# # ----------------------------

# class LabjackConfig(BaseModel):
#     device_type: Literal["T4", "T7", "ANY"] = "ANY"
#     serial_number: str = "ANY"
#     connection_type: str = "ANY"


# class I2CConfig(BaseModel):
#     sda_dio: int
#     scl_dio: int
#     address: int
#     rx_len: int = 4
#     speed: int = 65536


# class PWMConfig(BaseModel):
#     name: str
#     dio: int
#     frequency_hz: float = 1000
#     duty_cycle: float = Field(0.0, ge=0.0, le=1.0)
#     enabled: bool = True


# class RelayConfig(BaseModel):
#     name: str
#     dio: int
#     initial_state: Literal[0, 1] = 0
#     cycle_times: list[dt.time] = Field(default_factory=list)
#     cycle_duration_s: float = 30


# class LoggerConfig(BaseModel):
#     log_dir: Path = Path("LOGS")
#     sample_interval_s: float = 1.0
#     device: LabjackConfig
#     sensors: list["SensorConfig"] = Field(default_factory=list)
#     relays: list[RelayConfig] = Field(default_factory=list)
#     pwm_outputs: list[PWMConfig] = Field(default_factory=list)


# # ----------------------------
# # Sensor config models
# # ----------------------------

# class DeviceTemperatureConfig(BaseModel):
#     sensor_type: Literal["device_temperature"] = "device_temperature"
#     name: str = "labjack"


# class T9602Config(BaseModel):
#     sensor_type: Literal["t9602"] = "t9602"
#     name: str
#     i2c: I2CConfig


# class ThermistorConfig(BaseModel):
#     sensor_type: Literal["thermistor"] = "thermistor"
#     name: str
#     ain: int
#     nominal_resistance_ohm: float = 10_000
#     beta: Optional[float] = None


# SensorConfig = Annotated[
#     Union[
#         DeviceTemperatureConfig,
#         T9602Config,
#         ThermistorConfig,
#     ],
#     Field(discriminator="sensor_type"),
# ]


# # ----------------------------
# # Runtime / hardware objects
# # ----------------------------

# class LabjackRuntime(BaseModel):
#     config: LabjackConfig
#     handle: Optional[int] = None
#     connected: bool = False


# class SensorRuntime(BaseModel):
#     name: str
#     sensor_type: str
#     status: Optional[int] = None
#     raw_data: Optional[list] = None

#     _device: Optional[LabjackRuntime] = PrivateAttr(default=None)


# class RelayRuntime(BaseModel):
#     config: RelayConfig
#     state: Literal[0, 1] = 0

#     _device: Optional[LabjackRuntime] = PrivateAttr(default=None)


# class PWMRuntime(BaseModel):
#     config: PWMConfig
#     duty_cycle: float = Field(0.0, ge=0.0, le=1.0)
#     enabled: bool = True

#     _device: Optional[LabjackRuntime] = PrivateAttr(default=None)


# # ----------------------------
# # Reading / log models
# # ----------------------------

# class SensorReading(BaseModel):
#     name: str
#     sensor_type: str
#     values: dict
#     status: Optional[int] = None
#     raw_data: Optional[list] = None


# class ControlState(BaseModel):
#     name: str
#     control_type: Literal["relay", "pwm"]
#     values: dict


# class LogRecord(BaseModel):
#     version: Literal["0.1"] = "0.1"
#     timestamp: dt.datetime
#     device_serial_number: Optional[str] = None
#     sensors: list[SensorReading] = Field(default_factory=list)
#     controls: list[ControlState] = Field(default_factory=list)
#     errors: list[str] = Field(default_factory=list)


# # ----------------------------
# # Orchestration
# # ----------------------------

# class AcquisitionManager(BaseModel):
#     sensors: list[SensorRuntime] = Field(default_factory=list)


# class ControlManager(BaseModel):
#     relays: list[RelayRuntime] = Field(default_factory=list)
#     pwm_outputs: list[PWMRuntime] = Field(default_factory=list)


# class LogWriter(BaseModel):
#     log_dir: Path = Path("LOGS")
#     current_path: Optional[Path] = None


# class Scheduler(BaseModel):
#     sample_interval_s: float = 1.0
#     next_sample_at: Optional[float] = None


# class SystemRuntime(BaseModel):
#     config: LoggerConfig
#     device: LabjackRuntime
#     acquisition: AcquisitionManager
#     controls: ControlManager
#     logger: LogWriter
#     scheduler: Scheduler
    
# # from __future__ import annotations

# # import time
# # from typing import Annotated, Literal

# # from typing_extensions import Self

# # from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, computed_field
# # from labjack import ljm


# # # ----------------------------
# # # Shared model config
# # # ----------------------------

# # class AppModel(BaseModel):
# #     model_config = ConfigDict(
# #         extra="forbid",
# #         validate_assignment=True,
# #         str_strip_whitespace=True,
# #     )


# # DIOPin = Annotated[int, Field(ge=0, le=199)]
# # DutyCycle = Annotated[float, Field(ge=0.0, le=1.0)]


# # # ----------------------------
# # # LabJack wrapper
# # # ----------------------------

# # class Labjack(AppModel):
# #     device_type: Literal["T4", "T7", "ANY"] = "T4"
# #     conn_type: Literal["ANY", "USB", "ETHERNET", "WIFI"] = "ANY"
# #     sn: str = "ANY"

# #     _handle: int | None = PrivateAttr(default=None)

# #     @computed_field
# #     @property
# #     def connected(self) -> bool:
# #         return self._handle is not None

# #     @property
# #     def handle(self) -> int:
# #         if self._handle is None:
# #             raise RuntimeError("LabJack is not connected. Call connect() first.")
# #         return self._handle

# #     def connect(self) -> Self:
# #         if self._handle is not None:
# #             return self

# #         self._handle = ljm.openS(self.device_type, self.conn_type, self.sn)

# #         info = ljm.getHandleInfo(self._handle)
# #         self.sn = str(info[2])

# #         print(f"Connected to LabJack: devType={info[0]}, connType={info[1]}, serial={info[2]}")
# #         return self

# #     def close(self) -> None:
# #         if self._handle is None:
# #             return

# #         try:
# #             ljm.close(self._handle)
# #         finally:
# #             self._handle = None
# #             print("LabJack closed.")

# #     def configure_pwm(
# #         self,
# #         *,
# #         dio_pin: DIOPin,
# #         duty_cycle: DutyCycle,
# #         roll_value: int = 80000,
# #     ) -> None:
# #         """
# #         Configure PWM on a DIO pin.

# #         For T4/T7:
# #             80 MHz / 80000 = 1000 Hz PWM frequency
# #         """
# #         if roll_value < 2:
# #             raise ValueError("roll_value must be >= 2.")

# #         config_a = int(duty_cycle * roll_value)

# #         # Disable the DIO extended feature and Clock0 before changing PWM settings.
# #         ljm.eWriteName(self.handle, f"DIO{dio_pin}_EF_ENABLE", 0)
# #         ljm.eWriteName(self.handle, "DIO_EF_CLOCK0_ENABLE", 0)

# #         # Configure Clock0.
# #         ljm.eWriteName(self.handle, "DIO_EF_CLOCK0_DIVISOR", 1)
# #         ljm.eWriteName(self.handle, "DIO_EF_CLOCK0_ROLL_VALUE", roll_value)
# #         ljm.eWriteName(self.handle, "DIO_EF_CLOCK0_ENABLE", 1)

# #         # Configure this DIO pin for PWM Out.
# #         ljm.eWriteName(self.handle, f"DIO{dio_pin}_EF_INDEX", 0)

# #         # Your existing script uses DIO#_EF_OPTIONS for Clock0 selection.
# #         # LabJack docs now call this DIO#_EF_CLOCK_SOURCE, but EF_OPTIONS is the old/common name.
# #         # Try the newer name first; fall back to the older one if needed.
# #         try:
# #             ljm.eWriteName(self.handle, f"DIO{dio_pin}_EF_CLOCK_SOURCE", 0)
# #         except Exception:
# #             ljm.eWriteName(self.handle, f"DIO{dio_pin}_EF_OPTIONS", 0)

# #         ljm.eWriteName(self.handle, f"DIO{dio_pin}_EF_CONFIG_A", config_a)
# #         ljm.eWriteName(self.handle, f"DIO{dio_pin}_EF_ENABLE", 1)

# #     def set_pwm(
# #         self,
# #         *,
# #         dio_pin: DIOPin,
# #         duty_cycle: DutyCycle,
# #         roll_value: int = 80000,
# #     ) -> None:
# #         """
# #         Update PWM duty cycle after configure_pwm() has already been called.
# #         """
# #         if roll_value < 2:
# #             raise ValueError("roll_value must be >= 2.")

# #         config_a = int(duty_cycle * roll_value)
# #         ljm.eWriteName(self.handle, f"DIO{dio_pin}_EF_CONFIG_A", config_a)


# # # ----------------------------
# # # PWM output model
# # # ----------------------------

# # class PWMOutput(AppModel):
# #     name: str = "LED_dimmer"
# #     dio: DIOPin = 6

# #     duty_cycle: DutyCycle = 0.05
# #     min_duty_cycle: DutyCycle = 0.04
# #     roll_value: int = Field(80000, ge=2)

# #     _device: Labjack | None = PrivateAttr(default=None)
# #     _state: float = PrivateAttr(default=0.0)

# #     def model_post_init(self, __context) -> None:
# #         self._state = self._clamp(self.duty_cycle)

# #     @property
# #     def state(self) -> float:
# #         return self._state

# #     @computed_field
# #     @property
# #     def brightness_percent(self) -> float:
# #         return self._state * 100.0

# #     def _clamp(self, value: float) -> float:
# #         """
# #         Brightness behavior:
# #             0.0  -> real off
# #             0.001 through min_duty_cycle -> min_duty_cycle
# #             above min_duty_cycle -> requested value
# #             above 1.0 -> 1.0
# #         """
# #         value = float(value)

# #         if value <= 0.0:
# #             return 0.0

# #         if value > 1.0:
# #             return 1.0

# #         return max(self.min_duty_cycle, value)

# #     def bind(self, device: Labjack) -> Self:
# #         self._device = device

# #         self._device.configure_pwm(
# #             dio_pin=self.dio,
# #             duty_cycle=self._state,
# #             roll_value=self.roll_value,
# #         )

# #         return self

# #     def set_duty(self, duty_cycle: float) -> None:
# #         self._state = self._clamp(duty_cycle)

# #         if self._device is None:
# #             return

# #         self._device.set_pwm(
# #             dio_pin=self.dio,
# #             duty_cycle=self._state,
# #             roll_value=self.roll_value,
# #         )

# #     def off(self) -> None:
# #         self.set_duty(0.0)


# # # ----------------------------
# # # Smoke test app
# # # ----------------------------
# # class Vivarium(AppModel):
# #     device: Labjack = Field(default_factory=Labjack)
# #     light: PWMOutput = Field(default_factory=PWMOutput)
# #     mister: R
# # class LightSmokeTest(AppModel):
# #     device: Labjack = Field(default_factory=Labjack)
# #     light: PWMOutput = Field(default_factory=PWMOutput)

# #     def run(self) -> None:
# #         try:
# #             self.device.connect()
# #             self.light.bind(self.device)

# #             print(
# #                 f"{self.light.name} on DIO{self.light.dio} set to "
# #                 f"{self.light.brightness_percent:.1f}% brightness."
# #             )
# #             print(
# #                 f"Minimum nonzero brightness is "
# #                 f"{self.light.min_duty_cycle * 100:.1f}%."
# #             )
# #             print("Press Ctrl+C to turn the light off and close the LabJack.")

# #             while True:
# #                 time.sleep(1)

# #         except KeyboardInterrupt:
# #             print("\nTurning light off.")

# #         finally:
# #             try:
# #                 if self.light._device is not None:
# #                     self.light.off()
# #                     print("Light off.")
# #             finally:
# #                 self.device.close()


# # # ----------------------------
# # # Main
# # # ----------------------------

# # if __name__ == "__main__":
# #     config = {
# #         "device": {
# #             "device_type": "T7",
# #             "conn_type": "ANY",
# #             "sn": "ANY",
# #         },
# #         "light": {
# #             "name": "LED_dimmer",
# #             "dio": 0,
# #             "duty_cycle": 0.05,
# #             "min_duty_cycle": 0.04,
# #             "roll_value": 80000,
# #         },
# #     }

# #     app = LightSmokeTest.model_validate(config)
# #     app.run()