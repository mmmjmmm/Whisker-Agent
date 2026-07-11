from app.domain.models.agent_run import (
    AgentTask,
    CapabilityProfile,
    TaskStatus,
)
from app.domain.models.event import ResearchSourceEvent, ResearchTaskEvent
from app.domain.models.research import ResearchSource
from app.interfaces.schemas.event import EventMapper


def test_task_sse_preserves_correlation() -> None:
    task = AgentTask(
        id="task-1",
        run_id="run-1",
        plan_version=1,
        task_key="topic",
        description="topic",
        objective="topic",
        capability_profile=CapabilityProfile.RESEARCH_READONLY,
        acceptance_criteria=["evidence"],
        status=TaskStatus.RUNNING,
    )
    event = ResearchTaskEvent(
        session_id="session-1",
        run_id="run-1",
        task_id=task.id,
        attempt_id="attempt-1",
        agent_id="worker-1",
        sequence_no=7,
        status=TaskStatus.RUNNING,
        task=task,
    )

    mapped = EventMapper.event_to_sse_event(event)
    payload = mapped.data.model_dump()

    assert mapped.event == "research_task"
    assert payload["sequence_no"] == 7
    assert payload["run_id"] == "run-1"
    assert payload["task_id"] == "task-1"
    assert payload["attempt_id"] == "attempt-1"
    assert payload["agent_id"] == "worker-1"
    assert payload["status"] == TaskStatus.RUNNING


def test_source_sse_excludes_object_storage_key() -> None:
    source = ResearchSource(
        run_id="run-1",
        canonical_url="https://example.com/",
        original_url="https://example.com/",
        title="Example",
        domain="example.com",
        content_type="text/html",
        content_hash="hash",
        object_storage_key="research/run-1/hash",
    )
    event = ResearchSourceEvent(
        session_id="session-1",
        run_id="run-1",
        source=source,
    )

    mapped = EventMapper.event_to_sse_event(event)

    assert mapped.event == "research_source"
    assert "object_storage_key" not in mapped.data.model_dump()["source"]
