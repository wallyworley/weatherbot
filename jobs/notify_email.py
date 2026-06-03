"""Shared email notifier (Resend HTTPS API).

The VPS blocks outbound SMTP (OVH default), so notifications go over Resend's
HTTPS API instead. No-op unless RESEND_API_KEY is set; failures are logged and
swallowed so a notification problem never breaks the calling job.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request

log = logging.getLogger(__name__)

_UA = "weatherbot-research/0.1 (personal research; contact /u/wallyworley)"


def send_email(subject: str, html_body: str) -> bool:
    """Send an HTML email via Resend. Returns True on success, False otherwise."""
    key = os.getenv("RESEND_API_KEY")
    if not key:
        return False
    to = os.getenv("NOTIFY_EMAIL_TO", "wallyworley@gmail.com")
    sender = os.getenv("NOTIFY_EMAIL_FROM", "weatherbot <onboarding@resend.dev>")
    payload = json.dumps({"from": sender, "to": [to], "subject": subject, "html": html_body}).encode()
    req = urllib.request.Request(
        "https://api.resend.com/emails", data=payload, method="POST",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            # Resend is behind Cloudflare, which 403s (code 1010) the bare
            # Python-urllib User-Agent. Present an explicit UA.
            "User-Agent": _UA,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            log.info("notify email sent to %s (HTTP %s)", to, r.status)
            return True
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
        detail = exc.read().decode()[:200] if hasattr(exc, "read") else ""
        log.warning("notify email failed: %s %s", exc, detail)
        return False
