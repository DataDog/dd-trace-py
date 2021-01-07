"""
The FastAPI integration will trace requests to and from FastAPI.

"""
from ...utils.importlib import require_modules

required_modules = ["fastapi"]

with require_modules(required_modules) as missing_modules:
	if not missing_modules:
		from .patch import patch, unpatch

		__all__ = ["patch", "unpatch"]
