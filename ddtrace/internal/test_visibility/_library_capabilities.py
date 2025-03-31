import typing as t


class LibraryCapabilities:
    TAGS = {
        "early_flake_detection": "_dd.library_capabilities.early_flake_detection",
        "auto_test_retries": "_dd.library_capabilities.auto_test_retries",
        "test_impact_analysis": "_dd.library_capabilities.test_impact_analysis",
        "test_management_quarantine": "_dd.library_capabilities.test_management.quarantine",
        "test_management_disable": "_dd.library_capabilities.test_management.disable",
        "test_management_attempt_to_fix": "_dd.library_capabilities.test_management.attempt_to_fix",
    }

    def __init__(self, **capabilities: t.Optional[str]):
        self._tags: t.Dict[str, str] = {}

        for capability, version in capabilities.items():
            if version is not None:
                tag = LibraryCapabilities.TAGS[capability]
                self._tags[tag] = version

    def tags(self) -> t.Dict[str, str]:
        return self._tags
