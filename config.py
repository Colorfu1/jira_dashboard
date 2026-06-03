"""Public configuration for the Jira dashboard.

All sensitive values are read from environment variables so this file can
be checked in safely. Set the variables before starting the app, e.g. in
a ``.env`` file or via your shell:

    export JIRA_BASE_URL="https://your-jira.example.com"
    export JIRA_USERNAME="your.username"
    export JIRA_API_TOKEN="your-api-token"
    # optional:
    export JIRA_DASHBOARD_CACHE="/path/to/jira_cache.json"
    export JIRA_INCREMENTAL_LOOKBACK_MINUTES="30"

When adding a new secret or environment-specific setting, mirror the
change in the private ``jira_dashboard/config.py`` so the two repos stay
in sync.
"""

import os
from pathlib import Path

JIRA_BASE_URL = os.getenv("JIRA_BASE_URL", "https://your-jira.example.com").rstrip("/")
USERNAME = os.getenv("JIRA_USERNAME", "")
API_KEY = os.getenv("JIRA_API_TOKEN", "")
CACHE_PATH = Path(os.getenv("JIRA_DASHBOARD_CACHE", Path(__file__).with_name("jira_cache.json")))
INCREMENTAL_LOOKBACK_MINUTES = int(os.getenv("JIRA_INCREMENTAL_LOOKBACK_MINUTES", "30"))


def validate():
    """Fail fast at startup if required credentials are missing."""
    if not USERNAME:
        raise RuntimeError(
            "Missing JIRA_USERNAME. Set JIRA_USERNAME before starting the app."
        )
    if not API_KEY:
        raise RuntimeError(
            "Missing JIRA_API_TOKEN. Set JIRA_API_TOKEN before starting the app."
        )
