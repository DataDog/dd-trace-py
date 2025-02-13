import inspect

import ddtrace


source_code = inspect.getsource(ddtrace.appsec._common_module_patches.is_iast_request_enabled)
# Split the source code into lines
lines = source_code.split("\n")
body = "\n".join(lines[1:]).strip()
is_body_return_false = body == "return False"

print(f"Always False body: {is_body_return_false}")
