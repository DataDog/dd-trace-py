"""Automatically starts a collector when imported."""
from ddtrace.appsec._iast.main import patch_iast
from ddtrace.appsec.iast import enable_iast_propagation
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)
log.debug("Enabling the IAST by auto import")

patch_iast()
enable_iast_propagation()
