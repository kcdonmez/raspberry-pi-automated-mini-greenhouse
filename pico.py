import json
import subprocess
from typing import Optional


MICROPYTHON_READ_SCRIPT = r"""
from machine import Pin, ADC, I2C
import json
import time

# LM35DZ:
# left leg  -> Pico 3V3 OUT
# middle    -> Pico GP26 / ADC0
# right leg -> Pico GND
lm35 = ADC(26)

# ADS1115:
# Pico 3V3 -> VDD
# Pico GND -> GND
# Pico GP0 -> SDA
# Pico GP1 -> SCL
# Soil moisture analog output -> ADS1115 A0
ADS1115_ADDR = 0x48
ADS1115_CONVERSION = 0x00
ADS1115_CONFIG = 0x01
ADS1115_LSB_VOLTS = 4.096 / 32768
i2c = I2C(0, scl=Pin(1), sda=Pin(0), freq=100000)

# 2-channel relay board:
# VCC -> Pico VBUS / 5V
# GND -> Pico GND
# IN1 -> Pico GP15 = pump
# IN2 -> Pico GP14 = fan
# Active-low: 0=ON, 1=OFF
relay1 = Pin(15, Pin.OUT, value=1)
relay2 = Pin(14, Pin.OUT, value=1)

def read_ads1115_a0():
    # Single-shot, AIN0 vs GND, +/-4.096V PGA, 128 SPS, comparator disabled.
    config = 0xC383
    i2c.writeto_mem(ADS1115_ADDR, ADS1115_CONFIG, bytes([(config >> 8) & 0xFF, config & 0xFF]))
    time.sleep_ms(10)
    data = i2c.readfrom_mem(ADS1115_ADDR, ADS1115_CONVERSION, 2)
    raw = (data[0] << 8) | data[1]
    if raw & 0x8000:
        raw -= 0x10000
    return raw, raw * ADS1115_LSB_VOLTS

lm35_values = []
for _ in range(20):
    lm35_values.append(lm35.read_u16())
    time.sleep_ms(25)

raw_avg = sum(lm35_values) / len(lm35_values)
voltage = raw_avg * 3.3 / 65535
temperature_c = voltage * 100
soil_raw, soil_voltage = read_ads1115_a0()

payload = {
    "raw_avg": raw_avg,
    "raw_min": min(lm35_values),
    "raw_max": max(lm35_values),
    "voltage": voltage,
    "temperature_c": temperature_c,
    "soil_moisture_raw": soil_raw,
    "soil_moisture_voltage": soil_voltage,
    "soil_moisture_source": "ADS1115_A0",
    "relay1_gp15": relay1.value(),
    "relay2_gp14": relay2.value(),
    "relay_active_low": True,
}

marker = "__" + "SENSOR_JSON__"
print(marker + json.dumps(payload))
print("__" + "SENSOR_BEGIN__")
for key in payload:
    print(str(key) + "=" + str(payload[key]))
print("__" + "SENSOR_END__")
"""


MICROPYTHON_LM35_ONLY_SCRIPT = r"""
from machine import Pin, ADC
import json
import time

lm35 = ADC(26)

relay1 = Pin(15, Pin.OUT, value=1)
relay2 = Pin(14, Pin.OUT, value=1)

values = []
for _ in range(20):
    values.append(lm35.read_u16())
    time.sleep_ms(25)

raw_avg = sum(values) / len(values)
voltage = raw_avg * 3.3 / 65535
temperature_c = voltage * 100

payload = {
    "raw_avg": raw_avg,
    "raw_min": min(values),
    "raw_max": max(values),
    "voltage": voltage,
    "temperature_c": temperature_c,
    "soil_moisture_raw": 0,
    "soil_moisture_voltage": 0,
    "soil_moisture_source": "not_connected",
    "relay1_gp15": relay1.value(),
    "relay2_gp14": relay2.value(),
    "relay_active_low": True,
}

marker = "__" + "SENSOR_JSON__"
print(marker + json.dumps(payload))
print("__" + "SENSOR_BEGIN__")
for key in payload:
    print(str(key) + "=" + str(payload[key]))
print("__" + "SENSOR_END__")
"""


