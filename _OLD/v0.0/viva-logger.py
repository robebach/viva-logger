from abc import ABC, abstractmethod
# from datetime import datetime, date, timedelta, timezone
import datetime as dt
import time
from pathlib import Path
import json
from typing import Annotated, Union, Any, Dict, List, Literal, Optional
from typing_extensions import Self

from pydantic import BaseModel, Field, ConfigDict, field_validator, model_validator, computed_field, PrivateAttr
import numpy as np
from labjack import ljm

LOG_DIR = Path("./LOGS")          # existing directory

# ----------------------------
# Core metadata
# ----------------------------

def pws(tK):

    """
    Saturation Pressure Calculation. Constants taken from ASHRAE2005 pp6.2.

    Parameters:
        tK : number or np.ndarray
             temperatures in Kelvin(K)

    Raises:
        Value Error

    Returns:
        A : float or np.ndarray
            Saturation Pressure (Pa)
    """

    C_8  = -5.8002206E+3
    C_9  =  1.3914993E+0
    C_10 = -4.8640239E-2
    C_11 =  4.1764768E-5
    C_12 = -1.4452093E-8
    C_13 =  6.5459673E+0
        
    A = np.exp( C_8/tK + C_9 + C_10*tK + C_11*pow(tK,2) + C_12*pow(tK,3) + C_13*np.log(tK) )
        
    return A

class PWM(BaseModel):
    name: str
    dio: int = 17
    duty_cycle: float = Field(0.0, ge=0.0, le=1.0)

    _state: float = PrivateAttr(0.0)

    @property
    def state(self) -> float:
        return self._state
    
    @state.setter
    def state(self, new_state:float):
        if new_state != self._state:
            if new_state < 0:
                new_state = 0
            elif new_state > 1:
                new_state = 1

            self._state = new_state
            self._device.set_pwm(dio_pin=self.dio, duty_cycle=self._state)

class Relay(BaseModel):
    name: str
    dio: int = 13
    # cycle_times: list[dt.time] = Field(default_factory=list)
    cycle_duration: float = 30 #seconds
    on_state: Literal[0,1] = 0
    off_state: Literal[0,1] = 1

    _state: Literal[0,1] = PrivateAttr(0)

    # _device: Optional['Labjack']

    # @model_validator(mode="after")
    # def state_init(self) -> None:
    #     if self._device:
    #         self._device.set_relay(dio=self.dio, setting=self._state)

    @property
    def state(self) -> Literal[0,1]:
        return self._state

    @state.setter
    def state(self, new_state:Literal[0,1]):
        self._state = new_state
        self._device.set_relay(dio=self.dio, setting=self._state)

    def cycle(self):
        self.state = 1 - self.state

    def turn_on(self):
        self.state = self.on_state

    def turn_off(self):
        self.state = self.off_state

# class DailyAction(BaseModel):
#     name: str
#     time: dt.time
#     mist_duration: float = Field(30, gt=0) #seconds
#     light_duty_cycle: float = Field(..., ge=0.0, le=1.0)
    
class Sensor(BaseModel):
    name: str
    status: int = None
    data: list = Field(None, description="The current RAW data from the sensor")
    _device: Optional['Labjack']

    # _reading: list['Reading'] = []

    @abstractmethod
    def read(self):
        pass

class Thermistor(Sensor):
    sensor_type: Literal["10K"]
    name: str

    def read(self) -> None:
        pass

class Units():

    @classmethod
    def c2f(cls, c: float) -> float:
        return c*9/5+32

class deviceT(Sensor):
    sensor_type: Literal["deviceT"]

    def read(self) -> dict:
        self.data = self._device.tK
        return {"TF":(self.data - 273.15) * 9/5 + 32}
   
