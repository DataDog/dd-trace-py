from ddtrace.appsec.iast import enable_iast_propagation


enable_iast_propagation()

from .views import create_app_patch_all  # noqa: E402


app = create_app_patch_all()

if __name__ == "__main__":
    app.run()
