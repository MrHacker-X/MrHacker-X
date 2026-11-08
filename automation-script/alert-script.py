
#!/usr/bin/env python3
"""GitHub public-profile watcher with Telegram notifications."""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    import fcntl
except ImportError:
    fcntl = None


IST = ZoneInfo("Asia/Kolkata")

USERNAME = (
    os.getenv("GITHUB_USERNAME") or "MrHacker-X"
).strip()

TG_BOT_TOKEN = (os.getenv("TG_BOT_TOKEN") or "").strip()
TG_CHAT_ID = (os.getenv("TG_CHAT_ID") or "").strip()
GITHUB_TOKEN = (os.getenv("GITHUB_TOKEN") or "").strip()

ALWAYS_NOTIFY = (
    (os.getenv("ALWAYS_NOTIFY") or "1").strip().lower()
    in ("1", "true", "yes", "on")
)

STATE_FILE = Path(
    os.getenv("STATE_FILE")
    or (Path(__file__).resolve().parent / "github_state.json")
).expanduser().resolve()

API_VERSION = "2022-11-28"
MAX_PENDING = 100


def now():
    d = datetime.now(IST)
    return (
        d.strftime("%d %b %Y • %I:%M %p"),
        d.isoformat(timespec="seconds"),
    )


def load_state():
    try:
        with STATE_FILE.open(encoding="utf-8") as f:
            value = json.load(f)

        if not isinstance(value, dict):
            return {}

        tracked = value.get("tracked_username", USERNAME)

        if not isinstance(tracked, str):
            return {}

        if tracked.lower() != USERNAME.lower():
            return {}

        return value

    except (OSError, ValueError):
        return {}


def save_state(state):
    STATE_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temp = STATE_FILE.with_name(
        STATE_FILE.name + ".tmp"
    )

    with temp.open("w", encoding="utf-8") as f:
        json.dump(
            state,
            f,
            ensure_ascii=False,
            indent=2,
        )
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())

    os.replace(temp, STATE_FILE)


def api_request(path):
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "GitHub-Profile-Watch",
        "X-GitHub-Api-Version": API_VERSION,
    }

    if GITHUB_TOKEN:
        headers["Authorization"] = (
            f"Bearer {GITHUB_TOKEN}"
        )

    request = urllib.request.Request(
        "https://api.github.com" + path,
        headers=headers,
    )

    for attempt in range(3):
        try:
            with urllib.request.urlopen(
                request,
                timeout=20,
            ) as response:
                code = response.status
                raw = response.read()
                response_headers = dict(response.headers)

        except urllib.error.HTTPError as error:
            code = error.code
            raw = error.read()
            response_headers = dict(error.headers)

        except (
            urllib.error.URLError,
            TimeoutError,
            OSError,
        ) as error:
            if attempt < 2:
                time.sleep(2 ** attempt)
                continue

            return 0, None, str(error)

        try:
            data = json.loads(
                raw.decode("utf-8")
            )

        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
        ):
            if code == 200 and attempt < 2:
                time.sleep(2 ** attempt)
                continue

            return code, None, "Invalid JSON response"

        if code in (500, 502, 503, 504) and attempt < 2:
            time.sleep(2 ** attempt)
            continue

        detail = (
            data.get("message", "")
            if isinstance(data, dict)
            else ""
        )

        if code in (403, 429):
            retry_after = response_headers.get(
                "Retry-After", ""
            )

            if retry_after:
                detail = (
                    f"{detail} "
                    f"(Retry-After: {retry_after}s)"
                )

        return code, data, str(detail)[:180]

    return 0, None, "Request retries exhausted"


def valid_count(value):
    return type(value) is int and value >= 0


def fetch_profile():
    quoted = urllib.parse.quote(
        USERNAME,
        safe="",
    )

    status, data, detail = api_request(
        f"/users/{quoted}"
    )

    if status != 200:
        return status, None, detail

    if not isinstance(data, dict):
        return -1, None, "Invalid user profile response"

    login = data.get("login")

    if (
        not isinstance(login, str)
        or login.lower() != USERNAME.lower()
    ):
        return -1, None, "Unexpected user profile response"

    for key in ("public_repos", "followers"):
        if not valid_count(data.get(key)):
            return (
                -1,
                None,
                "Missing/invalid profile statistics",
            )

    return 200, data, ""


