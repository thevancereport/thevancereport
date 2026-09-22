"""The daily alert email, from value_screen.json, sent through Buttondown.

Runs after the "Daily value screen" workflow commits a new file. It reads only
what the site already publishes -- value_screen.json today and the previous
committed copy -- so the email can never say something the site does not.

What it sends: the date, how many companies were ranked, the top ten, and
which names entered or left the top ten since the previous file. Facts only;
no forecasts, no adjectives about prices.

When it does not send (and exits 0, so a quiet day is not a red X):
  * the file is thin (fewer than MIN_RANKED companies),
  * the session date is a weekend (a manual rerun, not a trading day),
  * the file is stale (the screen job ran but did not commit a new file),
  * an email for this session already exists in Buttondown.

ALERTS_MODE=send  -> Buttondown sends it to every confirmed subscriber.
anything else     -> it is saved as a draft in Buttondown for review.

Buttondown adds the unsubscribe link and the postal-address footer itself.
Standard library only.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

SITE = "https://thevancereport.com"
API = "https://api.buttondown.com/v1/emails"
MIN_RANKED = 200
MAX_AGE_HOURS = 20
TOP_N = 10
KEEP_CAPS = {"PLC", "LLC", "LP", "NV", "SA", "AG", "SE", "II", "III", "USA", "US", "REIT", "ETF"}


# ---------------------------------------------------------------- the facts


def load_json(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def previous_screen(path: str = "value_screen.json") -> dict | None:
    """The copy of the file from the commit before the latest one that touched it."""
    try:
        shas = subprocess.run(
            ["git", "log", "-n", "2", "--format=%H", "--", path],
            capture_output=True, text=True, check=True,
        ).stdout.split()
        if len(shas) < 2:
            return None
        text = subprocess.run(
            ["git", "show", f"{shas[1]}:{path}"],
            capture_output=True, text=True, check=True,
        ).stdout
        return json.loads(text)
    except (subprocess.CalledProcessError, json.JSONDecodeError, FileNotFoundError):
        return None


def session_date(screen: dict) -> dt.date:
    return dt.date.fromisoformat(screen["_meta"]["as_of"])


def pretty_name(name: str) -> str:
    """SEC names arrive in capitals ("YELP INC"). Soften those; leave mixed case alone."""
    name = (name or "").strip()
    if name and name.upper() == name:
        return " ".join(
            w if any(c.isdigit() for c in w) or w.strip(".,") in KEEP_CAPS else w.capitalize()
            for w in name.split()
        )
    return name


def top(screen: dict, n: int = TOP_N) -> list[dict]:
    rows = sorted(screen["ranked"], key=lambda r: r["rank"])
    return rows[:n]


def movers(today: dict, before: dict | None, n: int = TOP_N) -> tuple[list[dict], list[dict]]:
    """(entered, left) the top n, comparing today's file with the previous one."""
    if not before:
        return [], []
    now = {r["symbol"]: r for r in top(today, n)}
    then = {r["symbol"]: r for r in top(before, n)}
    entered = [now[s] for s in now if s not in then]
    left_syms = [s for s in then if s not in now]
    today_rank = {r["symbol"]: r["rank"] for r in today["ranked"]}
    left = [dict(then[s], today_rank=today_rank.get(s)) for s in left_syms]
    return entered, left


def check(screen: dict, now: dt.datetime) -> str | None:
    """Why this file must not be emailed, or None if it may be."""
    ranked = screen.get("ranked") or []
    if len(ranked) < MIN_RANKED:
        return f"thin file: {len(ranked)} ranked (< {MIN_RANKED})"
    day = session_date(screen)
    if day.weekday() >= 5:
        return f"session date {day} is a weekend"
    generated = dt.datetime.fromisoformat(screen["_meta"]["generated_at"])
    if generated.tzinfo is None:
        generated = generated.replace(tzinfo=dt.timezone.utc)
    age = (now - generated).total_seconds() / 3600
    if age > MAX_AGE_HOURS:
        return f"stale file: generated {age:.0f}h ago"
    return None