class t6902(Sensor):
    sensor_type: Literal["t6902"]
    protocol: str = Literal["i2c"]
    dio_sda: int = 8
    dio_scl: int = 9
    addr: int = 0x28
    rx_len: int = 4
    
    @computed_field
    def rh(self) -> float:
        '''humidity fraction'''
        rh_raw = ((self.data[0] & 0x3F) << 8) | self.data[1]
        return (rh_raw / 16384.0)

    @computed_field
    def tC(self) -> float:
        '''dry-buld temperature (C) from i2c bits'''
        t_raw = (self.data[2] << 6) | (self.data[3] >> 2)
        return (t_raw / 16384.0) * 165.0 - 40.0

    @computed_field
    def tF(self) -> float:
        '''dry-buld temperature (F)'''
        return self.tC * (9/5) + 32


    @computed_field
    def dpC(self)->float:
        '''dew point in C per the current temp (C) and rh (frc)'''
        c_14 = 6.54
        c_15 = 14.526
        c_16 = 0.7389
        c_17 = 0.09486
        c_18 = 0.4569

        tK = self.tC + 273.15
        Pws = pws(tK)
        Pw = Pws*self.rh*0.001 #Pa -> kPa
        a  = np.log(Pw) #natural log of Pw

        return c_14 + c_15*a + c_16*np.power(a,2) + c_17*np.power(a,3) + np.power(Pw,0.1984)*c_18
    
    @computed_field
    def dpF(self)->float:
        
        return Units.c2f(self.dpC)

    def read(self) -> dict:

        self.data = self._device.read_i2c(sda=self.dio_sda, scl=self.dio_scl, addr=self.addr, rx_len=self.rx_len)

        self.status = (self.data[0] >> 6) & 0x03
        
        return {"RH":self.rh, "TF":self.tF, "DPF":self.dpF}

class I2cConfig(BaseModel):
    sda_dio: int = 8
    scl_dio: int = 9
    speed: int = 65536

class Labjack(BaseModel):
    handle: int = None
    device_type: Literal['T4','T7','ANY'] = 'ANY'
    sn: Optional[str] = "ANY"
    conn_type: Optional[str] = "ANY"
    conn_status: bool = False

    # i2c: I2cConfig = I2cConfig() # 
    @model_validator(mode="after")
    def connect(self):
        self.handle = ljm.openS(self.device_type, self.conn_type, self.sn)

        info = ljm.getHandleInfo(self.handle)
        self.sn = info[2]
        print(f"Connected to: devType={info[0]}, connType={info[1]}, serial={info[2]}")

        self.conn_status = True

        self.config_i2c()
        
        return self
    
    @computed_field
    def tK(self) -> float:
        return ljm.eReadName(self.handle, "TEMPERATURE_DEVICE_K")

    def config_i2c(self):
        ljm.eWriteName(self.handle, "I2C_SPEED_THROTTLE", 65536)
        ljm.eWriteName(self.handle, "I2C_OPTIONS", 0)

        return self
    
    def configure_pwm(self, dio_pin:int, duty_cycle:float = 0.0, roll_value:int = 80000):
        # 1. Disable the DIO feature and the clock first to avoid conflict errors
        ljm.eWriteName(self.handle, f"DIO{dio_pin}_EF_ENABLE", 0)
        ljm.eWriteName(self.handle, "DIO_EF_CLOCK0_ENABLE", 0)

        # 2. Configure the Clock (Clock0)
        # For T7/T4 (80MHz base): 80,000,000 / 80,000 = 1,000 Hz
        roll_value = 80000 
        ljm.eWriteName(self.handle, "DIO_EF_CLOCK0_DIVISOR", 1)
        ljm.eWriteName(self.handle, "DIO_EF_CLOCK0_ROLL_VALUE", roll_value)
        ljm.eWriteName(self.handle, "DIO_EF_CLOCK0_ENABLE", 1)

        # 3. Configure the DIO Pin
        ljm.eWriteName(self.handle, f"DIO{dio_pin}_EF_INDEX", 0)      # PWM Out index
        ljm.eWriteName(self.handle, f"DIO{dio_pin}_EF_OPTIONS", 0)     # MUST link to Clock0
        ljm.eWriteName(self.handle, f"DIO{dio_pin}_EF_CONFIG_A", int(duty_cycle * roll_value))
        
        # 4. Finally, enable the feature
        ljm.eWriteName(self.handle, f"DIO{dio_pin}_EF_ENABLE", 1)

        # print(f"PWM enabled on {dio_pin} at 1kHz with {duty_cycle*100}% duty cycle.")
    def set_pwm(self, dio_pin: int, duty_cycle:float = 1.0, roll_value:int = 80000):
        ljm.eWriteName(self.handle, f"DIO{dio_pin}_EF_CONFIG_A", int(duty_cycle * roll_value))

    def read_pwm(self, dio_pin: int):
        ljm.eReadName(self.handle, f"DIO{dio_pin}_EF_CONFIG_A")
        
    def read_i2c(self, sda:int, scl:int, addr: int, rx_len: int):
        ljm.eWriteName(self.handle, "I2C_SDA_DIONUM", sda)
        ljm.eWriteName(self.handle, "I2C_SCL_DIONUM", scl)
        ljm.eWriteName(self.handle, "I2C_SLAVE_ADDRESS", addr)
        ljm.eWriteName(self.handle, "I2C_NUM_BYTES_TX", 0)
        ljm.eWriteName(self.handle, "I2C_NUM_BYTES_RX", rx_len)
        ljm.eWriteName(self.handle, "I2C_GO", 1)

        return ljm.eReadNameByteArray(self.handle, "I2C_DATA_RX", rx_len)
    
    def set_relay(self, dio:int, setting: int=0):
        ljm.eWriteName(self.handle, f"DIO{dio:0>2}", setting)
        # ljm.eWriteName(self.handle, "DIO13", 1)

