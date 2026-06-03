import re
import json
import requests
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from config import (
    API_KEY,
    CACHE_PATH,
    INCREMENTAL_LOOKBACK_MINUTES,
    JIRA_BASE_URL,
    USERNAME,
    validate as validate_config,
)

JIRA_API_URL = f"{JIRA_BASE_URL}/rest/api/2"
JIRA_BROWSE_URL = f"{JIRA_BASE_URL}/browse"
HEADERS = {"Authorization": f"Bearer {API_KEY}"}
SESSION = requests.Session()
# Internal Jira is reachable directly from this environment. Avoid routing
# requests through the local HTTP proxy, which can leave dashboard refreshes
# waiting with no useful progress signal.
SESSION.trust_env = False

ADD6_PREFIXES = {"mol-m", "ml3-vp"}
CARD_MIN_COUNT = 1  # show all linked cards as columns
OPEN_STATUSES_EXCLUDED = {"关闭归档", "已关闭", "Closed", "Done", "Resolved"}

BASE_JQL = f'(assignee="{USERNAME}" OR comment ~ "{USERNAME}")'
OPEN_JQL = (
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
    latest_mention_at = ""
    latest_my_reply_at = ""
    comments = comments_data.get("comments", []) if comments_data else []
    for c in comments:
        body = c.get("body", "")
        author_name = c.get("author", {}).get("name", "")
        if author_name == username:
            has_replied = True
            latest_my_reply_at = max(latest_my_reply_at, c.get("created", ""))
        if username in body:
            latest_mention_at = max(latest_mention_at, c.get("created", ""))
            author = c.get("author", {}).get("displayName", "")
            created = c.get("created", "")[:10]
            clean_body = re.sub(r"\{color[^}]*\}", "", body)
            clean_body = re.sub(r"!\S+\|[^!]*!", "[图片]", clean_body)
            clean_body = clean_body.strip()
            results.append(f"[{created} {author}] {clean_body}")
    return (
        "\n---\n".join(results) if results else "",
        has_replied,
        latest_mention_at,
        latest_my_reply_at,
    )


def _parse_jira_datetime(value):
    if not value:
        return None
    normalized = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", value)
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def _format_jql_datetime(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M")


def _load_cache():
    if not CACHE_PATH.exists():
        return {"issues": {}, "last_full_sync": None, "last_incremental_sync": None}
    try:
        with CACHE_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"issues": {}, "last_full_sync": None, "last_incremental_sync": None}
    data.setdefault("issues", {})
    data.setdefault("last_full_sync", None)
    data.setdefault("last_incremental_sync", None)
    return data


def _save_cache(cache):
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = CACHE_PATH.with_suffix(CACHE_PATH.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)
    tmp_path.replace(CACHE_PATH)


def _search_issues(jql):
    all_issues = []
    start_at = 0
    max_results = 200
    fields = "key,summary,status,priority,issuetype,created,updated,description,issuelinks,comment"

    while True:
        print(f"Fetching Jira issues: startAt={start_at}", flush=True)
        resp = SESSION.get(
            f"{JIRA_API_URL}/search",
            headers=HEADERS,
            params={
                "jql": jql,
                "startAt": start_at,
                "maxResults": max_results,
                "fields": fields,
            },
            timeout=(5, 60),
        )
        resp.raise_for_status()
        data = resp.json()
        issues = data.get("issues", [])
        all_issues.extend(issues)
        total = data.get("total", 0)
        print(
            f"Fetched {len(issues)} Jira issues; accumulated {len(all_issues)}/{total}",
            flush=True,
        )
        if start_at + len(issues) >= total or not issues:
            break
        start_at += len(issues)
    return all_issues


def _issue_to_snapshot(issue):
    f = issue["fields"]
    return {
        "key": issue["key"],
        "fields": {
            "summary": f.get("summary", ""),
            "status": f.get("status", {}),
            "priority": f.get("priority", {}),
            "issuetype": f.get("issuetype", {}),
            "created": f.get("created", ""),
            "updated": f.get("updated", ""),
            "description": f.get("description", "") or "",
            "issuelinks": f.get("issuelinks", []),
            "comment": f.get("comment", {}),
        },
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


def _is_open_fault(snapshot):
    fields = snapshot.get("fields", {})
    if fields.get("issuetype", {}).get("name", "") != "故障":
        return False
    status = fields.get("status", {}).get("name", "")
    return status not in OPEN_STATUSES_EXCLUDED


def _latest_cached_updated(cache):
    latest = None
    for snapshot in cache.get("issues", {}).values():
        parsed = _parse_jira_datetime(snapshot.get("fields", {}).get("updated", ""))
        if parsed and (latest is None or parsed > latest):
            latest = parsed
    return latest


def _sync_cache(mode):
    cache = _load_cache()
    now = datetime.now(timezone.utc)

    if mode == "rebuild":
        cache["last_mode"] = "rebuild"
        cache["last_changed_count"] = 0
        return cache

    validate_config()
    full_sync = mode == "full" or not cache.get("issues")
    if full_sync:
        jql = OPEN_JQL
    else:
        since = _latest_cached_updated(cache) or now
        since = since - timedelta(minutes=INCREMENTAL_LOOKBACK_MINUTES)
        jql = f'{BASE_JQL} AND updated >= "{_format_jql_datetime(since)}" ORDER BY updated DESC'

    issues = _search_issues(jql)
    snapshots = [_issue_to_snapshot(issue) for issue in issues]
    changed_keys = []
    for snapshot in snapshots:
        key = snapshot["key"]
        if _is_open_fault(snapshot):
            cache["issues"][key] = snapshot
        else:
            cache["issues"].pop(key, None)
        changed_keys.append(key)

    cache["last_incremental_sync"] = now.isoformat()
    if full_sync:
        cache["last_full_sync"] = now.isoformat()
        open_keys = {snapshot["key"] for snapshot in snapshots if _is_open_fault(snapshot)}
        for key in list(cache["issues"]):
            if key not in open_keys:
                cache["issues"].pop(key, None)
    cache["last_mode"] = "full" if full_sync else mode
    cache["last_changed_count"] = len(changed_keys)
    _save_cache(cache)
    return cache


def _build_dashboard_data(cache):
    all_issues = [
        issue
        for issue in cache.get("issues", {}).values()
        if _is_open_fault(issue)
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

        (
            at_comments,
            has_replied,
            latest_mention_at,
            latest_my_reply_at,
        ) = _get_at_comments(f.get("comment", {}))
        diagnosed = (
            has_replied
            and latest_my_reply_at
            and (not latest_mention_at or latest_my_reply_at >= latest_mention_at)
        )
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
                "diagnosed": diagnosed,
                "latest_mention_at": latest_mention_at,
                "latest_my_reply_at": latest_my_reply_at,
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
        "sync": {
            "mode": cache.get("last_mode"),
            "last_full_sync": cache.get("last_full_sync"),
            "last_incremental_sync": cache.get("last_incremental_sync"),
            "last_changed_count": cache.get("last_changed_count", 0),
            "cache_path": str(CACHE_PATH),
        },
    }


def fetch_all_issues(mode="incremental"):
    """Refresh Jira issue cache and return dashboard data."""
    if mode not in {"incremental", "full", "rebuild"}:
        raise ValueError("mode must be one of: incremental, full, rebuild")
    cache = _sync_cache(mode)
    return _build_dashboard_data(cache)
