import json
import time
from datetime import datetime
from pathlib import Path

import requests

from db import RELAY_ON, fetch_latest_reading, table_counts
from time_utils import LOCAL_TZ, format_local_time, reading_age_text


def normalize_for_message(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def load_telegram_config(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Telegram config not found: {path}")
    config = json.loads(path.read_text(encoding="utf-8"))
    token = config.get("telegram_bot_token")
    chat_id = config.get("telegram_chat_id")
    if not token or not chat_id:
        raise ValueError("Telegram config must include telegram_bot_token and telegram_chat_id.")
    if "replace_with" in token:
        raise ValueError(f"Telegram config still contains placeholder token: {path}")
    return {"telegram_bot_token": token, "telegram_chat_id": str(chat_id)}


def telegram_api(token: str, method: str, payload: dict | None = None) -> dict:
    payload = payload or {}
    request_timeout = max(20, int(payload.get("timeout", 0)) + 10)
    response = requests.post(
        f"https://api.telegram.org/bot{token}/{method}",
        json=payload,
        timeout=request_timeout,
    )
    try:
        data = response.json()
    except ValueError:
        data = {}
    if response.status_code >= 400:
        description = data.get("description", response.reason)
        raise RuntimeError(f"Telegram API HTTP {response.status_code} for {method}: {description}")
    if not data.get("ok"):
        raise RuntimeError(f"Telegram API error for {method}: {data}")
    return data["result"]


def get_latest_update_id(token: str) -> int:
    result = telegram_api(token, "getUpdates", {"timeout": 1, "limit": 1, "offset": -1})
    if not result:
        return 0
    return int(result[-1]["update_id"])


def format_automation_report(reading: dict, report: dict, plant_name: str, pump_seconds: float) -> str:
    actions = report.get("recommended_actions", {})
    fan_text = "EVET" if actions.get("fan_on") else "HAYIR"
    pump_text = "EVET" if actions.get("pump_on") else "HAYIR"
    return "\n".join(
        [
            "Pico otomatik tarim raporu",
            "",
            f"Bitki: {plant_name}",
            f"Sicaklik: {float(reading['temperature_c']):.1f} C",
            f"Toprak nemi ham deger: {int(reading.get('soil_moisture_raw', 0))}",
            f"Toprak nemi voltaj: {float(reading.get('soil_moisture_voltage', 0)):.3f} V",
            "",
            f"Sicaklik degerlendirmesi: {normalize_for_message(report.get('temperature_assessment', ''))}",
            f"Toprak degerlendirmesi: {normalize_for_message(report.get('soil_moisture_assessment', ''))}",
            "",
            f"Fan GP14: {fan_text} - {normalize_for_message(actions.get('fan_reason', ''))}",
            f"Pompa GP15: {pump_text} - {normalize_for_message(actions.get('pump_reason', ''))}",
            f"Onaylanirsa pompa suresi: {pump_seconds:.1f} saniye",
            "",
            f"Rapor: {normalize_for_message(report.get('user_report', ''))}",
            f"Guvenlik: {normalize_for_message(report.get('safety_note', ''))}",
            "",
            "Bu role aksiyonlarini onayliyor musun?",
        ]
    )


def request_telegram_approval(config: dict, text: str, timeout_seconds: int) -> tuple[bool, int | None]:
    token = config["telegram_bot_token"]
    chat_id = config["telegram_chat_id"]
    offset = get_latest_update_id(token) + 1
    sent = telegram_api(
        token,
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": text,
            "reply_markup": {
                "inline_keyboard": [
                    [
                        {"text": "Onayla", "callback_data": "approve_actions"},
                        {"text": "Reddet", "callback_data": "reject_actions"},
                    ]
                ]
            },
        },
    )

    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        updates = telegram_api(
            token,
            "getUpdates",
            {"offset": offset, "timeout": 5, "allowed_updates": ["callback_query"]},
        )
        for update in updates:
            offset = int(update["update_id"]) + 1
            callback = update.get("callback_query") or {}
            message = callback.get("message") or {}
            if str(message.get("chat", {}).get("id")) != chat_id:
                continue
            if message.get("message_id") != sent.get("message_id"):
                continue
            approved = callback.get("data") == "approve_actions"
            telegram_api(
                token,
                "answerCallbackQuery",
                {
                    "callback_query_id": callback["id"],
                    "text": "Onaylandi" if approved else "Reddedildi",
                },
            )
            telegram_api(
                token,
                "sendMessage",
                {"chat_id": chat_id, "text": "Role aksiyonlari onaylandi." if approved else "Role aksiyonlari reddedildi."},
            )
            return approved, sent.get("message_id")
    telegram_api(token, "sendMessage", {"chat_id": chat_id, "text": "Onay suresi doldu. Role aksiyonu uygulanmadi."})
    return False, sent.get("message_id")


def build_system_report(db_path: Path, plant_name: str = "cherry tomato") -> str:
    row = fetch_latest_reading(db_path)
    if not row:
        return "Henuz kayitli sensor olcumu yok."

    pump_state = "ACIK" if row["relay1_state"] == RELAY_ON else "KAPALI"
    fan_state = "ACIK" if row["relay2_state"] == RELAY_ON else "KAPALI"
    approved = row["telegram_approved"]
    approved_text = "beklemede/istenmedi" if approved is None else ("onaylandi" if approved else "reddedildi/sure doldu")
    report = {}
    if row["openai_report_json"]:
        try:
            report = json.loads(row["openai_report_json"])
        except json.JSONDecodeError:
            report = {"user_report": row["openai_report_json"]}
    actions = report.get("recommended_actions", {}) if isinstance(report, dict) else {}
    fan_reco = "EVET" if actions.get("fan_on") else "HAYIR"
    pump_reco = "EVET" if actions.get("pump_on") else "HAYIR"

    lines = [
        "Pico sistem raporu",
        "",
        f"Bot saati: {datetime.now(LOCAL_TZ).strftime('%Y-%m-%d %H:%M:%S TRT')}",
        f"Kayit ID: {row['id']}",
        f"Son olcum zamani: {format_local_time(row['measured_at_utc'])}",
        f"Son olcum yasi: {reading_age_text(row['measured_at_utc'])}",
        f"Bitki: {row['plant_name'] or plant_name}",
        f"Sicaklik: {float(row['temperature_c']):.1f} C",
        f"Toprak nemi ham deger: {row['soil_moisture_raw'] if row['soil_moisture_raw'] is not None else 'yok'}",
        f"Toprak nemi voltaj: {float(row['soil_moisture_voltage'] or 0):.3f} V",
        f"Pompa GP15: {pump_state}",
        f"Fan GP14: {fan_state}",
        "",
        f"Son oneri - Fan: {fan_reco}, Pompa: {pump_reco}",
        f"Telegram onayi: {approved_text}",
    ]
    if row["actions_applied_at_utc"]:
        applied_fan = "EVET" if row["applied_fan"] else "HAYIR"
        applied_pump = "EVET" if row["applied_pump"] else "HAYIR"
        lines.extend(
            [
                f"Aksiyon zamani: {format_local_time(row['actions_applied_at_utc'])}",
                f"Fan uygulandi: {applied_fan}",
                f"Pompa uygulandi: {applied_pump}",
            ]
        )

    user_report = normalize_for_message(report.get("user_report")) if isinstance(report, dict) else ""
    safety_note = normalize_for_message(report.get("safety_note")) if isinstance(report, dict) else ""
    if user_report:
        lines.extend(["", f"OpenAI raporu: {user_report}"])
    if safety_note:
        lines.append(f"Guvenlik: {safety_note}")
    return "\n".join(lines)


def send_system_report(config: dict, db_path: Path, plant_name: str = "cherry tomato") -> int | None:
    sent = telegram_api(
        config["telegram_bot_token"],
        "sendMessage",
        {"chat_id": config["telegram_chat_id"], "text": build_system_report(db_path, plant_name)},
    )
    return sent.get("message_id")


def build_help_message() -> str:
    return "\n".join(
        [
            "Pico bot komutlari",
            "",
            "/rapor - Detayli sistem raporu",
            "/son - Kisa son durum ozeti",
            "/sicaklik - Son sicaklik olcumu",
            "/durum - Bot ve veri tabani durumu",
            "/yardim - Komut listesini goster",
        ]
    )


def build_temperature_message(db_path: Path) -> str:
    row = fetch_latest_reading(db_path)
    if not row:
        return "Henuz sicaklik kaydi yok."
    return "\n".join(
        [
            "Son sicaklik",
            "",
            f"Bot saati: {datetime.now(LOCAL_TZ).strftime('%Y-%m-%d %H:%M:%S TRT')}",
            f"Kayit ID: {row['id']}",
            f"Son olcum zamani: {format_local_time(row['measured_at_utc'])}",
            f"Son olcum yasi: {reading_age_text(row['measured_at_utc'])}",
            f"Sicaklik: {float(row['temperature_c']):.1f} C",
            f"LM35 voltaj: {float(row['voltage']):.3f} V",
        ]
    )


def build_short_summary(db_path: Path) -> str:
    row = fetch_latest_reading(db_path)
    if not row:
        return "Henuz sensor kaydi yok."
    pump_state = "ACIK" if row["relay1_state"] == RELAY_ON else "KAPALI"
    fan_state = "ACIK" if row["relay2_state"] == RELAY_ON else "KAPALI"
    return "\n".join(
        [
            "Kisa son durum",
            "",
            f"Bot saati: {datetime.now(LOCAL_TZ).strftime('%Y-%m-%d %H:%M:%S TRT')}",
            f"Son olcum zamani: {format_local_time(row['measured_at_utc'])}",
            f"Son olcum yasi: {reading_age_text(row['measured_at_utc'])}",
            f"Sicaklik: {float(row['temperature_c']):.1f} C",
            f"Toprak nemi: {row['soil_moisture_raw'] if row['soil_moisture_raw'] is not None else 'yok'}",
            f"Pompa GP15: {pump_state}",
            f"Fan GP14: {fan_state}",
        ]
    )


def build_status_message(db_path: Path) -> str:
    counts, latest = table_counts(db_path)
    return "\n".join(
        [
            "Bot durumu",
            "",
            "Bot: calisiyor",
            f"Bot saati: {datetime.now(LOCAL_TZ).strftime('%Y-%m-%d %H:%M:%S TRT')}",
            f"Veri tabani: {db_path}",
            f"Son olcum zamani: {format_local_time(latest)}",
            f"Son olcum yasi: {reading_age_text(latest)}",
            f"Genel kayit: {counts['sensor_readings']}",
            f"Sicaklik kaydi: {counts['temperature_readings']}",
            f"Toprak nemi kaydi: {counts['soil_moisture_readings']}",
            f"Isik kaydi: {counts['light_readings']}",
        ]
    )


def run_telegram_bot(config: dict, db_path: Path, plant_name: str = "cherry tomato") -> None:
    token = config["telegram_bot_token"]
    chat_id = config["telegram_chat_id"]
    telegram_api(
        token,
        "setMyCommands",
        {
            "commands": [
                {"command": "rapor", "description": "Detayli sistem raporu"},
                {"command": "son", "description": "Kisa son durum ozeti"},
                {"command": "sicaklik", "description": "Son sicaklik olcumu"},
                {"command": "durum", "description": "Bot ve veri tabani durumu"},
                {"command": "yardim", "description": "Komut listesini goster"},
                {"command": "start", "description": "Bot durumunu goster"},
            ]
        },
    )
    offset = get_latest_update_id(token) + 1
    print("Telegram bot komut modu calisiyor. Komut listesi icin /yardim gonder.")
    while True:
        try:
            updates = telegram_api(
                token,
                "getUpdates",
                {"offset": offset, "timeout": 20, "allowed_updates": ["message"]},
            )
        except Exception as exc:
            print(f"Telegram polling gecici hata: {exc}")
            time.sleep(5)
            continue
        for update in updates:
            offset = int(update["update_id"]) + 1
            message = update.get("message") or {}
            chat = message.get("chat") or {}
            if str(chat.get("id")) != chat_id:
                continue
            text = (message.get("text") or "").strip()
            if text.startswith("/rapor"):
                reply = build_system_report(db_path, plant_name)
            elif text.startswith("/son"):
                reply = build_short_summary(db_path)
            elif text.startswith("/sicaklik"):
                reply = build_temperature_message(db_path)
            elif text.startswith("/durum"):
                reply = build_status_message(db_path)
            elif text.startswith("/yardim"):
                reply = build_help_message()
            elif text.startswith("/start"):
                reply = "Pico bot hazir. Komut listesi icin /yardim yaz."
            else:
                continue
            telegram_api(token, "sendMessage", {"chat_id": chat_id, "text": reply})
