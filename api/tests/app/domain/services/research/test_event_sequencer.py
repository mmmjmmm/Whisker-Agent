from app.domain.models.event import ResearchUsageEvent
from app.domain.services.research.event_sequencer import EventSequencer


async def test_events_receive_strict_run_sequence() -> None:
    sequencer = EventSequencer(run_id="run-1")
    await sequencer.publish(ResearchUsageEvent(run_id="wrong"))
    await sequencer.publish(ResearchUsageEvent())
    await sequencer.close()

    events = [event async for event in sequencer.events()]

    assert [event.sequence_no for event in events] == [1, 2]
    assert [event.run_id for event in events] == ["run-1", "run-1"]

