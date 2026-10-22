#!/usr/bin/env python3

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


def clean_env(name, default=None):
    value = (os.getenv(name) or "").strip()
    return value or default


USERNAME = clean_env("GITHUB_USERNAME", "MrHacker-X")
TG_BOT_TOKEN = clean_env("TG_BOT_TOKEN")
TG_CHAT_ID = clean_env("TG_CHAT_ID")

STATE_FILE = Path(
    os.getenv("STATE_FILE", "github_state.json")
)

IST = ZoneInfo("Asia/Kolkata")

SNAPSHOT_KEYS = (
    "username",
    "repositories",
    "followers",
    "following",
    "last_successful_check",
)


def current_time():
    now = datetime.now(IST)

    display = now.strftime("%d %b %Y • %I:%M %p")
    iso = now.isoformat(timespec="seconds")

    return display, iso


def load_state():
    if not STATE_FILE.exists():
        return {}

    try:
        with STATE_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, dict):
            return data

    except (json.JSONDecodeError, OSError):
        pass

    return {}


def save_state(data):
    STATE_FILE.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    temp_file = STATE_FILE.with_suffix(".tmp")

    with temp_file.open("w", encoding="utf-8") as f:
        json.dump(
            data,
            f,
            indent=2,
            ensure_ascii=False
        )
        f.write("\n")

    temp_file.replace(STATE_FILE)


def format_delta(current, previous):
    if previous is None:
        return ""

    try:
        difference = int(current) - int(previous)
    except (TypeError, ValueError):
        return ""

    if difference > 0:
        return f"  [+{difference}]"

    if difference < 0:
        return f"  [{difference}]"

    return ""


def as_count(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def validate_config():
    missing = [
        name
        for name, value in (
            ("TG_BOT_TOKEN", TG_BOT_TOKEN),
            ("TG_CHAT_ID", TG_CHAT_ID),
        )
        if not value
    ]

    if not missing:
        return

    print(
        "Missing required environment: " + ", ".join(missing),
        file=sys.stderr
    )
    raise SystemExit(1)


def snapshot_from(old_state):
    return {
        key: old_state[key]
        for key in SNAPSHOT_KEYS
        if key in old_state
    }


def observe(old_state, status, checked_iso, snapshot=None):
    # Failed checks keep the last successful snapshot.
    state = snapshot_from(old_state)

    if snapshot:
        state.update(snapshot)

    state["last_status"] = status
    state["last_checked"] = checked_iso
    return state


def counts_changed(old_state, repos, followers, following):
    current = {
        "repositories": repos,
        "followers": followers,
        "following": following,
    }

    return any(
        old_state.get(key) != value
        for key, value in current.items()
    )


def needs_alert(old_state, status, counts=None):
    if old_state.get("last_status") != status:
        return True

    if status != "online" or counts is None:
        return False

    return counts_changed(old_state, *counts)


def quiet_note(status):
    if status == "online":
        return (
            "No alert. Profile is online and "
            "the stats are unchanged."
        )

    if status == "unavailable":
        return "No alert. Profile is still unavailable."

    if status.startswith("api_warning"):
        return "No alert. The GitHub API warning is unchanged."

    if status == "server_error":
        return "No alert. The GitHub server error is unchanged."

    if status == "network_error":
        return "No alert. The network error is unchanged."

    if status == "invalid_response":
        return "No alert. The invalid response is unchanged."

    return "No alert. The check result is unchanged."


def report(message, alert, note):
    if not alert:
        print(note)
        return

    send_telegram(message)
    print(message)


def deliver(
    old_state,
    kind,
    message,
    checked_iso,
    snapshot=None,
    counts=None
):
    alert = needs_alert(old_state, kind, counts)
    report(message, alert, quiet_note(kind))
    save_state(
        observe(old_state, kind, checked_iso, snapshot)
    )
    return 0


def format_saved_time(value):
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return str(value)

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=IST)
    else:
        parsed = parsed.astimezone(IST)

    return parsed.strftime("%d %b %Y • %I:%M %p")


