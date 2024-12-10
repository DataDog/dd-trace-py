class App:
    def __init__(self):
        self.views = {}

    def route(self, path):
        def wrapper(view):
            self.views[path] = view

        return wrapper


app = App()


@app.route("/home")
def home():
    pass