discriminated_sensors = Annotated[Union[t6902, deviceT], Field(discriminator="sensor_type")]

def log_path() -> Path:
    # Day-start file name, local date: LOGS/YYYY-MM-DD.jsonl
    today = dt.date.today()
    return LOG_DIR / f"{today.isoformat()}.jsonl"

class LoggerConfig(BaseModel):
    pass
class LoggerRuntime(BaseModel):
    pass

class Logger(BaseModel):
    device: Labjack
    htz: float = Field(1, description="The minimum read frequency, in seconds")
    sensors: list[discriminated_sensors] = Field([deviceT], description="The list of Sensors")
    relays: list[Relay] = Field([], description="The list of Relays")
    pwm: PWM = Field([], description="PWM output")

    sunrise: dt.time = dt.time(6,0)
    sunset: dt.time = dt.time(18,0) 
    twilight: int = 30 # minutes

    sun_brightness: float = 0.4
    moon_brightness: float = 0.00

    mist_duration: int = Field(15, description="Seconds to run mist after crossing a setpoint")
    # mist_duration_min: int = Field(15, description="Seconds to run mist below min setpoint")

    # daily_actions: list[DailyAction] = Field(default_factory=list, description="Scheduled mist/light changes")
    
    @model_validator(mode="after")
    def associate_sensors(self) -> Self:
        for sensor in self.sensors:
            sensor._device = self.device
        for relay in self.relays:
            relay._device = self.device
            relay.turn_off()
        self.pwm._device = self.device
        self.pwm.state = self.sun_brightness * self.daytime

        self.device.configure_pwm(self.pwm.dio, duty_cycle=self.pwm.state)

        return self

    @computed_field
    def daytime(self) -> bool:
        return self.sunrise <= dt.datetime.now().time() <= self.sunset
        
    def set_mister(self, on: bool) -> None:
        for relay in self.relays:
            if on:
                relay.turn_on()
            else:
                relay.turn_off()

    def run(self, htz=1, cycle_runtime=30, cycle_delay=10) -> None:
        '''Begin reading and writing sensors in the config, at the given frequency (htz)
        Logs to a file per the log_path() function that sets the name by the date'''
        current_path = log_path()
        f = open(current_path, "a", buffering=1)

        print(f"Streaming JSONL to: {current_path}  (Ctrl+C to stop)")

        try:
            next_tick = time.monotonic()
            light_timer = time.monotonic()

            light_setpoint = self.sun_brightness * self.daytime

            mist_timer = time.monotonic()

            misting_sensors = ["low-rh", "top-rh"]
            
            self.set_mister(False)

            while True: 
                now = dt.datetime.now()
                # monotonic_now = time.monotonic()

                self.pwm.state = self.sun_brightness * self.daytime + self.moon_brightness
                
                top_rh = next(sensor.read()["RH"] for sensor in self.sensors if sensor.name == "top-rh")
                low_rh = next(sensor.read()["RH"] for sensor in self.sensors if sensor.name == "low-rh")

                if self.daytime:
                    top_trigger = 0.42
                    low_wet_limit = 0.82
                else:
                    top_trigger = 0.55
                    low_wet_limit = 0.86

                if top_rh < top_trigger and low_rh < low_wet_limit:
                    self.set_mister(True)
                    mist_timer = time.monotonic() + self.mist_duration

                # mist_rh = [sensor.read()['RH'] for sensor in self.sensors if sensor.name in misting_sensors]
                # rh_ave = np.average(mist_rh)
                # rh_min = np.min(mist_rh)
                # rh_max = np.max(mist_rh)
                
                if mist_timer - time.monotonic() <= 0:
                    self.set_mister(False)

                # mist_max_min = 0.70
                # mist_ave_min = 0.60
                # mist_max_max = 0.7
                # mist_min_min = 0.3

                # if self.daytime:
                #     mist_max_max = 0.7
                #     mist_max_min = 0.50
                #     mist_ave_min = 0.40
                
                # # daytime schedule
                # # reasons to turn on the mister, by adding time to the mist timer
                # if rh_max < mist_max_max: #don't turn on the mister if the max humidity is over the max limit
                #     if rh_ave < mist_ave_min: #humidity <= setpoints, turn on for cycle time
                #         self.set_mister(True)
                #         mist_timer = time.monotonic() + self.mist_duration
                #     elif rh_max < mist_max_min:
                #         self.set_mister(True)
                #         mist_timer = time.monotonic() + self.mist_duration

                # roll file at local midnight
                new_path = log_path()
                if new_path != current_path:
                    f.close()
                    current_path = new_path
                    f = open(current_path, "a", buffering=1)
                    print(f"New day → now streaming to: {current_path}")

                if next_tick - time.monotonic() <= 0:
                    next_tick += self.htz

                    now_iso = now.isoformat(timespec="seconds")
                    sensor_readings = {sensor.name:sensor.read() for sensor in self.sensors}
                    rh_readings = []
                    for sensor_name, readings in sensor_readings.items():
                        if "RH" in readings:
                            rh_readings.append(readings["RH"])
                    log = {
                        
                        now_iso: {"average_rh":np.average(rh_readings)} 
                        | sensor_readings
                        | {"daytime":self.daytime}
                        | {relay.name:{"onoff":{1:"off",0:"on"}[relay.state]} for relay in self.relays}
                        | {self.pwm.name:{"duty_cycle":self.pwm.state}}

                    }
                    f.write(json.dumps(log, default=lambda obj: str(obj)) + "\n")
                    f.flush()

                time.sleep(1) # sample rate

                

        except KeyboardInterrupt:
            print("\nStopping.")
        finally:
            try:
                self.set_mister(False)
            finally:
                f.close()
                ljm.close(self.device.handle)


