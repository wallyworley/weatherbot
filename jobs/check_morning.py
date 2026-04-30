"""Verify last night's morning.sh run completed; email on failure.

Run this ~30 min after morning.sh's scheduled fire (02:00 local). Looks for
today's log at logs/morning/YYYY-MM-DD.log and confirms it ends with the
"Done." marker that morning.sh prints on success.

Email is sent via Gmail SMTP. The app password is fetched from macOS
Keychain — set it once with:

    security add-generic-password -a wallyworley@gmail.com \\
        -s weatherbot-gmail-app-password -w 'YOUR_APP_PASSWORD'

(Generate the app password at https://myaccount.google.com/apppasswords)
"""
from __future__ import annotations

import logging
import smtplib
import subprocess
import sys
from datetime import date
from email.message import EmailMessage
from pathlib import Path

EMAIL_FROM = "wallyworley@gmail.com"
EMAIL_TO = "wallyworley@gmail.com"
KEYCHAIN_SERVICE = "weatherbot-gmail-app-password"
LOG_DIR = Path("/Users/walterworley/dev/weather_bot/logs/morning")
SUCCESS_MARKER = "Done."

log = logging.getLogger(__name__)


def _today_log_path() -> Path:
    return LOG_DIR / f"{date.today().isoformat()}.log"


def _check_log() -> tuple[bool, str]:
    """Return (ok, reason). reason is empty when ok=True."""
    p = _today_log_path()
    if not p.exists():
        return False, f"Expected log file not found: {p}"
    text = p.read_text(errors="replace")
    if not text.strip():
        return False, f"Log file is empty: {p}"
    tail = "\n".join(text.splitlines()[-30:])
    if not any(line.startswith(SUCCESS_MARKER) for line in tail.splitlines()):
        return False, f"Success marker '{SUCCESS_MARKER}' not found in last 30 lines of {p}\n\nTail:\n{tail}"
    return True, ""


def _get_app_password() -> str:
    r = subprocess.run(
        ["security", "find-generic-password", "-a", EMAIL_FROM, "-s", KEYCHAIN_SERVICE, "-w"],
        capture_output=True, text=True, check=True,
    )
    return r.stdout.strip()


def _send_email(subject: str, body: str) -> None:
    pw = _get_app_password()
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO
    msg.set_content(body)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(EMAIL_FROM, pw)
        s.send_message(msg)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    ok, reason = _check_log()
    if ok:
        log.info("morning.sh succeeded: %s", _today_log_path())
        return 0
    log.warning("morning.sh check FAILED: %s", reason)
    try:
        _send_email(
            subject=f"[weather_bot] morning.sh did not complete — {date.today()}",
            body=(
                f"morning.sh did not produce a successful log for {date.today()}.\n\n"
                f"{reason}\n\n"
                f"Check launchd: launchctl list | grep weatherbot-morning\n"
                f"Re-run manually:  /Users/walterworley/Library/Scripts/weatherbot-morning.sh\n"
            ),
        )
        log.info("Notification email sent to %s", EMAIL_TO)
    except Exception as exc:
        log.error("Failed to send email: %s", exc)
        return 2
    return 1


if __name__ == "__main__":
    sys.exit(main())
