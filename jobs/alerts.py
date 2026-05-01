"""Push alerts when a health-check row goes RED.

Two channels:
- macOS Notification Center (always on when running on Darwin via osascript)
- iMessage (opt-in: set ALERT_PHONE='+15551234567' in .env)

Anti-spam strategy: each RED row is alerted on exactly once. The
`alerted_at` column on health_check tracks which rows have already been
sent. A RED that resolves and later goes RED again is a NEW row, so it
will alert again — that's intentional (you want to know when something
re-breaks).

Called from jobs.health_check.run() right after upsert.
"""
from __future__ import annotations

import json
import logging
import os
import platform
import subprocess
from datetime import datetime, timezone

from weather_bot.data import persistence

log = logging.getLogger(__name__)

ALERT_PHONE = os.getenv("ALERT_PHONE", "").strip()
ALERTS_ENABLED = os.getenv("ALERTS_ENABLED", "true").lower() == "true"


def _send_macos_notification(title: str, message: str, subtitle: str = "") -> bool:
    """Fire a native macOS notification via osascript.

    No-op (returns False) on non-Darwin systems.
    """
    if platform.system() != "Darwin":
        return False
    # Escape double quotes — osascript is picky about quoting.
    def esc(s): return s.replace('"', '\\"').replace("\\", "\\\\")
    script = (
        f'display notification "{esc(message)}" '
        f'with title "{esc(title)}" '
        f'subtitle "{esc(subtitle)}" '
        f'sound name "Sosumi"'
    )
    try:
        subprocess.run(["osascript", "-e", script], check=True, timeout=10,
                       capture_output=True)
        return True
    except Exception as exc:
        log.warning("macOS notification failed: %s", exc)
        return False


def _send_imessage(phone: str, message: str) -> bool:
    """Send an iMessage to a phone number via Messages.app + osascript.

    Requires Messages.app to be configured with iMessage on this Mac and the
    target phone to be reachable via iMessage. SMS fallback is not supported
    by this script.

    No-op (returns False) when ALERT_PHONE is empty.
    """
    if not phone or platform.system() != "Darwin":
        return False
    def esc(s): return s.replace('"', '\\"').replace("\\", "\\\\")
    script = f'''
    tell application "Messages"
        set targetService to 1st service whose service type = iMessage
        set targetBuddy to buddy "{esc(phone)}" of targetService
        send "{esc(message)}" to targetBuddy
    end tell
    '''
    try:
        subprocess.run(["osascript", "-e", script], check=True, timeout=10,
                       capture_output=True)
        return True
    except subprocess.CalledProcessError as exc:
        # Most common failure: Messages.app not authorized to send programmatically.
        # On first run, macOS will prompt — accept the dialog and the next call
        # will succeed. Log full error so the user can debug.
        log.warning("iMessage send failed (rc=%s): stderr=%s",
                    exc.returncode, exc.stderr.decode("utf-8", "replace") if exc.stderr else "")
        return False
    except Exception as exc:
        log.warning("iMessage send failed: %s", exc)
        return False


def _format_alert(row: dict) -> tuple[str, str]:
    """Build (title, message) for a RED row.

    Keeps the message phone-readable (≤140 chars) since iMessage history
    truncates and notifications elide.
    """
    detail = row.get("detail") or {}
    if isinstance(detail, str):
        try: detail = json.loads(detail)
        except Exception: detail = {}
    station = row.get("station", "?")
    component = row.get("component", "?")

    plain_component = {
        "MODEL":        "Bot accuracy",
        "RISK":         "Open trade exposure",
        "PNL":          "7-day P&L",
        "MARKETS":      "Open Kalshi markets",
        "DATA_NBM":     "NBM forecast feed",
        "DATA_HRRR":    "HRRR forecast feed",
        "DATA_METAR":   "Weather observations feed",
        "DATA_KALSHI":  "Kalshi market feed",
    }.get(component, component)

    title = f"🌡️ weather_bot: {plain_component} RED"

    # Component-specific phrasings — prioritise the most actionable bit.
    if component == "MODEL":
        msg = (f"{station}: bot accuracy degrading. "
               f"Brier {detail.get('brier_7d','?')}, "
               f"profit gap {detail.get('edge_diff_per_fill','?')}/trade "
               f"({detail.get('n_settled_7d','?')} fills/7d). Trades paused.")
    elif component == "RISK":
        msg = (f"{station}: ${detail.get('open_notional','?')} in open trades "
               f"({int(100*detail.get('bankroll_pct',0))}% of bankroll, "
               f"{detail.get('n_open','?')} positions). Trades paused.")
    elif component == "PNL":
        msg = f"{station}: 7-day P&L is ${detail.get('net_7d','?')}. Trades paused."
    elif component == "MARKETS":
        msg = f"{station}: only {detail.get('n_open',0)} open Kalshi markets — "\
              "Kalshi may be down."
    elif component.startswith("DATA_"):
        msg = (f"{plain_component}: last update {detail.get('lag_min','?')} min ago "
               f"(normal cadence {detail.get('cadence_min','?')} min). "
               "Check launchd logs.")
    else:
        msg = f"{station}/{component}: metric {row.get('metric_value','?')}. "\
              "Open the dashboard for detail."

    # Trim to ~150 chars for SMS-ish display.
    if len(msg) > 160:
        msg = msg[:157] + "..."
    return title, msg


def _fetch_unalerted_reds() -> list[dict]:
    """Get RED rows from the latest health_check that haven't been alerted yet."""
    sql = """
    SELECT hc.* FROM health_check hc
     WHERE hc.status = 'RED'
       AND hc.alerted_at IS NULL
       AND hc.acknowledged_at IS NULL
       AND hc.ts = (
           SELECT MAX(ts) FROM health_check
            WHERE station = hc.station AND component = hc.component
       )
     ORDER BY hc.component, hc.station
    """
    with persistence.connect() as conn, conn.cursor() as cur:
        cur.execute(sql)
        return cur.fetchall()


def _mark_alerted(rows: list[dict]) -> None:
    if not rows:
        return
    sql = """UPDATE health_check SET alerted_at = now()
              WHERE ts=%s AND station=%s AND component=%s"""
    with persistence.connect() as conn, conn.cursor() as cur:
        for r in rows:
            cur.execute(sql, (r["ts"], r["station"], r["component"]))
        conn.commit()


def fire() -> int:
    """Send alerts for any RED rows that haven't been alerted yet.

    Returns count of alerts sent. Safe to call repeatedly — only un-alerted
    rows fire.
    """
    if not ALERTS_ENABLED:
        log.info("ALERTS_ENABLED=false; skipping alerts")
        return 0

    rows = _fetch_unalerted_reds()
    if not rows:
        return 0

    sent = []
    for row in rows:
        title, message = _format_alert(row)
        notif_ok = _send_macos_notification(title, message,
                                             subtitle="dashboard: 127.0.0.1:8501")
        ims_ok = _send_imessage(ALERT_PHONE, f"{title}\n{message}") if ALERT_PHONE else False
        log.warning("ALERT %s/%s sent: notif=%s imessage=%s — %s",
                    row["station"], row["component"], notif_ok, ims_ok, message)
        if notif_ok or ims_ok:
            sent.append(row)
    _mark_alerted(sent)
    return len(sent)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    n = fire()
    log.info("Fired %d alerts", n)
