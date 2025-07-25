"""Automatically starts a collector when imported. this module is loaded by ddtrace/bootstrap/preload.py"""
from ddtrace.appsec.iast import enable_iast_propagation
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)
log.debug("Enabling the IAST by auto import")

enable_iast_propagation()
