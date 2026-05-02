import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


PUMP_GPIO = 15
FAN_GPIO = 14
RELAY_ON = 0
RELAY_OFF = 1


def add_column_if_missing(conn: sqlite3.Connection, table: str, name: str, definition: str) -> None:
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if name not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def init_db(db_path: Path) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sensor_readings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                measured_at_utc TEXT NOT NULL,
                device TEXT NOT NULL,
                lm35_gpio INTEGER NOT NULL,
                lm35_adc INTEGER NOT NULL,
                raw_avg REAL NOT NULL,
                raw_min INTEGER NOT NULL,
                raw_max INTEGER NOT NULL,
                voltage REAL NOT NULL,
                temperature_c REAL NOT NULL,
                relay1_gpio INTEGER NOT NULL,
                relay1_state INTEGER NOT NULL,
                relay2_gpio INTEGER NOT NULL,
                relay2_state INTEGER NOT NULL,
                relay_active_low INTEGER NOT NULL
            )
            """
        )
        for name, definition in {
            "soil_moisture_raw": "INTEGER",
            "soil_moisture_voltage": "REAL",
            "soil_moisture_source": "TEXT",
            "plant_name": "TEXT",
            "openai_model": "TEXT",
            "openai_report_json": "TEXT",
            "recommended_fan": "INTEGER",
            "recommended_pump": "INTEGER",
            "telegram_message_id": "INTEGER",
            "telegram_approved": "INTEGER",
            "actions_applied_at_utc": "TEXT",
            "applied_fan": "INTEGER",
            "applied_pump": "INTEGER",
            "pump_seconds": "REAL",
            "action_result_json": "TEXT",
        }.items():
            add_column_if_missing(conn, "sensor_readings", name, definition)

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS temperature_readings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sensor_reading_id INTEGER,
                measured_at_utc TEXT NOT NULL,
                device TEXT NOT NULL,
                sensor TEXT NOT NULL,
                gpio INTEGER NOT NULL,
                adc INTEGER NOT NULL,
                raw_avg REAL NOT NULL,
                raw_min INTEGER NOT NULL,
                raw_max INTEGER NOT NULL,
                voltage REAL NOT NULL,
                temperature_c REAL NOT NULL,
                FOREIGN KEY(sensor_reading_id) REFERENCES sensor_readings(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS soil_moisture_readings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sensor_reading_id INTEGER,
                measured_at_utc TEXT NOT NULL,
                device TEXT NOT NULL,
                sensor TEXT NOT NULL,
                source TEXT NOT NULL,
                raw INTEGER NOT NULL,
                voltage REAL NOT NULL,
                FOREIGN KEY(sensor_reading_id) REFERENCES sensor_readings(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS light_readings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sensor_reading_id INTEGER,
                measured_at_utc TEXT NOT NULL,
                device TEXT NOT NULL,
                sensor TEXT NOT NULL,
                source TEXT NOT NULL,
                raw INTEGER NOT NULL,
                voltage REAL NOT NULL,
                FOREIGN KEY(sensor_reading_id) REFERENCES sensor_readings(id)
            )
            """
        )
        for table in [
            "sensor_readings",
            "temperature_readings",
            "soil_moisture_readings",
            "light_readings",
        ]:
            conn.execute(
                f"""
                CREATE INDEX IF NOT EXISTS idx_{table}_measured_at
                ON {table}(measured_at_utc)
                """
            )


