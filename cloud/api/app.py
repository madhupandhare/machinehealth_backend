"""
cloud/api/app.py — Flask application factory
Loads .env, registers API + demo blueprints, serves React build.
"""
import logging
import os
import sys
sys.path.insert(0, ".")

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from flask import Flask, jsonify, send_from_directory
from cloud.api.routes      import api_bp
from cloud.api.demo_routes import demo_bp

REACT_BUILD = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "../../dashboard/react_build"
)

def create_app() -> Flask:
    app = Flask(__name__, static_folder=None)
    app.config["JSON_SORT_KEYS"] = False

    app.register_blueprint(api_bp)
    app.register_blueprint(demo_bp)

    if os.path.isdir(REACT_BUILD):
        @app.route("/", defaults={"path": ""})
        @app.route("/<path:path>")
        def serve_react(path):
            if path.startswith("api/"):
                return jsonify({"error": "not found"}), 404
            full = os.path.join(REACT_BUILD, path)
            if path and os.path.isfile(full):
                return send_from_directory(REACT_BUILD, path)
            return send_from_directory(REACT_BUILD, "index.html")
    else:
        @app.route("/")
        def index():
            return jsonify({
                "status": "IMHM Flask API running",
                "endpoints": ["/api/machines", "/api/demo/inject", "/api/demo/status"],
            })

    return app

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    )

    create_app().run(host="0.0.0.0", threaded=True)
