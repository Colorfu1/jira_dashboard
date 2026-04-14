import threading
from datetime import datetime

from flask import Flask, jsonify, render_template

from jira_fetcher import fetch_all_issues

app = Flask(__name__)

# In-memory cache
_cache = {"data": None, "last_updated": None, "loading": False}
_lock = threading.Lock()


def _load_data():
    with _lock:
        if _cache["loading"]:
            return
        _cache["loading"] = True

    try:
        data = fetch_all_issues()
        with _lock:
            _cache["data"] = data
            _cache["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    finally:
        with _lock:
            _cache["loading"] = False


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/data")
def api_data():
    with _lock:
        if _cache["data"] is None:
            return jsonify({"error": "Data not loaded yet, please refresh"}), 503
        return jsonify(
            {
                "data": _cache["data"],
                "last_updated": _cache["last_updated"],
            }
        )


@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    with _lock:
        if _cache["loading"]:
            return jsonify({"status": "already_loading"})

    thread = threading.Thread(target=_load_data, daemon=True)
    thread.start()
    return jsonify({"status": "started"})


@app.route("/api/status")
def api_status():
    with _lock:
        return jsonify(
            {
                "loading": _cache["loading"],
                "last_updated": _cache["last_updated"],
                "total": _cache["data"]["total"] if _cache["data"] else 0,
            }
        )


if __name__ == "__main__":
    print("Loading initial data from Jira...")
    _load_data()
    print(f"Loaded {_cache['data']['total']} issues. Starting server...")
    app.run(host="0.0.0.0", port=5050, debug=False)
