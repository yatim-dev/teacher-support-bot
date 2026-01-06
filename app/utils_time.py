from datetime import datetime
from zoneinfo import ZoneInfo


def fmt_dt_for_tz(dt_utc: datetime, tz: str | None) -> str:
    z = ZoneInfo(tz or "Europe/Moscow")
    return dt_utc.astimezone(z).strftime("%Y-%m-%d %H:%M")
