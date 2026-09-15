from typing import Any
from typing import cast

import pytest

from tests.contrib.temporalio.sample_app.activities import compose_greeting
from tests.contrib.temporalio.sample_app.starter import TASK_QUEUE
from tests.contrib.temporalio.sample_app.starter import run


class _WorkflowHandle:
    def __init__(self) -> None:
        self.salutation = "Hello"
        self.queries: list[Any] = []
        self.signals: list[tuple[Any, str]] = []

    async def query(self, query: Any) -> str:
        self.queries.append(query)
        return self.salutation

    async def signal(self, signal: Any, salutation: str) -> None:
        self.signals.append((signal, salutation))
        self.salutation = salutation

    async def result(self) -> str:
        return await compose_greeting("Temporal", self.salutation)


class _Client:
    def __init__(self) -> None:
        self.handle = _WorkflowHandle()
        self.start: tuple[Any, str, str, str] | None = None

    async def start_workflow(
        self,
        workflow: Any,
        name: str,
        **kwargs: str,
    ) -> _WorkflowHandle:
        self.start = (workflow, name, kwargs["id"], kwargs["task_queue"])
        return self.handle


@pytest.mark.asyncio
async def test_starter_runs_customer_workflow_flow() -> None:
    client = _Client()

    initial_salutation, greeting = await run(cast(Any, client), "sample-workflow")

    assert initial_salutation == "Hello"
    assert greeting == "Welcome, Temporal!"
    assert client.start is not None
    _, name, workflow_id, task_queue = client.start
    assert name == "Temporal"
    assert workflow_id == "sample-workflow"
    assert task_queue == TASK_QUEUE
    assert len(client.handle.queries) == 1
    assert len(client.handle.signals) == 1
