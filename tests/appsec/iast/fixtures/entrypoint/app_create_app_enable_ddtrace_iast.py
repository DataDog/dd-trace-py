from .views import create_app_enable_iast_propagation


app = create_app_enable_iast_propagation()

if __name__ == "__main__":
    app.run()