if __name__ == "__main__":
    logger_config =  {"device":{"device_type":"T4"},
                      "sun_brightness":0.35,

                      "htz":10, # log frequency in seconds
                      "sensors":[ # sensor list
                          {"sensor_type":"deviceT", "name":"labjack"},
                        #   {"sensor_type":"t6902", "name":"office", "dio_sda":0,"dio_scl":1},
                          {"sensor_type":"t6902", "name":"low-rh", "dio_sda":8,"dio_scl":9},
                          {"sensor_type":"t6902", "name":"top-rh", "dio_sda":10,"dio_scl":11}
                        ],
                        "relays":[
                            {"name":"mist","dio":12, "on_state":0, "off_state":1},
                            # {"name":"B","dio":13, "on_state":0, "off_state":1}
                        ], 
                        "pwm": {"name":"LED_dimmer", "dio":6}
                    }
    
    logger = Logger(**logger_config)
    
    print(logger.run())
    
    
# 	def connect(self):
#         info = ljm.getHandleInfo(handle)
# # ----------------------------
# # Sensor models
# # ----------------------------

# class T9602Raw(BaseModel):
#     b: List[int] = Field(..., min_length=4, max_length=4, description="Raw 4 bytes from I2C_DATA_RX")
#     rh_raw14: int = Field(..., ge=0, le=16383)
#     t_raw14: int = Field(..., ge=0, le=16383)

