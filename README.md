# Raspberry Codex Pico Automation

Python automation project for reading Raspberry Pi Pico sensor data, storing it in SQLite, asking OpenAI for conservative plant-care recommendations, requesting Telegram approval, and controlling relay outputs for a pump and fan.

## Features

- Reads LM35DZ temperature data from Pico GP26 / ADC0.
- Reads soil moisture from ADS1115 A0 over Pico I2C GP0/GP1.
- Stores measurements in SQLite with split temperature, soil moisture, and light tables.
- Requests OpenAI advice for plant-care decisions.
- Sends Telegram approval prompts before relay actions.
- Supports S3 upload for the SQLite database.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Copy `telegram_config.example.json` to `telegram_config.json` and fill in your Telegram bot token and chat ID. Keep `telegram_config.json` private.

Configure optional values with environment variables:

```powershell
$env:PICO_PORT="COM7"
$env:PLANT_NAME="cherry tomato"
$env:OPENAI_MODEL="gpt-5.2"
$env:OPENAI_KEY_CSV="openai_api_key.csv"
$env:S3_BUCKET="your-bucket-name"
$env:AWS_KEY_CSV="aws_key.csv"
```

## Usage

Run one reading without OpenAI, Telegram, or relay actions:

```powershell
python main.py --skip-ai --no-upload
```

Run with a mocked reading for local testing:

```powershell
python main.py --mock-reading-json "{\"raw_avg\": 5000, \"raw_min\": 4900, \"raw_max\": 5100, \"voltage\": 0.25, \"temperature_c\": 25, \"soil_moisture_raw\": 12000, \"soil_moisture_voltage\": 1.5, \"relay1_gp15\": 1, \"relay2_gp14\": 1, \"relay_active_low\": true}" --auto-approve --dry-run-actions --no-upload
```

Run Telegram command bot mode:

```powershell
python main.py --telegram-bot
```

Send the latest system report once:

```powershell
python main.py --send-telegram-report
```

## Notes

Generated local files such as logs, SQLite databases, compiled Python files, API key CSV/XLSX files, and real Telegram configuration are intentionally ignored by git.