def last_known_text(old_state):
    stamp = old_state.get("last_successful_check")

    if not stamp:
        return "No earlier successful check is saved."

    return (
        "Last confirmed online:\n"
        f"📦 Repositories: {old_state.get('repositories', 0)}\n"
        f"👥 Followers: {old_state.get('followers', 0)}\n"
        f"🔗 Following: {old_state.get('following', 0)}\n"
        f"🕒 {format_saved_time(stamp)}"
    )


def github_detail(data):
    if not isinstance(data, dict):
        return ""

    message = data.get("message")

    if message is None:
        return ""

    text = " ".join(str(message).split())

    if len(text) > 240:
        text = text[:237] + "..."

    return text


def send_telegram(message):
    if not TG_BOT_TOKEN:
        raise RuntimeError(
            "TG_BOT_TOKEN is missing."
        )

    if not TG_CHAT_ID:
        raise RuntimeError(
            "TG_CHAT_ID is missing."
        )

    url = (
        f"https://api.telegram.org/"
        f"bot{TG_BOT_TOKEN}/sendMessage"
    )

    payload = urllib.parse.urlencode({
        "chat_id": TG_CHAT_ID,
        "text": message,
        "disable_web_page_preview": "true"
    }).encode("utf-8")

    request = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={
            "Content-Type":
                "application/x-www-form-urlencoded"
        }
    )

    with urllib.request.urlopen(
        request,
        timeout=20
    ) as response:
        result = json.loads(
            response.read().decode("utf-8")
        )

    if not result.get("ok"):
        raise RuntimeError(
            f"Telegram API error: {result}"
        )


def github_request():
    request = urllib.request.Request(
        f"https://api.github.com/users/{USERNAME}",
        headers={
            "Accept":
                "application/vnd.github+json",
            "User-Agent":
                "GitHub-Profile-Watch",
            "X-GitHub-Api-Version":
                "2022-11-28"
        }
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=20
        ) as response:

            body = response.read().decode(
                "utf-8"
            )

            return (
                response.status,
                json.loads(body)
            )

    except urllib.error.HTTPError as error:
        try:
            body = error.read().decode(
                "utf-8"
            )
            data = json.loads(body)
        except Exception:
            data = {}

        return error.code, data


def success_message(data, old_state, checked, restored=False):
    repos = as_count(data.get("public_repos"))
    followers = as_count(data.get("followers"))
    following = as_count(data.get("following"))

    repo_delta = format_delta(
        repos,
        old_state.get("repositories")
    )

    follower_delta = format_delta(
        followers,
        old_state.get("followers")
    )

    following_delta = format_delta(
        following,
        old_state.get("following")
    )

    title = (
        "🟢 GitHub Profile Back Online"
        if restored
        else "🟢 GitHub Profile Online"
    )

    return (
        f"{title}\n\n"
        f"{USERNAME} is publicly accessible "
        "and responding normally.\n\n"
        f"📦 Repositories: {repos}{repo_delta}\n"
        f"👥 Followers: {followers}{follower_delta}\n"
        f"🔗 Following: {following}{following_delta}\n\n"
        f"🕒 Checked: {checked}"
    )


def unavailable_message(status, checked, old_state):
    return (
        "🔴 GitHub Profile Unavailable\n\n"
        f"{USERNAME} is not publicly accessible "
        "through the GitHub API.\n\n"
        f"⚠️ HTTP Status: {status} Not Found\n\n"
        f"{last_known_text(old_state)}\n\n"
        "This may indicate that the profile has "
        "been restricted, hidden, renamed, "
        "or removed.\n\n"
        f"🕒 Checked: {checked}"
    )


def api_warning_message(status, checked, detail):
    label = {
        403: "Forbidden",
        429: "Too Many Requests"
    }.get(status, "API Error")

    lines = [
        "🟠 GitHub API Warning",
        "",
        f"Unable to verify {USERNAME} right now.",
        "",
        f"⚠️ HTTP Status: {status} {label}",
    ]

    if detail:
        lines.append(f"💬 {detail}")

    lines.extend([
        "",
        "The account is not being marked as "
        "unavailable because this may be an "
        "API or rate-limit issue.",
        "",
        f"🕒 Checked: {checked}",
    ])

    return "\n".join(lines)


