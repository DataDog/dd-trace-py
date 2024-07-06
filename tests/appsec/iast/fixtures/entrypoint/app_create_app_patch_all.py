from .views import create_app_patch_all


app = create_app_patch_all()

if __name__ == "__main__":
    app.run()