# ---------------------------------------------------------------- the words


def long_date(d: dt.date) -> str:
    return f"{d:%A}, {d.day} {d:%B %Y}"


def short_date(d: dt.date) -> str:
    return f"{d:%a} {d.day} {d:%b}"


def link(sym: str) -> str:
    return f"[{sym}]({SITE}/stock.html?symbol={urllib.parse.quote(sym)})"


def subject(screen: dict) -> str:
    d = session_date(screen)
    return f"The screen, {short_date(d)}: {len(screen['ranked']):,} companies ranked"


def body(screen: dict, before: dict | None) -> str:
    d = session_date(screen)
    n = len(screen["ranked"])
    lines = [
        f"**{n:,} US companies ranked** on the close of {long_date(d)}.",
        "",
        "## Top ten today",
        "",
        "| # | Company | Sector | Score |",
        "|---:|---|---|---:|",
    ]
    for r in top(screen):
        lines.append(
            f"| {r['rank']} | {link(r['symbol'])} {pretty_name(r['name'])} "
            f"| {r['sector']} | {r['score']:.1f} |"
        )

    entered, left = movers(screen, before)
    if entered or left:
        lines += ["", "## Changes in the top ten", ""]
        for r in entered:
            lines.append(f"- **In:** {link(r['symbol'])} {pretty_name(r['name'])}, now #{r['rank']}")
        for r in left:
            where = f"now #{r['today_rank']}" if r.get("today_rank") else "no longer ranked"
            lines.append(f"- **Out:** {link(r['symbol'])} {pretty_name(r['name'])}, {where}")

    lines += [
        "",
        f"[See the full ranking]({SITE}/) · [Look up any company]({SITE}/stock.html)",
        "",
        "Each score blends cheapness (40%), quality (30%), safety (20%) and "
        "confirmation (10%), every one a percentile against the company's own "
        "sector. A high rank is a reason to look, not a reason to buy.",
        "",
        f"New here? [How the Vance Value Screener works]({SITE}/vance-value-screener.html)",
        "",
        "*Educational research only — not investment advice. The Vance Report "
        "is not a registered investment adviser.*",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------- Buttondown


def api(method: str, url: str, key: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Token {key}",
        "Content-Type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=30) as res:
        return json.loads(res.read() or b"{}")


def already_created(key: str, session: str) -> bool:
    q = urllib.parse.urlencode({"ordering": "-creation_date", "source": "api", "excluded_fields": "body"})
    listing = api("GET", f"{API}?{q}", key)
    for e in listing.get("results", [])[:50]:
        if (e.get("metadata") or {}).get("session") == session:
            return True
    return False


def main() -> int:
    path = os.environ.get("SCREEN_FILE", "value_screen.json")
    key = os.environ.get("BUTTONDOWN_API_KEY", "")
    mode = os.environ.get("ALERTS_MODE", "draft").strip().lower()
    dry = os.environ.get("DRY_RUN", "") == "1"

    screen = load_json(path)
    reason = check(screen, dt.datetime.now(dt.timezone.utc))
    if reason:
        print(f"No email today: {reason}.")
        return 0

    before = previous_screen(path)
    subj, text = subject(screen), body(screen, before)
    session = screen["_meta"]["as_of"]

    if dry:
        print(subj, "\n", text, sep="\n")
        return 0
    if not key:
        print("BUTTONDOWN_API_KEY is not set; nothing sent.", file=sys.stderr)
        return 1

    if already_created(key, session):
        print(f"An email for {session} already exists in Buttondown; not creating another.")
        return 0

    status = "about_to_send" if mode == "send" else "draft"
    created = api("POST", API, key, {
        "subject": subj,
        "body": "<!-- buttondown-editor-mode: plaintext -->" + text,
        "status": status,
        "metadata": {"session": session, "source": "daily_alert.py"},
    })
    print(f"Created {status} email {created.get('id')}: {subj}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except urllib.error.HTTPError as err:
        print(f"Buttondown returned {err.code}: {err.read()[:500]!r}", file=sys.stderr)
        sys.exit(1)
