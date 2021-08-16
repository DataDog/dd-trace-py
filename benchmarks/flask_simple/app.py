import random

from flask import Flask
from flask import render_template_string


app = Flask(__name__)


@app.route("/")
def index():
    rand_numbers = [random.random() for _ in range(20)]
    return render_template_string(
        """
<!DOCTYPE html>
<html>
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Hello World!</title>
  </head>
  <body>
  <section class="section">
    <div class="container">
      <h1 class="title">
        Hello World
      </h1>
      <p class="subtitle">
        My first website
      </p>
      <ul>
        {% for i in rand_numbers %}
          <li>{{ i }}</li>
        {% endfor %}
      </ul>
    </div>
  </section>
  </body>
</html>
    """,
        rand_numbers=rand_numbers,
    )