#     @field_validator("b")
#     @classmethod
#     def _bytes_0_255(cls, v: List[int]) -> List[int]:
#         for x in v:
#             if x < 0 or x > 255:
#                 raise ValueError("Each raw byte must be in [0,255]")
#         return v


# class T9602Reading(BaseModel):
#     ok: bool = True
#     i2c_addr_7bit: Optional[int] = Field(default=None, ge=0, le=0x7F)
#     status_bits: Optional[int] = Field(default=None, ge=0, le=3, description="0=valid, 1=stale, 2=command, 3=unused")
#     rh_pct: Optional[float] = Field(default=None, ge=0.0, le=100.0)
#     temp_c: Optional[float] = None
#     raw: Optional[T9602Raw] = None
#     err: Optional[str] = None


# class ThermistorReading(BaseModel):
#     ain: int = Field(..., ge=0, le=199, description="AIN channel index used")
#     v: float = Field(..., ge=0.0, description="Measured analog voltage at AIN")
#     r_ohm: Optional[float] = Field(default=None, gt=0.0, description="Computed resistance in ohms")
#     temp_c: Optional[float] = Field(default=None, description="Computed temperature in °C")
#     err: Optional[str] = None


# class ThermistorPair(BaseModel):
#     a: ThermistorReading
#     b: ThermistorReading


# class SensorsBlock(BaseModel):
#     t9602: Optional[T9602Reading] = None
#     thermistors: Optional[ThermistorPair] = None
#     extra: Dict[str, Any] = Field(default_factory=dict, description="Room for future sensors without schema break")


# # ----------------------------
# # Controls models
# # ----------------------------

# class BlowerPWMControl(BaseModel):
#     dio: int = Field(..., ge=0, le=199)
#     freq_hz: float = Field(..., gt=0)
#     roll: int = Field(..., ge=2)
#     duty: float = Field(..., ge=0.0, le=1.0)
#     enabled: bool = True


# class SolenoidControl(BaseModel):
#     dio: int = Field(..., ge=0, le=199)
#     on: bool = False


# class LightControl(BaseModel):
#     # Keep this vague until you pick a driver/dimming method.
#     enabled: bool = False
#     mode: Optional[Literal["none", "pwm", "0-10v", "resistance", "manual"]] = "none"
#     level: Optional[float] = Field(default=None, ge=0.0, le=1.0, description="Normalized 0..1 dim level")


# class ControlsBlock(BaseModel):
#     blower: Optional[BlowerPWMControl] = None
#     solenoid: Optional[SolenoidControl] = None
#     lights: Optional[LightControl] = None


# # ----------------------------
# # Top-level log record
# # ----------------------------

# class VivariumLogRecordV1(BaseModel):
#     model_config = ConfigDict(extra="forbid")

#     version: Literal["0.1"] = "0.1"

#     ts: datetime = Field(..., description="UTC timestamp (timezone-aware)")
#     ts_unix: float = Field(..., description="Unix epoch seconds (float)")

#     device: DeviceMeta
#     errors: List[str] = Field(default_factory=list)

#     sensors: SensorsBlock
#     controls: ControlsBlock

#     @field_validator("ts")
#     @classmethod
#     def _ts_must_be_aware_utc(cls, v: datetime) -> datetime:
#         if v.tzinfo is None or v.utcoffset() is None:
#             raise ValueError("ts must be timezone-aware")
#         # normalize to UTC to keep logs consistent
#         return v.astimezone(timezone.utc)


# def make_record(
#     *,
#     device: DeviceMeta,
#     sensors: SensorsBlock,
#     controls: ControlsBlock,
#     errors: Optional[List[str]] = None,
#     ts: Optional[datetime] = None,
# ) -> VivariumLogRecordV1:
#     ts = ts or datetime.now(timezone.utc)
#     return VivariumLogRecordV1(
#         ts=ts,
#         ts_unix=ts.timestamp(),
#         device=device,
#         errors=errors or [],
#         sensors=sensors,
#         controls=controls,
#     )
