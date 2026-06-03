import threading
import traceback
from datetime import datetime

from flask import Flask, jsonify, render_template, request

from jira_fetcher import fetch_all_issues

app = Flask(__name__)

# In-memory cache
_cache = {"data": None, "last_updated": None, "loading": False, "error": None}
_lock = threading.Lock()


def _set_data(data):
    _cache["data"] = data
    _cache["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _cache["error"] = None


def _load_data(mode="incremental"):
    with _lock:
        if _cache["loading"]:
            return
        _cache["loading"] = True

    try:
        data = fetch_all_issues(mode=mode)
        with _lock:
            _set_data(data)
    except Exception as exc:
        traceback.print_exc()
        with _lock:
            _cache["error"] = str(exc)
    finally:
        with _lock:
            _cache["loading"] = False


def _load_cached_data_on_startup():
    try:
        data = fetch_all_issues(mode="rebuild")
        with _lock:
            _set_data(data)
    except Exception:
        traceback.print_exc()


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/data")
def api_data():
    with _lock:
        if _cache["data"] is None:
            return jsonify(
                {
                    "error": _cache["error"]
                    or "Data not loaded yet, please refresh",
                }
            ), 503
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

    mode = request.args.get("mode", "incremental")
    if mode not in {"incremental", "full", "rebuild"}:
        return jsonify({"error": "mode must be incremental, full, or rebuild"}), 400

    thread = threading.Thread(target=_load_data, kwargs={"mode": mode}, daemon=True)
    thread.start()
    return jsonify({"status": "started", "mode": mode})


@app.route("/api/status")
def api_status():
    with _lock:
        return jsonify(
            {
                "loading": _cache["loading"],
                "error": _cache["error"],
                "last_updated": _cache["last_updated"],
                "total": _cache["data"]["total"] if _cache["data"] else 0,
            }
        )


if __name__ == "__main__":
    print("Loading cached Jira dashboard data...")
    _load_cached_data_on_startup()
    print("Starting server. Use Refresh for incremental Jira updates.")
    app.run(host="0.0.0.0", port=5050, debug=False)