def fetch_stars(expected_repos):
    """
    Count stars received across every public repository
    owned by the monitored GitHub account.
    """

    quoted = urllib.parse.quote(
        USERNAME,
        safe="",
    )

    total = 0
    seen_ids = set()
    page = 1

    while True:
        path = (
            f"/users/{quoted}/repos"
            f"?type=owner&per_page=100&page={page}"
        )

        status, data, detail = api_request(path)

        if status != 200:
            return (
                status,
                None,
                f"Repository listing failed "
                f"on page {page}: {detail}",
            )

        if not isinstance(data, list) or len(data) > 100:
            return (
                -1,
                None,
                "Invalid repository listing response",
            )

        for repo in data:
            if not isinstance(repo, dict):
                return -1, None, "Invalid repository data"

            stars = repo.get("stargazers_count")
            repo_id = repo.get("id")
            owner = repo.get("owner")

            if (
                not valid_count(stars)
                or not isinstance(repo_id, int)
            ):
                return -1, None, "Invalid repository statistics"

            if not isinstance(owner, dict):
                return -1, None, "Missing repository owner"

            owner_login = owner.get("login", "")

            if str(owner_login).lower() != USERNAME.lower():
                return -1, None, "Unexpected repository owner"

            if repo_id in seen_ids:
                return (
                    -1,
                    None,
                    "Duplicate repository across pages",
                )

            seen_ids.add(repo_id)
            total += stars

        if len(data) < 100:
            break

        page += 1

        if page > 1000:
            return (
                -1,
                None,
                "Repository pagination exceeded safety limit",
            )

    if len(seen_ids) != expected_repos:
        return (
            -1,
            None,
            "Repository count mismatch: "
            f"profile={expected_repos}, "
            f"listing={len(seen_ids)}",
        )

    return 200, total, ""


def delta(current, old):
    if not valid_count(old):
        return ""

    difference = current - old

    return (
        f"  [{difference:+d}]"
        if difference
        else ""
    )


def last_known(state):
    if not state.get("last_successful_check"):
        return "No earlier successful check saved."

    return (
        "Last confirmed online:\n"
        f"📦 Repositories: {state.get('repositories', 'N/A')}\n"
        f"👥 Followers: {state.get('followers', 'N/A')}\n"
        f"⭐ Total Stars: {state.get('stars', 'N/A')}\n"
        f"🕒 {state['last_successful_check']}"
    )


def make_message(
    kind,
    display,
    state,
    stats=None,
    detail="",
):
    title = f"▶ GitHub Profile Watch: {USERNAME}"

    if kind == "online":
        restored = (
            state.get("confirmed_status") == "unavailable"
        )

        prior = (
            state.get("last_successful_check") is not None
        )

        changed = (
            not prior
            or any(
                state.get(k) != v
                for k, v in stats.items()
            )
        )

        header = (
            "🟢 GitHub Profile Back Online"
            if restored
            else "🟢 GitHub Profile Online"
        )

        context = (
            "📊 Statistics updated."
            if changed
            else "📊 Stats unchanged since "
                 "the last successful check."
        )

        return (
            f"{header}\n\n"
            f"{title}\n"
            f"{context}\n\n"
            f"📦 Repositories: {stats['repositories']}"
            f"{delta(stats['repositories'], state.get('repositories'))}\n"
            f"👥 Followers: {stats['followers']}"
            f"{delta(stats['followers'], state.get('followers'))}\n"
            f"⭐ Total Stars: {stats['stars']}"
            f"{delta(stats['stars'], state.get('stars'))}\n\n"
            f"🕒 Checked: {display}"
        )

    if kind == "unavailable":
        return (
            "🔴 GitHub Profile Unavailable\n\n"
            f"{title}\n"
            "The public API returned HTTP 404 "
            "on two consecutive checks.\n\n"
            f"{last_known(state)}\n\n"
            f"🕒 Checked: {display}"
        )

    if kind == "unconfirmed_404":
        return (
            "🟠 GitHub Profile 404 — Unconfirmed\n\n"
            f"{title}\n"
            "First HTTP 404. Waiting for another "
            "check before marking unavailable.\n\n"
            f"{last_known(state)}\n\n"
            f"🕒 Checked: {display}"
        )

    return (
        "🟡 GitHub Check Failed\n\n"
        f"{title}\n"
        f"⚠️ {detail or kind}\n\n"
        "No confirmed profile status change "
        "has been assumed.\n\n"
        f"🕒 Checked: {display}"
    )