def make_relay_script(fan_on: bool, pump_seconds: float, pump_gpio: int = 15, fan_gpio: int = 14) -> str:
    pump_seconds = max(0, float(pump_seconds))
    fan_value = 0 if fan_on else 1
    return f"""
from machine import Pin
import json
import time

pump = Pin({pump_gpio}, Pin.OUT, value=1)
fan = Pin({fan_gpio}, Pin.OUT, value=1)

fan.value({fan_value})
pump_started = False
if {pump_seconds!r} > 0:
    pump_started = True
    pump.value(0)
    time.sleep({pump_seconds!r})
    pump.value(1)

payload = {{
    "pump_gp{pump_gpio}": pump.value(),
    "fan_gp{fan_gpio}": fan.value(),
    "pump_was_activated": pump_started,
    "fan_was_activated": {str(fan_on)},
    "pump_seconds": {pump_seconds!r},
    "relay_active_low": True,
}}
marker = "__" + "ACTION_JSON__"
print(marker + json.dumps(payload))
"""


def run_pico_code(port: str, code: str, marker: str, timeout: int = 15) -> dict:
    ps = f"""
$code = @'
{code}
'@
$port = New-Object System.IO.Ports.SerialPort '{port}',115200,'None',8,'One'
$port.ReadTimeout = 1500
$port.WriteTimeout = 1500
try {{
  $port.Open()
  Start-Sleep -Milliseconds 250
  $port.DiscardInBuffer()
  $port.Write([byte[]](3), 0, 1)
  Start-Sleep -Milliseconds 100
  $port.Write([byte[]](3), 0, 1)
  $port.Write("`r`n")
  Start-Sleep -Milliseconds 250
  $port.DiscardInBuffer()
  $port.Write([byte[]](5), 0, 1)
  Start-Sleep -Milliseconds 150
  $port.Write($code)
  $port.Write("`r`n")
  $port.Write([byte[]](4), 0, 1)
  Start-Sleep -Milliseconds 1800
  $output = ""
  $idle = 0
  while ($idle -lt 8) {{
    $chunk = $port.ReadExisting()
    if ($chunk.Length -gt 0) {{ $output += $chunk; $idle = 0 }} else {{ $idle++ }}
    Start-Sleep -Milliseconds 150
  }}
  $output
}} finally {{
  if ($port.IsOpen) {{ $port.Close() }}
}}
"""
    result = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )

    combined = result.stdout + result.stderr
    start = 0
    while True:
        marker_index = combined.find(marker, start)
        if marker_index == -1:
            break
        payload_start = marker_index + len(marker)
        payload_text = combined[payload_start:].lstrip()
        decoder = json.JSONDecoder()
        try:
            payload, _ = decoder.raw_decode(payload_text)
            return payload
        except json.JSONDecodeError:
            start = payload_start

    kv_payload = parse_sensor_key_value_payload(combined)
    if kv_payload is not None:
        return kv_payload

    raise RuntimeError(f"Pico JSON marker {marker!r} could not be read. Output:\n{combined}")


def parse_sensor_key_value_payload(output: str) -> Optional[dict]:
    begin = output.find("__SENSOR_BEGIN__")
    end = output.find("__SENSOR_END__", begin)
    if begin == -1 or end == -1:
        return None

    payload = {}
    for line in output[begin:end].splitlines()[1:]:
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value in {"True", "False"}:
            payload[key] = value == "True"
            continue
        try:
            if any(ch in value for ch in ".eE"):
                payload[key] = float(value)
            else:
                payload[key] = int(value)
        except ValueError:
            payload[key] = value

    return payload or None


def run_pico_script(port: str) -> dict:
    return run_pico_code(port, MICROPYTHON_READ_SCRIPT, "__SENSOR_JSON__")


def run_pico_lm35_only_script(port: str) -> dict:
    return run_pico_code(port, MICROPYTHON_LM35_ONLY_SCRIPT, "__SENSOR_JSON__")


def apply_relay_actions(port: str, fan_on: bool, pump_on: bool, pump_seconds: float, pump_gpio: int = 15, fan_gpio: int = 14) -> dict:
    duration = pump_seconds if pump_on else 0
    code = make_relay_script(fan_on, duration, pump_gpio=pump_gpio, fan_gpio=fan_gpio)
    return run_pico_code(port, code, "__ACTION_JSON__", timeout=20)
