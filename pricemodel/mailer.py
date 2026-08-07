"""SMTP delivery for the daily report."""

from __future__ import annotations

import logging
import smtplib
import ssl
import time
from email.message import EmailMessage

from .config import EmailConfig

log = logging.getLogger(__name__)


def build_message(cfg: EmailConfig, subject: str, text: str, html: str) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg.sender
    msg["To"] = ", ".join(cfg.recipients)
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    return msg


def send(cfg: EmailConfig, msg: EmailMessage, attempts: int = 3) -> None:
    """Send with retries; transient SMTP and DNS failures are common in CI."""
    context = ssl.create_default_context()
    last: Exception | None = None

    for attempt in range(1, attempts + 1):
        try:
            if cfg.port == 465:
                with smtplib.SMTP_SSL(cfg.host, cfg.port, context=context, timeout=45) as s:
                    s.login(cfg.username, cfg.password)
                    s.send_message(msg)
            else:
                with smtplib.SMTP(cfg.host, cfg.port, timeout=45) as s:
                    s.ehlo()
                    if cfg.use_tls:
                        s.starttls(context=context)
                        s.ehlo()
                    s.login(cfg.username, cfg.password)
                    s.send_message(msg)
            log.info("report emailed to %s", ", ".join(cfg.recipients))
            return
        except (smtplib.SMTPException, OSError) as exc:
            last = exc
            log.warning("email attempt %d/%d failed: %s", attempt, attempts, exc)
            if attempt < attempts:
                time.sleep(2**attempt)

    raise RuntimeError(f"failed to send report after {attempts} attempts: {last}")
