import importlib


reexport_path = "ddtrace.internal.utils.compat"
reexported_module = importlib.import_module(reexport_path)
for name in dir(reexported_module):
    locals()[name] = getattr(reexported_module, name)
