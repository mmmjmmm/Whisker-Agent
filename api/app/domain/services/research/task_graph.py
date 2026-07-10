from typing import Any

from app.domain.models.agent_run import RunBudget, TaskStatus
from app.domain.models.research import ResearchTaskSpec


TERMINAL_TASK_STATUSES = {
    TaskStatus.COMPLETED,
    TaskStatus.FAILED,
    TaskStatus.SKIPPED,
    TaskStatus.CANCELLED,
    TaskStatus.TIMED_OUT,
    TaskStatus.INTERRUPTED,
}


class InvalidTaskGraph(ValueError):
    def __init__(self, code: str, details: dict[str, Any]) -> None:
        self.code = code
        self.details = details
        super().__init__(f"{code}: {details}")


class TaskGraph:
    def __init__(
            self,
            tasks: dict[str, ResearchTaskSpec],
            budget: RunBudget,
    ) -> None:
        self._tasks = tasks
        self._budget = budget
        self._status = {key: TaskStatus.PENDING for key in tasks}

    @classmethod
    def build(
            cls,
            tasks: list[ResearchTaskSpec],
            budget: RunBudget,
    ) -> "TaskGraph":
        if not tasks:
            raise InvalidTaskGraph("empty_graph", {})

        keys = [task.key for task in tasks]
        if len(keys) != len(set(keys)):
            raise InvalidTaskGraph("duplicate_task_key", {"keys": keys})

        by_key = {task.key: task for task in tasks}
        for task in tasks:
            unknown = sorted(set(task.dependencies) - set(by_key))
            if unknown:
                raise InvalidTaskGraph(
                    "unknown_dependency",
                    {"task": task.key, "dependencies": unknown},
                )
            if task.key in task.dependencies:
                raise InvalidTaskGraph("self_dependency", {"task": task.key})

        if len(tasks) > budget.max_tasks:
            raise InvalidTaskGraph(
                "max_tasks",
                {"actual": len(tasks), "limit": budget.max_tasks},
            )

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(key: str) -> None:
            if key in visiting:
                raise InvalidTaskGraph("cycle", {"task": key})
            if key in visited:
                return
            visiting.add(key)
            for dependency in by_key[key].dependencies:
                visit(dependency)
            visiting.remove(key)
            visited.add(key)

        for key in by_key:
            visit(key)

        depths: dict[str, int] = {}

        def depth(key: str) -> int:
            if key not in depths:
                dependencies = by_key[key].dependencies
                depths[key] = (
                    1
                    if not dependencies
                    else 1 + max(depth(item) for item in dependencies)
                )
            return depths[key]

        actual_depth = max(depth(key) for key in by_key)
        if actual_depth > budget.max_graph_depth:
            raise InvalidTaskGraph(
                "max_graph_depth",
                {"actual": actual_depth, "limit": budget.max_graph_depth},
            )

        return cls(by_key, budget)

    def add_repair_tasks(self, tasks: list[ResearchTaskSpec]) -> None:
        candidate = TaskGraph.build(
            [*self._tasks.values(), *tasks],
            self._budget,
        )
        for key, status in self._status.items():
            candidate._status[key] = status
        self._tasks = candidate._tasks
        self._status = candidate._status

    def ready_tasks(self) -> list[ResearchTaskSpec]:
        ready = [
            task
            for key, task in self._tasks.items()
            if self._status[key] in {TaskStatus.PENDING, TaskStatus.READY}
            and all(
                self._status[dependency] == TaskStatus.COMPLETED
                for dependency in task.dependencies
            )
        ]
        return sorted(ready, key=lambda item: (-item.priority, item.key))

    def start(self, key: str) -> None:
        task = self._task(key)
        status = self._status[key]
        if status not in {TaskStatus.PENDING, TaskStatus.READY}:
            raise InvalidTaskGraph(
                "invalid_start_state",
                {"task": key, "status": status.value},
            )
        if any(
            self._status[dependency] != TaskStatus.COMPLETED
            for dependency in task.dependencies
        ):
            raise InvalidTaskGraph("dependencies_not_completed", {"task": key})
        self._status[key] = TaskStatus.RUNNING

    def complete(self, key: str) -> None:
        self._task(key)
        if self._status[key] != TaskStatus.RUNNING:
            raise InvalidTaskGraph(
                "invalid_complete_state",
                {"task": key, "status": self._status[key].value},
            )
        self._status[key] = TaskStatus.COMPLETED

    def fail(self, key: str) -> list[str]:
        return self.terminate(key, TaskStatus.FAILED)

    def terminate(self, key: str, status: TaskStatus) -> list[str]:
        self._task(key)
        if status not in {
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
            TaskStatus.TIMED_OUT,
            TaskStatus.INTERRUPTED,
        }:
            raise InvalidTaskGraph(
                "invalid_terminal_state",
                {"task": key, "status": status.value},
            )

        self._status[key] = status
        blocked = {key}
        skipped: list[str] = []
        changed = True
        while changed:
            changed = False
            for task_key, task in self._tasks.items():
                current = self._status[task_key]
                if current in TERMINAL_TASK_STATUSES or current == TaskStatus.RUNNING:
                    continue
                if any(dependency in blocked for dependency in task.dependencies):
                    self._status[task_key] = TaskStatus.SKIPPED
                    blocked.add(task_key)
                    skipped.append(task_key)
                    changed = True
        return skipped

    def terminal(self) -> bool:
        return all(
            status in TERMINAL_TASK_STATUSES
            for status in self._status.values()
        )

    def status(self, key: str) -> TaskStatus:
        self._task(key)
        return self._status[key]

    def task(self, key: str) -> ResearchTaskSpec:
        return self._task(key)

    def statuses(self) -> dict[str, TaskStatus]:
        return dict(self._status)

    def _task(self, key: str) -> ResearchTaskSpec:
        try:
            return self._tasks[key]
        except KeyError as exc:
            raise InvalidTaskGraph("unknown_task", {"task": key}) from exc

