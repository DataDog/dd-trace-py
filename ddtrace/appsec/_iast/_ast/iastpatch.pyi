ALLOWED_FIRST_PARTY_ALLOWLIST: int
ALLOWED_STATIC_ALLOWLIST: int
ALLOWED_USER_ALLOWLIST: int
DENIED_BUILTINS_DENYLIST: int
DENIED_NOT_FOUND: int
DENIED_STATIC_DENYLIST: int
DENIED_USER_DENYLIST: int

def build_list_from_env(*args, **kwargs): ...
def get_user_allowlist(*args, **kwargs): ...
def should_iast_patch(*args, **kwargs): ...
