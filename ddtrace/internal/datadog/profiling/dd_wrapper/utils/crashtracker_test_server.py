from flask import Flask, request, jsonify
from werkzeug.utils import secure_filename
import os

app = Flask(__name__)

@app.route('/telemetry/proxy/api/v2/apmtelemetry', methods=['POST'])
def telemetry():
    # Write the entire body to files/telemetry.json
    with open('./files/telemetry.json', 'w') as f:
        f.write(request.get_data(as_text=True))
    return jsonify({"message": "Telemetry received"}), 200

@app.route('/profiling/v1/input', methods=['POST'])
def profiling_input():
    for key in request.files.keys():
        file = request.files[key]
        filename = secure_filename(file.filename)
        file.save(os.path.join('./files', filename))
    return jsonify({"message": "Files received"}), 200

if __name__ == '__main__':
    app.run(port=8000, debug=True)
