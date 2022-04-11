from ddtrace.internal.module import register_post_run_module_hook


def post_run_module_hook(module):
    assert module.__name__ == "__main__"
    assert module.post_run_module


register_post_run_module_hook(post_run_module_hook)
