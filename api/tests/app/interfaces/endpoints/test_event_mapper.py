from app.interfaces.schemas.event import EventMapper


def test_event_mapper_supports_message_delta_event() -> None:
    mapping = EventMapper._get_event_type_mapping()

    assert "message_delta" in mapping
    assert mapping["message_delta"].data_class.__name__ == "MessageDeltaEventData"
