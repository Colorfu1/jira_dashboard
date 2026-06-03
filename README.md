# Jira Dashboard

A small Flask dashboard for Jira issues.

## Setup

1. Install dependencies:

```bash
pip install flask requests
```

2. Export environment variables (read by `config.py`):

```bash
export JIRA_BASE_URL="https://your-jira.example.com"
export JIRA_USERNAME="your_username"
export JIRA_API_TOKEN="your_api_token"
# optional overrides:
export JIRA_DASHBOARD_CACHE="/path/to/jira_cache.json"
export JIRA_INCREMENTAL_LOOKBACK_MINUTES="30"
```

All sensitive settings live in `config.py`. To add a new credential or
environment-specific setting, declare it there (with an `os.getenv` and a
sensible default) and import it from the consuming module.

3. Run:

```bash
python app.py
```

The app starts on `http://127.0.0.1:5050`.
