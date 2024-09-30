import dataclasses

from ddtrace.ext.test_visibility import api as ext_api


@dataclasses.dataclass(frozen=True)
class InternalTestId(ext_api.TestId):
    retry_number: int = 0

    def __repr__(self):
        return "TestId(module={}, suite={}, test={}, parameters={}, retry_number={})".format(
            self.parent_id.parent_id.name,
            self.parent_id.name,
            self.name,
            self.parameters,
            self.retry_number,
        )
