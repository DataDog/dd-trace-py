from datetime import timedelta
from typing import cast

from temporalio import workflow


with workflow.unsafe.imports_passed_through():
    from tests.contrib.temporalio.sample_app.activities import compose_greeting


@workflow.defn
class GreetingWorkflow:
    def __init__(self) -> None:
        self._salutation = "Hello"
        self._approved = False

    @workflow.run
    async def run(self, name: str) -> str:
        await workflow.wait_condition(lambda: self._approved)
        return cast(
            str,
            await workflow.execute_activity(
                compose_greeting,
                args=[name, self._salutation],
                start_to_close_timeout=timedelta(seconds=10),
            ),
        )

    @workflow.query
    def current_salutation(self) -> str:
        return self._salutation

    @workflow.signal
    def approve(self, salutation: str) -> None:
        self._salutation = salutation
        self._approved = True
