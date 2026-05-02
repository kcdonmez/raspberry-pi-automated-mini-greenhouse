from datetime import datetime, timedelta, timezone


LOCAL_TZ = timezone(timedelta(hours=3), "TRT")


def format_local_time(utc_value: str | None) -> str:
    if not utc_value:
        return "yok"
    try:
        dt = datetime.fromisoformat(str(utc_value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S TRT")
    except ValueError:
        return str(utc_value)


def seconds_to_age(seconds: float | int | None) -> str:
    if seconds is None:
        return "bilinmiyor"
    seconds = max(0, int(seconds))
    minutes = seconds // 60
    hours = minutes // 60
    days = hours // 24
    if days:
        return f"{days} gün {hours % 24} saat önce"
    if hours:
        return f"{hours} saat {minutes % 60} dk önce"
    if minutes:
        return f"{minutes} dk önce"
    return f"{seconds} sn önce"


def reading_age_text(utc_value: str | None) -> str:
    if not utc_value:
        return "bilinmiyor"
    try:
        dt = datetime.fromisoformat(str(utc_value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return seconds_to_age((datetime.now(timezone.utc) - dt).total_seconds())
    except ValueError:
        return "bilinmiyor"
