from collections import deque

from app.domain.models.team import (
    PlannedTaskGraph,
    TaskGraph,
    TaskGraphStatus,
    TeamTask,
    TeamTaskStatus,
)


class TaskGraphError(ValueError):
    pass


def build_task_graph(plan: PlannedTaskGraph, max_tasks: int) -> TaskGraph:
    if not 1 <= len(plan.tasks) <= max_tasks:
        raise TaskGraphError(f"task count must be between 1 and {max_tasks}")

    task_ids = [task.id for task in plan.tasks]
    if len(task_ids) != len(set(task_ids)):
        raise TaskGraphError("duplicate task id")

    known_ids = set(task_ids)
    indegree = {task_id: 0 for task_id in task_ids}
    children: dict[str, list[str]] = {task_id: [] for task_id in task_ids}
    for task in plan.tasks:
        if task.id in task.dependencies:
            raise TaskGraphError("self dependency")
        if len(task.dependencies) != len(set(task.dependencies)):
            raise TaskGraphError(f"duplicate dependency in task: {task.id}")
        for dependency in task.dependencies:
            if dependency not in known_ids:
                raise TaskGraphError(f"unknown dependency: {dependency}")
            indegree[task.id] += 1
            children[dependency].append(task.id)

    queue = deque(task_id for task_id, degree in indegree.items() if degree == 0)
    visited = 0
    while queue:
        current = queue.popleft()
        visited += 1
        for child in children[current]:
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)
    if visited != len(task_ids):
        raise TaskGraphError("cycle detected")

    return TaskGraph(
        title=plan.title,
        goal=plan.goal,
        tasks=[TeamTask(**task.model_dump()) for task in plan.tasks],
    )


def ready_tasks(graph: TaskGraph) -> list[TeamTask]:
    completed = {
        task.id for task in graph.tasks if task.status is TeamTaskStatus.COMPLETED
    }
    return [
        task
        for task in graph.tasks
        if task.status is TeamTaskStatus.PENDING
        and set(task.dependencies).issubset(completed)
    ]


def propagate_skipped(graph: TaskGraph) -> list[TeamTask]:
    blocked = {
        task.id
        for task in graph.tasks
        if task.status
        in {
            TeamTaskStatus.FAILED,
            TeamTaskStatus.SKIPPED,
            TeamTaskStatus.CANCELLED,
        }
    }
    changed: list[TeamTask] = []
    progress = True
    while progress:
        progress = False
        for task in graph.tasks:
            if (
                task.status is TeamTaskStatus.PENDING
                and blocked.intersection(task.dependencies)
            ):
                task.status = TeamTaskStatus.SKIPPED
                task.error = "dependency_failed"
                blocked.add(task.id)
                changed.append(task)
                progress = True
    return changed


def finalize_graph(graph: TaskGraph) -> TaskGraphStatus:
    completed = sum(
        task.status is TeamTaskStatus.COMPLETED for task in graph.tasks
    )
    failed = any(
        task.status in {TeamTaskStatus.FAILED, TeamTaskStatus.SKIPPED}
        for task in graph.tasks
    )
    cancelled = any(
        task.status is TeamTaskStatus.CANCELLED for task in graph.tasks
    )

    if cancelled:
        graph.status = TaskGraphStatus.CANCELLED
    elif completed == len(graph.tasks):
        graph.status = TaskGraphStatus.COMPLETED
    elif completed > 0 and failed:
        graph.status = TaskGraphStatus.PARTIAL
    else:
        graph.status = TaskGraphStatus.FAILED
    return graph.status
