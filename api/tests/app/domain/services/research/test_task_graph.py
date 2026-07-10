import pytest

from app.domain.models.agent_run import CapabilityProfile, RunBudget, TaskStatus
from app.domain.models.research import ResearchTaskSpec
from app.domain.services.research.task_graph import InvalidTaskGraph, TaskGraph


def task(
        key: str,
        dependencies: list[str] | None = None,
        priority: int = 0,
) -> ResearchTaskSpec:
    return ResearchTaskSpec(
        key=key,
        description=key,
        objective=key,
        capability_profile=CapabilityProfile.RESEARCH_READONLY,
        dependencies=dependencies or [],
        acceptance_criteria=["has evidence"],
        priority=priority,
    )


def test_graph_rejects_cycle() -> None:
    with pytest.raises(InvalidTaskGraph, match="cycle"):
        TaskGraph.build(
            [task("a", ["b"]), task("b", ["a"])],
            RunBudget(),
        )


def test_ready_tasks_require_completed_dependencies() -> None:
    graph = TaskGraph.build([task("a"), task("b", ["a"])], RunBudget())

    assert [item.key for item in graph.ready_tasks()] == ["a"]

    graph.start("a")
    graph.complete("a")

    assert [item.key for item in graph.ready_tasks()] == ["b"]


def test_ready_tasks_are_sorted_by_priority_then_key() -> None:
    graph = TaskGraph.build(
        [task("b", priority=1), task("a", priority=1), task("c", priority=2)],
        RunBudget(),
    )

    assert [item.key for item in graph.ready_tasks()] == ["c", "a", "b"]


def test_failure_recursively_skips_dependent_tasks() -> None:
    graph = TaskGraph.build(
        [task("a"), task("b", ["a"]), task("c", ["b"]), task("d")],
        RunBudget(),
    )
    graph.start("a")

    skipped = graph.fail("a")

    assert skipped == ["b", "c"]
    assert graph.status("a") == TaskStatus.FAILED
    assert graph.status("b") == TaskStatus.SKIPPED
    assert graph.status("c") == TaskStatus.SKIPPED
    assert graph.status("d") == TaskStatus.PENDING


def test_repair_tasks_share_initial_task_limit() -> None:
    graph = TaskGraph.build([task("a"), task("b")], RunBudget(max_tasks=2))

    with pytest.raises(InvalidTaskGraph) as exc_info:
        graph.add_repair_tasks([task("repair")])

    assert exc_info.value.code == "max_tasks"


def test_graph_rejects_depth_over_budget() -> None:
    with pytest.raises(InvalidTaskGraph) as exc_info:
        TaskGraph.build(
            [task("a"), task("b", ["a"]), task("c", ["b"])],
            RunBudget(max_graph_depth=2),
        )

    assert exc_info.value.code == "max_graph_depth"

