"""Configuration for fast file-level coverage."""

import os
import typing as t


def is_fast_coverage_enabled() -> bool:
    """Check if fast file-level coverage is enabled via environment variable."""
    env_value = os.environ.get('_DD_CIVISIBILITY_FAST_COVERAGE', '').lower()
    return env_value in ('1', 'true', 'yes', 'on')


class FastCoverageConfig:
    """Configuration for fast file-level coverage."""
    
    def __init__(self):
        self.enabled = is_fast_coverage_enabled()
        self.track_files_only = True
        self.minimal_instrumentation = True
        
    def __bool__(self) -> bool:
        return self.enabled


# Global instance
FAST_COVERAGE_CONFIG = FastCoverageConfig()
