import os
from pathlib import Path


PORT = os.getenv("PICO_PORT", "COM7")
DB_PATH = Path(os.getenv("SENSOR_DB_PATH", "sensor_data.sqlite3"))
S3_BUCKET = os.getenv("S3_BUCKET")
S3_PREFIX = os.getenv("S3_PREFIX", "raspberry-codex").strip("/")
AWS_KEY_CSV = os.getenv("AWS_KEY_CSV")
