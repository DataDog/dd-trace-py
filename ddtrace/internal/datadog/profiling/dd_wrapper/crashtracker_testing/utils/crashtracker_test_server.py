import os
import shutil

from flask import Flask
from flask import jsonify
from flask import request
from werkzeug.utils import secure_filename


app = Flask(__name__)

# Before we do anything else, define some directories, make them, and clear them
root_dir = "files"
shutil.rmtree(root_dir, ignore_errors=True)
os.makedirs(root_dir, exist_ok=True)


@app.route("/telemetry/proxy/api/v2/apmtelemetry", methods=["POST"])
def telemetry():
    if not hasattr(telemetry, "seq"):
        telemetry.seq = 0
    telemetry.seq += 1
    dir_name = root_dir + "/" + str(telemetry.seq)
    os.makedirs(dir_name, exist_ok=True)

    # Handle request
    with open(dir_name + "/telemetry.json", "w") as f:
        f.write(request.get_data(as_text=True))
    return jsonify({"message": "Telemetry received"}), 200


@app.route("/profiling/v1/input", methods=["POST"])
def profiling_input():
    if not hasattr(profiling_input, "seq"):
        profiling_input.seq = 0
    profiling_input.seq += 1
    dir_name = root_dir + "/" + str(profiling_input.seq)
    os.makedirs(dir_name, exist_ok=True)

    # Handle request
    for key in request.files.keys():
        file = request.files[key]
        filename = secure_filename(file.filename)
        file.save(dir_name + "/" + filename)
    return jsonify({"message": "Files received"}), 200

# Catch-all route, prints the route
@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def catch_all(path):
    return jsonify({"path": path}), 200


if __name__ == "__main__":
    app.run(port=8000)
