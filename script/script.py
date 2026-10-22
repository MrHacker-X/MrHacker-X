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


USERNAME = os.getenv("GITHUB_USERNAME", "MrHacker-X")
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
TG_CHAT_ID = os.getenv("TG_CHAT_ID")

STATE_FILE = Path(
    os.getenv("STATE_FILE", "github_state.json")
)

GITHUB_API = f"https://api.github.com/users/{USERNAME}"

IST = ZoneInfo("Asia/Kolkata")


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
        GITHUB_API,
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


def success_message(data, old_state, checked):
    repos = data.get("public_repos", 0)
    followers = data.get("followers", 0)
    following = data.get("following", 0)

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

    return (
        "🟢 GitHub Profile Online\n\n"
        f"{USERNAME} is publicly accessible "
        "and responding normally.\n\n"
        f"📦 Repositories: {repos}{repo_delta}\n"
        f"👥 Followers: {followers}{follower_delta}\n"
        f"🔗 Following: {following}{following_delta}\n\n"
        f"🕒 Checked: {checked}"
    )


def unavailable_message(status, checked):
    return (
        "🔴 GitHub Profile Unavailable\n\n"
        f"{USERNAME} is not publicly accessible "
        "through the GitHub API.\n\n"
        f"⚠️ HTTP Status: {status} Not Found\n\n"
        "This may indicate that the profile has "
        "been restricted, hidden, renamed, "
        "or removed.\n\n"
        f"🕒 Checked: {checked}"
    )


def api_warning_message(status, checked):
    label = {
        403: "Forbidden",
        429: "Too Many Requests"
    }.get(status, "API Error")

    return (
        "🟠 GitHub API Warning\n\n"
        f"Unable to verify {USERNAME} right now.\n\n"
        f"⚠️ HTTP Status: {status} {label}\n\n"
        "The account is not being marked as "
        "unavailable because this may be an "
        "API or rate-limit issue.\n\n"
        f"🕒 Checked: {checked}"
    )


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


def main():
    checked, checked_iso = current_time()
    old_state = load_state()

    try:
        status, data = github_request()

    except (
        urllib.error.URLError,
        TimeoutError,
        OSError
    ) as error:

        message = network_error_message(
            error,
            checked
        )

        send_telegram(message)

        print(message)
        return 0

    if status == 200:
        if not isinstance(data, dict):
            message = network_error_message(
                "Invalid GitHub API response",
                checked
            )

            send_telegram(message)
            print(message)
            return 0

        api_username = data.get("login")

        if (
            not api_username
            or api_username.lower()
            != USERNAME.lower()
        ):
            message = network_error_message(
                "Unexpected GitHub profile response",
                checked
            )

            send_telegram(message)
            print(message)
            return 0

        message = success_message(
            data,
            old_state,
            checked
        )

        send_telegram(message)

        new_state = {
            "username": api_username,
            "repositories":
                data.get("public_repos", 0),
            "followers":
                data.get("followers", 0),
            "following":
                data.get("following", 0),
            "last_successful_check":
                checked_iso
        }

        save_state(new_state)

        print(message)
        return 0

    if status == 404:
        message = unavailable_message(
            status,
            checked
        )

    elif status in (403, 429):
        message = api_warning_message(
            status,
            checked
        )

    elif 500 <= status <= 599:
        message = server_error_message(
            status,
            checked
        )

    else:
        message = (
            "🟡 GitHub Check Failed\n\n"
            f"Unable to verify {USERNAME}.\n\n"
            f"⚠️ HTTP Status: {status}\n\n"
            "No account status change has been "
            "assumed.\n\n"
            f"🕒 Checked: {checked}"
        )

    send_telegram(message)

    print(message)

    # Important:
    # Failed checks DO NOT overwrite
    # the last successful JSON state.

    return 0


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
