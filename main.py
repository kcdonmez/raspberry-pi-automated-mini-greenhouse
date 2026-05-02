import argparse
import json
import os
import sys
import time
from pathlib import Path

import pico
from config import AWS_KEY_CSV, DB_PATH, PORT, S3_BUCKET, S3_PREFIX
from db import init_db, migrate_split_sensor_tables, print_latest, save_reading, update_decision
from openai_service import load_openai_key_from_file, request_openai_advice
from storage import upload_db_to_s3
from telegram_bot import (
    format_automation_report,
    load_telegram_config,
    request_telegram_approval,
    run_telegram_bot,
    send_system_report,
)


TELEGRAM_CONFIG = Path(os.getenv("TELEGRAM_CONFIG", "telegram_config.json"))
OPENAI_KEY_CSV = os.getenv("OPENAI_KEY_CSV")
PLANT_NAME = os.getenv("PLANT_NAME", "cherry tomato")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.2")
PUMP_SECONDS = float(os.getenv("PUMP_SECONDS", "5"))
TELEGRAM_APPROVAL_TIMEOUT = int(os.getenv("TELEGRAM_APPROVAL_TIMEOUT", "120"))


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


def load_json_arg(value: str | None) -> dict | None:
    if not value:
        return None
    if value.startswith("@"):
        return json.loads(Path(value[1:]).read_text(encoding="utf-8"))
    return json.loads(value)


def read_sensor_data(args: argparse.Namespace) -> dict:
    if args.mock_reading_json:
        return load_json_arg(args.mock_reading_json)
    if args.lm35_only:
        return pico.run_pico_lm35_only_script(args.port)
    return pico.run_pico_script(args.port)


def maybe_upload(args: argparse.Namespace, db_path: Path) -> None:
    if args.no_upload:
        return
    if not args.bucket:
        print("S3_BUCKET is not set; skipped S3 upload.")
        return
    location = upload_db_to_s3(db_path, args.bucket, args.prefix, args.aws_key_csv)
    print(f"Uploaded SQLite database to {location}")


def run_once(args: argparse.Namespace, db_path: Path, openai_api_key: str | None) -> None:
    reading = read_sensor_data(args)
    reading_id = save_reading(db_path, reading, args.plant)
    print_latest(db_path)

    report = None
    approved = None
    message_id = None
    action_result = None

    if not args.skip_ai:
        try:
            report = load_json_arg(args.mock_advice_json) or request_openai_advice(
                reading, args.plant, args.model, openai_api_key
            )
            report_text = format_automation_report(reading, report, args.plant, args.pump_seconds)
            print(report_text)

            if args.auto_approve:
                approved = True
            else:
                telegram_config = load_telegram_config(Path(args.telegram_config))
                approved, message_id = request_telegram_approval(
                    telegram_config, report_text, args.approval_timeout
                )

            actions = report.get("recommended_actions", {})
            if approved and (actions.get("fan_on") or actions.get("pump_on")):
                if args.dry_run_actions:
                    action_result = {
                        "dry_run": True,
                        "fan_was_activated": bool(actions.get("fan_on")),
                        "pump_was_activated": bool(actions.get("pump_on")),
                        "pump_seconds": args.pump_seconds,
                    }
                else:
                    action_result = pico.apply_relay_actions(
                        args.port,
                        bool(actions.get("fan_on")),
                        bool(actions.get("pump_on")),
                        args.pump_seconds,
                    )
                print(f"Relay action result: {json.dumps(action_result, ensure_ascii=False)}")
            else:
                print("No relay action applied.")
        except Exception as exc:
            print(f"Automation skipped safely: {exc}")
        finally:
            update_decision(
                db_path,
                reading_id,
                args.model if report else None,
                report,
                message_id,
                approved,
                action_result,
                args.pump_seconds,
            )

    maybe_upload(args, db_path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read Pico farming data, ask OpenAI for advice, get Telegram approval, and control relays."
    )
    parser.add_argument("--port", default=PORT, help="Pico serial port, default COM7")
    parser.add_argument("--db", default=str(DB_PATH), help="SQLite database path")
    parser.add_argument("--bucket", default=S3_BUCKET, help="S3 bucket name")
    parser.add_argument("--prefix", default=S3_PREFIX, help="S3 key prefix")
    parser.add_argument("--aws-key-csv", default=AWS_KEY_CSV, help="CSV with AWS access keys")
    parser.add_argument("--interval", type=float, default=0, help="Repeat interval in seconds")
    parser.add_argument("--no-upload", action="store_true", help="Only save locally")
    parser.add_argument("--plant", default=PLANT_NAME, help="Plant name for OpenAI analysis")
    parser.add_argument("--model", default=OPENAI_MODEL, help="OpenAI model name")
    parser.add_argument("--openai-key-csv", default=OPENAI_KEY_CSV, help="CSV/XLSX file containing an OpenAI API key")
    parser.add_argument("--telegram-config", default=str(TELEGRAM_CONFIG), help="Telegram config JSON path")
    parser.add_argument("--approval-timeout", type=int, default=TELEGRAM_APPROVAL_TIMEOUT)
    parser.add_argument("--pump-seconds", type=float, default=PUMP_SECONDS)
    parser.add_argument("--skip-ai", action="store_true", help="Save sensor data without OpenAI/Telegram/relay actions")
    parser.add_argument("--lm35-only", action="store_true", help="Read only LM35DZ; skip ADS1115 soil moisture")
    parser.add_argument("--mock-reading-json", help="Inline JSON or @path for testing without Pico")
    parser.add_argument("--mock-advice-json", help="Inline JSON or @path for testing without OpenAI")
    parser.add_argument("--auto-approve", action="store_true", help="Testing only: approve without Telegram")
    parser.add_argument("--dry-run-actions", action="store_true", help="Do not activate Pico relays")
    parser.add_argument("--telegram-bot", action="store_true", help="Run Telegram command bot mode")
    parser.add_argument("--send-telegram-report", action="store_true", help="Send one latest system report to Telegram")
    parser.add_argument("--migrate-sensor-tables", action="store_true", help="Backfill split temperature/soil/light tables")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    db_path = Path(args.db)
    init_db(db_path)

    if args.migrate_sensor_tables:
        migrate_split_sensor_tables(db_path)
        print("Sensör tabloları ayrıldı ve eski kayıtlar taşındı.")
        return 0

    if args.telegram_bot or args.send_telegram_report:
        telegram_config = load_telegram_config(Path(args.telegram_config))
        if args.send_telegram_report:
            message_id = send_system_report(telegram_config, db_path, args.plant)
            print(f"Telegram raporu gönderildi. message_id={message_id}")
            return 0
        run_telegram_bot(telegram_config, db_path, args.plant)
        return 0

    openai_api_key = load_openai_key_from_file(args.openai_key_csv)
    while True:
        run_once(args, db_path, openai_api_key)
        if args.interval <= 0:
            break
        time.sleep(args.interval)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