def save_reading(db_path: Path, reading: dict, plant_name: str) -> int:
    measured_at = datetime.now(timezone.utc).isoformat()
    device = "Raspberry Pi Pico W"
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO sensor_readings (
                measured_at_utc, device, lm35_gpio, lm35_adc,
                raw_avg, raw_min, raw_max, voltage, temperature_c,
                relay1_gpio, relay1_state, relay2_gpio, relay2_state,
                relay_active_low, soil_moisture_raw, soil_moisture_voltage,
                soil_moisture_source, plant_name
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                measured_at,
                device,
                26,
                0,
                float(reading["raw_avg"]),
                int(reading["raw_min"]),
                int(reading["raw_max"]),
                float(reading["voltage"]),
                float(reading["temperature_c"]),
                PUMP_GPIO,
                int(reading["relay1_gp15"]),
                FAN_GPIO,
                int(reading["relay2_gp14"]),
                1 if reading["relay_active_low"] else 0,
                int(reading.get("soil_moisture_raw", 0)),
                float(reading.get("soil_moisture_voltage", 0)),
                reading.get("soil_moisture_source", "ADS1115_A0"),
                plant_name,
            ),
        )
        reading_id = int(cur.lastrowid)
        conn.execute(
            """
            INSERT INTO temperature_readings (
                sensor_reading_id, measured_at_utc, device, sensor,
                gpio, adc, raw_avg, raw_min, raw_max, voltage, temperature_c
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                reading_id,
                measured_at,
                device,
                "LM35DZ",
                26,
                0,
                float(reading["raw_avg"]),
                int(reading["raw_min"]),
                int(reading["raw_max"]),
                float(reading["voltage"]),
                float(reading["temperature_c"]),
            ),
        )
        conn.execute(
            """
            INSERT INTO soil_moisture_readings (
                sensor_reading_id, measured_at_utc, device, sensor, source, raw, voltage
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                reading_id,
                measured_at,
                device,
                "soil_moisture",
                reading.get("soil_moisture_source", "ADS1115_A0"),
                int(reading.get("soil_moisture_raw", 0)),
                float(reading.get("soil_moisture_voltage", 0)),
            ),
        )
        if "light_raw" in reading or "light_voltage" in reading:
            conn.execute(
                """
                INSERT INTO light_readings (
                    sensor_reading_id, measured_at_utc, device, sensor, source, raw, voltage
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    reading_id,
                    measured_at,
                    device,
                    "LDR",
                    reading.get("light_source", "ADS1115_A1"),
                    int(reading.get("light_raw", 0)),
                    float(reading.get("light_voltage", 0)),
                ),
            )
        conn.commit()
        return reading_id


def migrate_split_sensor_tables(db_path: Path) -> None:
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO temperature_readings (
                sensor_reading_id, measured_at_utc, device, sensor,
                gpio, adc, raw_avg, raw_min, raw_max, voltage, temperature_c
            )
            SELECT
                sr.id, sr.measured_at_utc, sr.device, 'LM35DZ',
                sr.lm35_gpio, sr.lm35_adc, sr.raw_avg, sr.raw_min,
                sr.raw_max, sr.voltage, sr.temperature_c
            FROM sensor_readings sr
            WHERE NOT EXISTS (
                SELECT 1 FROM temperature_readings tr WHERE tr.sensor_reading_id = sr.id
            )
            """
        )
        conn.execute(
            """
            INSERT INTO soil_moisture_readings (
                sensor_reading_id, measured_at_utc, device, sensor, source, raw, voltage
            )
            SELECT
                sr.id, sr.measured_at_utc, sr.device, 'soil_moisture',
                COALESCE(sr.soil_moisture_source, 'unknown'),
                COALESCE(sr.soil_moisture_raw, 0),
                COALESCE(sr.soil_moisture_voltage, 0)
            FROM sensor_readings sr
            WHERE NOT EXISTS (
                SELECT 1 FROM soil_moisture_readings sm WHERE sm.sensor_reading_id = sr.id
            )
            """
        )
        conn.commit()


def update_decision(
    db_path: Path,
    reading_id: int,
    model: str | None,
    report: dict | None,
    telegram_message_id: int | None,
    telegram_approved: bool | None,
    action_result: dict | None,
    pump_seconds: float,
) -> None:
    recommended = report.get("recommended_actions", {}) if report else {}
    now = datetime.now(timezone.utc).isoformat() if action_result else None
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            UPDATE sensor_readings
            SET openai_model = ?,
                openai_report_json = ?,
                recommended_fan = ?,
                recommended_pump = ?,
                telegram_message_id = ?,
                telegram_approved = ?,
                actions_applied_at_utc = ?,
                applied_fan = ?,
                applied_pump = ?,
                pump_seconds = ?,
                action_result_json = ?
            WHERE id = ?
            """,
            (
                model,
                json.dumps(report, ensure_ascii=False) if report else None,
                1 if recommended.get("fan_on") else 0,
                1 if recommended.get("pump_on") else 0,
                telegram_message_id,
                None if telegram_approved is None else int(telegram_approved),
                now,
                1 if action_result and action_result.get("fan_was_activated") else 0,
                1 if action_result and action_result.get("pump_was_activated") else 0,
                pump_seconds,
                json.dumps(action_result, ensure_ascii=False) if action_result else None,
                reading_id,
            ),
        )
        conn.commit()


def fetch_latest_reading(db_path: Path) -> sqlite3.Row | None:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(
            "SELECT * FROM sensor_readings ORDER BY id DESC LIMIT 1"
        ).fetchone()


def table_counts(db_path: Path) -> tuple[dict, str | None]:
    with sqlite3.connect(db_path) as conn:
        counts = {}
        for table in [
            "sensor_readings",
            "temperature_readings",
            "soil_moisture_readings",
            "light_readings",
        ]:
            counts[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        latest = conn.execute(
            "SELECT measured_at_utc FROM sensor_readings ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return counts, latest[0] if latest else None


def print_latest(db_path: Path) -> None:
    row = fetch_latest_reading(db_path)
    if not row:
        print("No readings yet.")
        return
    relay1 = "ON" if row["relay1_state"] == RELAY_ON else "OFF"
    relay2 = "ON" if row["relay2_state"] == RELAY_ON else "OFF"
    print(
        "Saved reading #{id}: {temp:.1f} C, soil={soil_raw} ({soil_v:.3f} V), "
        "pump/GP15={relay1}, fan/GP14={relay2}".format(
            id=row["id"],
            temp=row["temperature_c"],
            soil_raw=row["soil_moisture_raw"] if row["soil_moisture_raw"] is not None else 0,
            soil_v=row["soil_moisture_voltage"] if row["soil_moisture_voltage"] is not None else 0,
            relay1=relay1,
            relay2=relay2,
        )
    )
