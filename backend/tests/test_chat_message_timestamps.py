from services.chat_message_timestamps import chat_turn_timestamps


def test_chat_turn_timestamps_user_before_assistant():
    user_at, assistant_at = chat_turn_timestamps()
    assert user_at < assistant_at
