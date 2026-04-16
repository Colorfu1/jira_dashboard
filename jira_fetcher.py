import os
import re
import requests
from collections import Counter, defaultdict

JIRA_BASE_URL = os.getenv("JIRA_BASE_URL", "https://your-jira.example.com").rstrip("/")
JIRA_API_URL = f"{JIRA_BASE_URL}/rest/api/2"
JIRA_BROWSE_URL = f"{JIRA_BASE_URL}/browse"
USERNAME = os.getenv("JIRA_USERNAME", "")
API_KEY = os.getenv("JIRA_API_TOKEN", "")
HEADERS = {"Authorization": f"Bearer {API_KEY}"}

ADD6_PREFIXES = {"mol-m", "ml3-vp"}
CARD_MIN_COUNT = 1  # show all linked cards as columns

JQL = (
    f'(assignee="{USERNAME}" OR comment ~ "{USERNAME}") AND status NOT IN '
    '("关闭归档","已关闭","Closed","Done","Resolved") '
    'ORDER BY updated DESC'
)


def _classify_vehicle(desc):
    if not desc:
        return ("未知", "")
    for line in desc.split("\n"):
        if "测试车次" in line:
            m = re.search(r"(?:prod|test)\.([a-zA-Z0-9\-]+)\.(\d+)", line)
            if m:
                prefix, num = m.group(1), m.group(2)
                full_id = f"{prefix}.{num}"
                platform = "add6" if prefix in ADD6_PREFIXES else "x86"
                return (platform, full_id)
            break
    return ("未知", "")


def _get_test_time(desc):
    if not desc:
        return ("", "")
    for line in desc.split("\n"):
        if "测试时间" in line:
            clean = re.sub(r"\{color[^}]*\}", "", line)
            m = re.search(
                r"(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2})\s*~\s*"
                r"(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2})",
                clean,
            )
            if m:
                return (f"{m.group(1)} {m.group(2)}", m.group(1))
            m2 = re.search(r"(\d{4}-\d{2}-\d{2})", clean)
            if m2:
                return (m2.group(1), m2.group(1))
            break
    return ("", "")


def _get_parent_cards(issuelinks):
    cards = []
    for l in issuelinks:
        if "outwardIssue" in l and "child" in l["type"].get("outward", ""):
            oi = l["outwardIssue"]
            cards.append((oi["key"], oi["fields"].get("summary", "")))
    return cards


def _get_at_comments(comments_data, username=None):
    username = username or USERNAME
    results = []
    has_replied = False
    comments = comments_data.get("comments", []) if comments_data else []
    for c in comments:
        body = c.get("body", "")
        author_name = c.get("author", {}).get("name", "")
        if author_name == username:
            has_replied = True
        if username in body:
            author = c.get("author", {}).get("displayName", "")
            created = c.get("created", "")[:10]
            clean_body = re.sub(r"\{color[^}]*\}", "", body)
            clean_body = re.sub(r"!\S+\|[^!]*!", "[图片]", clean_body)
            clean_body = clean_body.strip()
            results.append(f"[{created} {author}] {clean_body}")
    return ("\n---\n".join(results) if results else "", has_replied)


def fetch_all_issues():
    """Fetch all open issues from Jira and return structured data."""
    if not USERNAME:
        raise RuntimeError(
            "Missing JIRA_USERNAME. Set JIRA_USERNAME before starting the app."
        )
    if not API_KEY:
        raise RuntimeError(
            "Missing JIRA_API_TOKEN. Set JIRA_API_TOKEN before starting the app."
        )

    all_issues = []
    start_at = 0
    max_results = 200
    fields = "key,summary,status,priority,issuetype,created,updated,description,issuelinks,comment"

    while True:
        resp = requests.get(
            f"{JIRA_API_URL}/search",
            headers=HEADERS,
            params={
                "jql": JQL,
                "startAt": start_at,
                "maxResults": max_results,
                "fields": fields,
            },
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        issues = data.get("issues", [])
        all_issues.extend(issues)
        total = data.get("total", 0)
        if start_at + len(issues) >= total or not issues:
            break
        start_at += len(issues)

    # Filter to 故障 only (exclude 积累卡/任务)
    all_issues = [
        i
        for i in all_issues
        if i["fields"].get("issuetype", {}).get("name", "") == "故障"
    ]

    # Collect parent cards
    all_parent_cards = Counter()
    card_summaries = {}
    for issue in all_issues:
        for ck, cs in _get_parent_cards(issue["fields"].get("issuelinks", [])):
            all_parent_cards[ck] += 1
            if ck not in card_summaries:
                card_summaries[ck] = cs

    card_columns = [
        (k, v) for k, v in all_parent_cards.most_common() if v >= CARD_MIN_COUNT
    ]
    card_keys_set = {k for k, _ in card_columns}

    # Build rows
    rows = []
    for issue in all_issues:
        f = issue["fields"]
        desc = f.get("description", "") or ""
        platform, vehicle_id = _classify_vehicle(desc)
        test_time, test_date = _get_test_time(desc)
        parent_cards = _get_parent_cards(f.get("issuelinks", []))
        card_membership = {ck for ck, _ in parent_cards if ck in card_keys_set}
        other_cards = [
            f"{ck}: {cs}" for ck, cs in parent_cards if ck not in card_keys_set
        ]
        at_comments, has_replied = _get_at_comments(f.get("comment", {}))

        rows.append(
            {
                "key": issue["key"],
                "url": f"{JIRA_BROWSE_URL}/{issue['key']}",
                "summary": f.get("summary", ""),
                "status": f.get("status", {}).get("name", ""),
                "priority": f.get("priority", {}).get("name", ""),
                "created": f.get("created", "")[:10],
                "updated": f.get("updated", "")[:10],
                "platform": platform,
                "vehicle_id": vehicle_id,
                "test_time": test_time,
                "test_date": test_date,
                "card_membership": list(card_membership),
                "other_cards": "; ".join(other_cards),
                "at_comments": at_comments,
                "diagnosed": has_replied,
            }
        )

    # Group by date
    date_groups = defaultdict(list)
    for r in rows:
        d = r["test_date"] if r["test_date"] else "未知日期"
        date_groups[d].append(r)

    dates_sorted = sorted(
        [d for d in date_groups if d != "未知日期"], reverse=True
    )
    if "未知日期" in date_groups:
        dates_sorted.append("未知日期")

    # Card column info
    card_info = [
        {"key": ck, "summary": card_summaries.get(ck, ""), "count": cv}
        for ck, cv in card_columns
    ]

    return {
        "rows": rows,
        "total": len(rows),
        "date_groups": dict(date_groups),
        "dates": dates_sorted,
        "card_columns": card_info,
    }