def server_error_message(status, checked):
    return (
        "🟡 GitHub Check Failed\n\n"
        f"Unable to complete the public profile "
        f"check for {USERNAME}.\n\n"
        f"⚠️ HTTP Status: {status}\n\n"
        "GitHub API returned a server-side error. "
        "No account status change has been assumed.\n\n"
        f"🕒 Checked: {checked}"
    )


def network_error_message(reason, checked):
    reason = str(reason)

    if len(reason) > 120:
        reason = reason[:117] + "..."

    return (
        "🟡 GitHub Check Failed\n\n"
        f"Unable to complete the public profile "
        f"check for {USERNAME}.\n\n"
        f"⚠️ Reason: {reason}\n\n"
        "No account status change has been assumed.\n\n"
        f"🕒 Checked: {checked}"
    )


def other_status_message(status, checked):
    return (
        "🟡 GitHub Check Failed\n\n"
        f"Unable to verify {USERNAME}.\n\n"
        f"⚠️ HTTP Status: {status}\n\n"
        "No account status change has been "
        "assumed.\n\n"
        f"🕒 Checked: {checked}"
    )


def profile_snapshot(data, checked_iso):
    repos = as_count(data.get("public_repos"))
    followers = as_count(data.get("followers"))
    following = as_count(data.get("following"))

    snapshot = {
        "username": data.get("login"),
        "repositories": repos,
        "followers": followers,
        "following": following,
        "last_successful_check": checked_iso,
    }

    return snapshot, (repos, followers, following)


def main():
    validate_config()

    checked, checked_iso = current_time()
    old_state = load_state()

    try:
        status, data = github_request()

    except (
        urllib.error.URLError,
        TimeoutError,
        OSError
    ) as error:

        return deliver(
            old_state,
            "network_error",
            network_error_message(error, checked),
            checked_iso
        )

    if status == 200:
        if not isinstance(data, dict):
            return deliver(
                old_state,
                "invalid_response",
                network_error_message(
                    "Invalid GitHub API response",
                    checked
                ),
                checked_iso
            )

        api_username = data.get("login")

        if (
            not isinstance(api_username, str)
            or api_username.lower() != USERNAME.lower()
        ):
            return deliver(
                old_state,
                "invalid_response",
                network_error_message(
                    "Unexpected GitHub profile response",
                    checked
                ),
                checked_iso
            )

        snapshot, counts = profile_snapshot(
            data,
            checked_iso
        )
        restored = old_state.get("last_status") == "unavailable"

        return deliver(
            old_state,
            "online",
            success_message(
                data,
                old_state,
                checked,
                restored=restored
            ),
            checked_iso,
            snapshot=snapshot,
            counts=counts
        )

    if status == 404:
        return deliver(
            old_state,
            "unavailable",
            unavailable_message(
                status,
                checked,
                old_state
            ),
            checked_iso
        )

    if status in (403, 429):
        return deliver(
            old_state,
            f"api_warning:{status}",
            api_warning_message(
                status,
                checked,
                github_detail(data)
            ),
            checked_iso
        )

    if 500 <= status <= 599:
        return deliver(
            old_state,
            "server_error",
            server_error_message(status, checked),
            checked_iso
        )

    return deliver(
        old_state,
        f"http_{status}",
        other_status_message(status, checked),
        checked_iso
    )


if __name__ == "__main__":
    try:
        sys.exit(main())

    except Exception as error:
        checked, _ = current_time()

        fallback = network_error_message(
            f"Watcher error: {error}",
            checked
        )

        try:
            send_telegram(fallback)
        except Exception as telegram_error:
            print(
                f"Telegram notification failed: "
                f"{telegram_error}",
                file=sys.stderr
            )

        print(fallback, file=sys.stderr)
        sys.exit(1)
