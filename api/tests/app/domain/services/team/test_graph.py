import pytest

from app.domain.models.team import (
    PlannedTask,
    PlannedTaskGraph,
    TaskGraphStatus,
    TeamCapability,
    TeamTaskStatus,
)
from app.domain.services.team.graph import (
    TaskGraphError,
    build_task_graph,
    finalize_graph,
    propagate_skipped,
    ready_tasks,
)


def node(task_id: str, deps: list[str] | None = None) -> PlannedTask:
    return PlannedTask(
        id=task_id,
        description=task_id,
        dependencies=deps or [],
        capability=TeamCapability.ANALYSIS,
        success_criteria="done",
    )


def test_rejects_cycle():
    plan = PlannedTaskGraph(
        title="t",
        goal="g",
        tasks=[node("a", ["b"]), node("b", ["a"])],
    )

    with pytest.raises(TaskGraphError, match="cycle"):
        build_task_graph(plan, max_tasks=5)


def test_rejects_unknown_dependency():
    plan = PlannedTaskGraph(title="t", goal="g", tasks=[node("a", ["missing"])])

    with pytest.raises(TaskGraphError, match="unknown dependency"):
        build_task_graph(plan, max_tasks=5)


def test_rejects_self_and_duplicate_dependencies():
    with pytest.raises(TaskGraphError, match="self dependency"):
        build_task_graph(
            PlannedTaskGraph(title="t", goal="g", tasks=[node("a", ["a"])]),
            max_tasks=5,
        )

    with pytest.raises(TaskGraphError, match="duplicate dependency"):
        build_task_graph(
            PlannedTaskGraph(
                title="t",
                goal="g",
                tasks=[node("a"), node("b", ["a", "a"])],
            ),
            max_tasks=5,
        )


def test_rejects_too_many_tasks_and_duplicate_ids():
    too_many = PlannedTaskGraph(
        title="t",
        goal="g",
        tasks=[node(str(index)) for index in range(6)],
    )
    with pytest.raises(TaskGraphError, match="task count"):
        build_task_graph(too_many, max_tasks=5)

    duplicate = PlannedTaskGraph(
        title="t",
        goal="g",
        tasks=[node("same"), node("same")],
    )
    with pytest.raises(TaskGraphError, match="duplicate"):
        build_task_graph(duplicate, max_tasks=5)


def test_ready_tasks_preserve_planner_order_and_skip_blocked_descendants():
    graph = build_task_graph(
        PlannedTaskGraph(
            title="t",
            goal="g",
            tasks=[node("a"), node("b"), node("c", ["a"]), node("d", ["c"])],
        ),
        max_tasks=5,
    )

    assert [task.id for task in ready_tasks(graph)] == ["a", "b"]
    graph.task_by_id("a").status = TeamTaskStatus.FAILED
    changed = propagate_skipped(graph)

    assert [task.id for task in changed] == ["c", "d"]
    assert all(task.error == "dependency_failed" for task in changed)


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        ([TeamTaskStatus.COMPLETED], TaskGraphStatus.COMPLETED),
        (
            [TeamTaskStatus.COMPLETED, TeamTaskStatus.FAILED],
            TaskGraphStatus.PARTIAL,
        ),
        ([TeamTaskStatus.FAILED], TaskGraphStatus.FAILED),
        ([TeamTaskStatus.CANCELLED], TaskGraphStatus.CANCELLED),
    ],
)
def test_finalize_graph_uses_explicit_terminal_statuses(statuses, expected):
    graph = build_task_graph(
        PlannedTaskGraph(
            title="t",
            goal="g",
            tasks=[node(str(index)) for index in range(len(statuses))],
        ),
        max_tasks=5,
    )
    for task, status in zip(graph.tasks, statuses, strict=True):
        task.status = status

    assert finalize_graph(graph) is expected
    assert graph.status is expected
