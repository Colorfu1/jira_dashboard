# Jira Dashboard

A small Flask dashboard for Jira issues.

## Setup

1. Install dependencies:

```bash
pip install flask requests
```

2. Export environment variables:

```bash
export JIRA_BASE_URL="https://your-jira.example.com"
export JIRA_USERNAME="your_username"
export JIRA_API_TOKEN="your_api_token"
```

3. Run:

```bash
python app.py
```

The app starts on `http://127.0.0.1:5050`.
