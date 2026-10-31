# Viva Logger 🦎🌿

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)
[![Hardware: LabJack T4](https://img.shields.io/badge/hardware-LabJack%20T4-orange.svg)](https://labjack.com/)
[![Platform: Raspberry Pi](https://img.shields.io/badge/platform-Raspberry%20Pi-red.svg)](https://www.raspberrypi.com/)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)

**Viva Logger** is an industrial-grade, telemetry-driven environmental automation and control system for high-performance vivaria. Built around **LabJack** instrumentation hardware and deployed on **Raspberry Pi**, it bridges precision laboratory data acquisition with biological animal husbandry.

Originally engineered for **Penelope**, a crested gecko (*Correlophus ciliatus*), Viva Logger rejects consumer smart-plug timers in favor of closed-loop feedback, psychrometric calculations (dew point & VPD), fail-safe relay architecture, astronomical solar curves, and non-volatile brownout recovery.

---

## 🌟 Key Engineering Features

### 1. Fail-Safe Humidity Control & Flood Prevention
* **Multi-Stage State Machine**: Transitions through `idle` $\rightarrow$ `misting` (micro-pulses) $\rightarrow$ `settling` (evaporation buffer) with configurable hysteresis.
* **Non-Volatile Brownout Recovery**: State (`last_pulse_ended_at`, `pulse_count_in_window`, `phase`) is persisted atomically to disk (`.runtime_state.json`). Following an unattended power cycle, the system **resumes the remaining cooldown** rather than resetting to amnesia.
* **Boot Grace Period (`startup_grace_period_s`)**: Enforces a strict lockout upon reboot to allow sensors to warm up and stabilize before misting is permitted.
* **Triple Safety Interlocks**:
  * `hard_max_rh_pct`: Hard ceiling that unconditionally cuts pump power.
  * `min_off_s`: Enforced minimum off-time between any mist events.
  * `max_consecutive_pulses`: Prevents pump burnout or reservoir drainage if a nozzle clogs or a sensor disconnects.
  * `drying_alarm`: Exponential moving average (EMA) alert for sudden enclosure desiccation.

### 2. Astronomical Habitat Solar Simulation
* **New Caledonia Solar Position Engine** (`logger.sun`): Computes real-time astronomical solar elevation angles for the Crested Gecko's endemic habitat in New Caledonia (`-22.15° S, 166.75° E`).
* **Natural Sine Irradiance Curves**: Replaces harsh stepped or trapezoidal dimmer ramps with smooth, continuous astronomical solar arcs.
* **Atmospheric Cloud Cover Modeling**: Modulates irradiance with both offline procedural multi-frequency cloud drifts and live real-time weather sync via the **Open-Meteo API**.
* **Zero-Flicker Motorized 0–180° Dimmer**: Standard LED bulbs flicker or drop out below ~20% electrical dimming. Viva Logger calculates a physical rotation angle for a motorized stepper fixture: holding electrical power steady at a flicker-free 20% floor while gently rotating the fixture $0^\circ \rightarrow 180^\circ$ toward the ceiling, producing true $0\text{ lux}$ interior night darkness with zero electrical flicker.

### 3. LabJack Instrumentation Layer
* **Active-Low Fail-Safe Relays**: Digital outputs enforce active-low logic (`0` = energized, `1` = de-energized/safe), ensuring that unpowered or booting pins cannot accidentally energize high-pressure pumps.
* **Hardware DIO_EF Timers**: Hardware-clocked 1000 Hz PWM dimming using LabJack DIO extended features with roll counter calibration.
* **I2C Bit-Banging Engine**: High-accuracy Amphenol **T9602** relative humidity and temperature sensor decoding.
* **In-Process USB Auto-Reconnect**: Main polling loops gracefully catch transient USB dropouts or brownouts, resetting handles and re-initializing without killing the background daemon.

### 4. Live Telemetry & Web Dashboard
* **Append-Only JSONL Logs**: Streaming records written to `VIV-LOGS/YYYY-MM-DD.jsonl`, automatically rolling at local midnight.
* **Calculated Psychrometrics**: Real-time computation of saturation vapor pressure, actual vapor pressure, dew point, and **Vapor Pressure Deficit (VPD)**.
* **Interactive Dash Dashboard** (`logger.dashboard`): Real-time Plotly charts, live diagnostics, and on-the-fly JSON configuration tuning with instant hot-reloading.

---

## 🏛️ System Architecture

```text
┌────────────────────────────────────────────────────────────────────────┐
│                          Raspberry Pi (Host)                           │
│                                                                        │
│   systemd (viva-logger.service)                                        │
│     │                                                                  │
│     ├── SystemRuntime                                                  │
│     │     ├── Scheduler (5s Monotonic Loop)                            │
│     │     ├── SensorManager  ──> [ T9602 I2C / LabJack Temp / AIN ]    │
│     │     ├── ControlManager ──> [ PWM Daytime Ramp / Sun Curve ]      │
│     │     │                  ──> [ HumidityControl State Machine ]     │
│     │     └── StatePersistence (.runtime_state.json)                   │
│     │                                                                  │
│     └── Dash Dashboard (Port 8050) <── Tails YYYY-MM-DD.jsonl         │
└─────────────────────────────────┬──────────────────────────────────────┘
                                  │ USB (LabJack LJM Driver)
┌─────────────────────────────────▼──────────────────────────────────────┐
│                           LabJack T4 DAQ                               │
│                                                                        │
│   DIO6  ───> 0-10V Dimmer Module (PWM @ 1000 Hz)                       │
│   DIO8  ───> T9602 Lower/Floor SDA (3.3V I2C, 0x28)                    │
│   DIO9  ───> T9602 Lower/Floor SCL (3.3V I2C)                          │
│   DIO10 ───> T9602 Top/Canopy SDA (3.3V I2C, 0x28)                     │
│   DIO11 ───> T9602 Top/Canopy SCL (3.3V I2C)                           │
│   DIO12 ───> Active-Low Relay ───> MistKing High-Pressure Pump         │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 📦 Installation & Setup

Viva Logger expects **Python 3.11+** managed by **Poetry**.

```bash
# Clone the repository
git clone https://github.com/robebach/viva-logger.git
cd viva-logger

# Install dependencies (piwheels supported for fast ARM builds)
poetry install
```

### LabJack LJM Drivers (Hardware Runtimes)
For live hardware operation, install the LabJack LJM library:
* **Raspberry Pi OS (64-bit / AArch64)**:
  ```bash
  curl -fsSL -O https://files.labjack.com/installers/LJM/Linux/AArch64/release/LabJack-LJM_2025-05-07.zip
  unzip LabJack-LJM_2025-05-07.zip
  sudo ./labjack_ljm_installer.run -- --without-kipling
  ```
* **Offline Mock Driver**: Viva Logger includes `MockLabjackDriver` for unit tests and local software development without physical hardware.

---

## 🚀 Running the System

### 1. Production Service (Raspberry Pi)
Deploy as an auto-recovering background service under `systemd`:

```bash
sudo cp systemd/viva-logger.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now viva-logger
```

Watch live telemetry stream:
```bash
journalctl -u viva-logger -f
```

### 2. Manual Execution
Run directly in the foreground with hot-reloading JSON config:
```bash
poetry run python -m examples.penelope_full --config VIV-CONFIG/penelope.json
```

### 3. Launching the Web Dashboard
```bash
poetry run python -m logger.dashboard \
  --host 0.0.0.0 \
  --log VIV-LOGS/$(date +%Y-%m-%d).jsonl \
  --config VIV-CONFIG/penelope.json
```
Open your browser to `http://penelope-pi.local:8050` to inspect live curves and adjust setpoints.

---

## ⚙️ Configuration Example (`penelope.json`)

```json
{
  "log_dir": "VIV-LOGS",
  "sample_interval_s": 5.0,
  "device": { "device_type": "T4", "serial_number": "ANY", "connection_type": "ANY" },
  "sensors": [
    { "sensor_type": "device_temperature", "name": "labjack" },
    {
      "sensor_type": "t9602",
      "name": "temp-rh-canopy",
      "i2c": { "sda_dio": 8, "scl_dio": 9, "address": 40, "rx_len": 4 }
    }
  ],
  "relays": [
    { "name": "misting-pump-eio4", "dio": 12, "initial_state": 1 }
  ],
  "pwm_outputs": [
    {
      "name": "led-0-10v",
      "dio": 6,
      "frequency_hz": 1000.0,
      "duty_cycle": 0.04,
      "enabled": true,
      "schedules": [
        {
          "name": "daylight-ramp",
          "start_time": "06:00:00",
          "end_time": "20:00:00",
          "min_duty_cycle": 0.0,
          "max_duty_cycle": 0.4,
          "ramp_duration_s": 60.0
        }
      ]
    }
  ],
  "humidity_controls": [
    {
      "name": "penelope-mister",
      "rh_sensor": "temp-rh-canopy",
      "relay": "misting-pump-eio4",
      "enabled": true,
      "startup_grace_period_s": 180.0,
      "settle_s": 300.0,
      "min_off_s": 30.0,
      "hard_max_rh_pct": 85.0,
      "max_consecutive_pulses": 6,
      "schedules": [
        {
          "name": "day-background",
          "start_time": "05:00:00",
          "end_time": "20:00:00",
          "target_rh_pct": 52.0,
          "trigger_below_rh_pct": 42.0,
          "max_pulse_s": 8.0
        },
        {
          "name": "night-humidity",
          "start_time": "20:00:00",
          "end_time": "05:00:00",
          "target_rh_pct": 72.0,
          "trigger_below_rh_pct": 65.0,
          "max_pulse_s": 30.0
        }
      ]
    }
  ]
}
```

---

## 🧪 Testing

Viva Logger comes with a full automated test suite covering state persistence, recovery, and astronomical calculations:

```bash
poetry run pytest
```

---

## 🗺️ Roadmap & Future Architecture

- [x] **Dual-Sensor Vertical Gradient Monitoring**: Top canopy RH (`top-rh`) vs. bottom substrate RH (`low-rh`) telemetry deployed to monitor the vivarium microclimate gradient.
- [ ] **Nocturnal Activity & Movement Tracking Camera**: Integrate an infrared (NoIR) camera module with computer vision motion tracking / timelapse capture to monitor Penelope's nocturnal activity, tracking her movements when she emerges from her dark upper shelf / vertical PVC sleeping hide and descends to the lower vivarium at night.
- [ ] **Hardware-Agnostic Driver Abstraction Layer (HAL)**: While **LabJack** remains the gold standard for laboratory instrumentation and noise-free ADC measurements, decouple driver bindings to support alternate backends (e.g., native Raspberry Pi GPIO, ESP32 microcontrollers via I2C/UART, Home Assistant / MQTT bridge). *(Low priority — LabJack reliability is unmatched).*
- [ ] **Ultra-Quiet Circulation Fan Module**: PWM-controlled low-speed brushless fan to gently circulate air and balance the vertical gradient, mimicking natural forest breezes.
- [ ] **Physical Stepper Dimmer Driver**: Trinamic TMC2208/TMC2209 StealthChop integration to execute the physical 0–180° fixture rotation dead silently.
- [ ] **Habitat Weather Mode**: Optional synchronization with live weather radar/satellite precipitation feeds for real-time rain and light dynamics.

---

## 📄 License

MIT License. Designed and crafted with precision for high-standard animal care.