def send_telegram(message):
    url = (
        "https://api.telegram.org/"
        f"bot{TG_BOT_TOKEN}/sendMessage"
    )

    body = urllib.parse.urlencode({
        "chat_id": TG_CHAT_ID,
        "text": message,
        "disable_web_page_preview": "true",
    }).encode("utf-8")

    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type":
                "application/x-www-form-urlencoded",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=20,
        ) as response:
            data = json.loads(
                response.read().decode("utf-8")
            )

        if (
            not isinstance(data, dict)
            or data.get("ok") is not True
        ):
            raise RuntimeError(
                "Telegram returned an unsuccessful response"
            )

    except (
        urllib.error.HTTPError,
        urllib.error.URLError,
        TimeoutError,
        OSError,
        ValueError,
        RuntimeError,
    ) as error:
        raise RuntimeError(
            f"Telegram delivery failed: {error}"
        ) from error


def flush_queue(state):
    pending = state.get("pending", [])

    while pending:
        try:
            send_telegram(pending[0])

        except RuntimeError as error:
            print(
                str(error),
                file=sys.stderr,
            )
            return False

        pending.pop(0)
        save_state(state)

    return True


def queue_alert(state, message):
    pending = state.setdefault("pending", [])

    if len(pending) >= MAX_PENDING:
        pending.pop(0)

        print(
            "Warning: notification queue full; "
            "oldest message discarded",
            file=sys.stderr,
        )

    pending.append(message)


def check(state):
    display, stamp = now()

    status, profile, detail = fetch_profile()
    stats = None

    if status == 200:
        stars_status, stars, stars_detail = fetch_stars(
            profile["public_repos"]
        )

        if stars_status != 200:
            status = stars_status
            detail = stars_detail

        else:
            stats = {
                "repositories": profile["public_repos"],
                "followers": profile["followers"],
                "stars": stars,
            }

    if status == 200:
        kind = "online"

        changed = (
            not state.get("last_successful_check")
            or state.get("confirmed_status") == "unavailable"
            or any(
                state.get(k) != v
                for k, v in stats.items()
            )
        )

        notify = (
            ALWAYS_NOTIFY
            or state.get("last_observation") != "online"
            or changed
        )

        message = make_message(
            kind,
            display,
            state,
            stats,
        )

        state.update(stats)

        state["last_successful_check"] = stamp
        state["confirmed_status"] = "online"
        state["404_streak"] = 0

    elif status == 404:
        state["404_streak"] = (
            state.get("404_streak", 0) + 1
        )

        kind = (
            "unavailable"
            if state["404_streak"] >= 2
            else "unconfirmed_404"
        )

        notify = (
            ALWAYS_NOTIFY
            or state.get("last_observation") != kind
        )

        message = make_message(
            kind,
            display,
            state,
        )

        if kind == "unavailable":
            state["confirmed_status"] = "unavailable"

    else:
        if status in (403, 429):
            kind = f"api_warning:{status}"

        elif status == 0:
            kind = "network_error"

        elif status == -1:
            kind = "invalid_response"

        else:
            kind = f"http_{status}"

        state["404_streak"] = 0

        notify = (
            ALWAYS_NOTIFY
            or state.get("last_observation") != kind
        )

        description = (
            f"HTTP {status}: {detail}"
            if status > 0
            else detail
        )

        message = make_message(
            kind,
            display,
            state,
            detail=description,
        )

    state["last_observation"] = kind
    state["last_checked"] = stamp
    state["tracked_username"] = USERNAME

    if notify:
        queue_alert(state, message)
        print(message)

    else:
        print(
            f"No alert; unchanged status: "
            f"{kind} ({display})"
        )

    save_state(state)


def main():
    if (
        not USERNAME
        or not TG_BOT_TOKEN
        or not TG_CHAT_ID
    ):
        print(
            "Required: GITHUB_USERNAME (or default), "
            "TG_BOT_TOKEN and TG_CHAT_ID",
            file=sys.stderr,
        )
        return 2

    if fcntl is None:
        print(
            "Single-instance locking requires "
            "fcntl (Linux/macOS).",
            file=sys.stderr,
        )
        return 2

    STATE_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    lock = STATE_FILE.with_name(
        STATE_FILE.name + ".lock"
    )

    with lock.open("a+") as f:
        try:
            fcntl.flock(
                f.fileno(),
                fcntl.LOCK_EX | fcntl.LOCK_NB,
            )

        except BlockingIOError:
            print(
                "Another watcher instance is "
                "already running; skipping."
            )
            return 0

        state = load_state()

        # Retry previously undelivered notifications.
        flush_queue(state)

        # Run current GitHub check regardless of
        # Telegram delivery status.
        check(state)

        return 0 if flush_queue(state) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())

    except Exception as error:
        print(
            f"Watcher failure: {error}",
            file=sys.stderr,
        )
        sys.exit(1)
