COMPONENT_VALUE = "unittest"
FRAMEWORK = "unittest"
KIND = "test"

TEST_OPERATION_NAME = "unittest.test"
SUITE_OPERATION_NAME = "unittest.test_suite"
SESSION_OPERATION_NAME = "unittest.test_session"
MODULE_OPERATION_NAME = "unittest.test_module"


# EFD (Early Flake Detection) outcome constants
class EFD_RETRY_OUTCOMES:
    EFD_ATTEMPT_PASSED = "dd_efd_attempt_passed"
    EFD_ATTEMPT_FAILED = "dd_efd_attempt_failed"
    EFD_ATTEMPT_SKIPPED = "dd_efd_attempt_skipped"
    EFD_FINAL_PASSED = "dd_efd_final_passed"
    EFD_FINAL_FAILED = "dd_efd_final_failed"
    EFD_FINAL_SKIPPED = "dd_efd_final_skipped"
    EFD_FINAL_FLAKY = "dd_efd_final_flaky"


# ATR (Automatic Test Retry) outcome constants
class ATR_RETRY_OUTCOMES:
    ATR_ATTEMPT_PASSED = "dd_atr_attempt_passed"
    ATR_ATTEMPT_FAILED = "dd_atr_attempt_failed"
    ATR_ATTEMPT_SKIPPED = "dd_atr_attempt_skipped"
    ATR_FINAL_PASSED = "dd_atr_final_passed"
    ATR_FINAL_FAILED = "dd_atr_final_failed"


# Attempt to Fix outcome constants
class ATTEMPT_TO_FIX_OUTCOMES:
    ATTEMPT_PASSED = "dd_fix_attempt_passed"
    ATTEMPT_FAILED = "dd_fix_attempt_failed"
    ATTEMPT_SKIPPED = "dd_fix_attempt_skipped"
    FINAL_PASSED = "dd_fix_final_passed"
    FINAL_FAILED = "dd_fix_final_failed"
