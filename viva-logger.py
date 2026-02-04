from abc import ABC, abstractmethod
from datetime import datetime, date, timedelta, timezone
import time
from pathlib import Path
import json
from typing import Annotated, Union, Any, Dict, List, Literal, Optional
from typing_extensions import Self

from pydantic import BaseModel, Field, ConfigDict, field_validator, model_validator, computed_field
import numpy as np
from labjack import ljm

LOG_DIR = Path("LOGS")          # existing directory

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
    

class Sensor(BaseModel):
    name: str
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


class t6902(Sensor):
    sensor_type: Literal["t6902"]
    name: str
    addr: int = 0x28
    rx_len: int = 4
    data: list = Field(None, description="The current RAW data from the sensor")
    
    @computed_field
    def rh(self) -> float:
        '''humidity fratcion'''
        rh_raw = ((self.data[0] & 0x3F) << 8) | self.data[1]
        return (rh_raw / 16384.0)

    @computed_field
    def tC(self) -> float:
        '''dry-buld temperature (C)'''
        t_raw = (self.data[2] << 6) | (self.data[3] >> 2)
        return (t_raw / 16384.0) * 165.0 - 40.0

    @computed_field
    def tF(self) -> float:
        '''dry-buld temperature (F)'''
        return self.tC * (9/5) + 32


    @computed_field
    def dpC(self)->float:
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

        self.data = self._device.read_i2c(addr=self.addr, rx_len=self.rx_len)

        status = (self.data[0] >> 6) & 0x03
        
        return {"RH":self.rh, "TF":self.tF, "DPF":self.dpF}


class I2cConfig(BaseModel):
    sda_dio: int = 8
    scl_dio: int = 9
    speed: int = 65536

class Labjack(BaseModel):
    handle: int = None
    device_type: Optional[str] = "T4"
    sn: Optional[str] = "ANY"
    conn_type: Optional[str] = "TCP"
    conn_status: bool = False
    i2c: I2cConfig = I2cConfig()
   
    
    @model_validator(mode="after")
    def connect(self):
        self.handle = ljm.openS(self.device_type, self.conn_type, self.sn)
        info = ljm.getHandleInfo(self.handle)
        self.sn = info[2]
        print(f"Connected to: devType={info[0]}, connType={info[1]}, serial={info[2]}")

        self.conn_status = True

        if self.i2c:
            self.config_i2c()
        
        return self
    
    def config_i2c(self):
        ljm.eWriteName(self.handle, "I2C_SDA_DIONUM", self.i2c.sda_dio)
        ljm.eWriteName(self.handle, "I2C_SCL_DIONUM", self.i2c.scl_dio)
        ljm.eWriteName(self.handle, "I2C_SPEED_THROTTLE", self.i2c.speed)
        ljm.eWriteName(self.handle, "I2C_OPTIONS", 0)

        return self
    
    def read_i2c(self, addr: int, rx_len: int):
        ljm.eWriteName(self.handle, "I2C_SLAVE_ADDRESS", addr)
        ljm.eWriteName(self.handle, "I2C_NUM_BYTES_TX", 0)
        ljm.eWriteName(self.handle, "I2C_NUM_BYTES_RX", rx_len)
        ljm.eWriteName(self.handle, "I2C_GO", 1)

        return ljm.eReadNameByteArray(self.handle, "I2C_DATA_RX", rx_len)


discriminated_sensors = Annotated[Union[t6902], Field(discriminator="sensor_type")]

def log_path() -> Path:
    # Day-start file name, local date: LOGS/YYYY-MM-DD.jsonl
    today = date.today()
    return LOG_DIR / f"{today.isoformat()}.jsonl"

class Logger(BaseModel):
    device: Labjack
    htz: float = Field(1, description="The minimum read frequency, in seconds")
    sensors: Optional[list[discriminated_sensors]] = []

    @model_validator(mode="after")
    def associate_sensors(self) -> Self:
        for sensor in self.sensors:
            sensor._device = self.device
        return self

    def read_all(self) -> list:
        reading = []
        for sensor in self.sensors:
            reading.append(sensor.read())
        return reading
    
    def run(self, htz=1) -> None:
        
        current_path = log_path()
        f = open(current_path, "a", buffering=1)
        print(f"Streaming JSONL to: {current_path}  (Ctrl+C to stop)")
    
        try:
            next_tick = time.monotonic()
            while True:
                # roll file at local midnight
                new_path = log_path()
                if new_path != current_path:
                    f.close()
                    current_path = new_path
                    f = open(current_path, "a", buffering=1)
                    print(f"New day → now streaming to: {current_path}")

                next_tick += self.htz
                now = datetime.now()
                now_iso = now.isoformat(timespec="seconds")
                now_epoch = now.timestamp()

                record = {
                    "timestamp": now_iso
                }
                readings = [point.read() for point in self.sensors]
                record['readings' ]  = readings

                f.write(json.dumps(record, default=lambda obj: str(obj)) + "\n")
                f.flush()

                sleep_s = next_tick - time.monotonic()
                if sleep_s > 0:
                    time.sleep(sleep_s)

        except KeyboardInterrupt:
            print("\nStopping.")
        finally:
            f.close()
            ljm.close(self.device.handle)

    # sensors: list["Sensor"] = Field(..., description="List of sensors")

if __name__ == "__main__":
    logger_config =  {"device":{"device_type":"T4"},
                      "htz":15,
                      "sensors":[
                          {"sensor_type":"t6902", "name":"temp-rh"},
                        #   {"sensor_type":"10K", "name":"temp-A"},
                        #   {"sensor_type":"10K", "name":"temp-B"}
                          ]}
    
    logger = Logger(**logger_config)
    print(logger.run())
    
# class DeviceMeta(BaseModel):
#     model_config = ConfigDict(extra="allow")

#     dev_type: Optional[int] = Field(default=None, description="LabJack device type enum (from getHandleInfo)")
#     conn_type: Optional[int] = Field(default=None, description="Connection type enum (USB/Ethernet/etc.)")
#     serial: Optional[int] = None
#     ip: Optional[int] = Field(default=None, description="IP as integer from LabJack API (if applicable)")
#     port: Optional[int] = None

    
    
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
