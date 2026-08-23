from datetime import datetime, timedelta, timezone


def chat_turn_timestamps() -> tuple[datetime, datetime]:
    """Return distinct timestamps for user/assistant rows saved in one turn."""

    user_at = datetime.now(timezone.utc)
    return user_at, user_at + timedelta(microseconds=1)
