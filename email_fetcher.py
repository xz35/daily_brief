"""
email_fetcher.py — fetch manually-forwarded research reports from Gmail.

Connects to xdailybrief@gmail.com via IMAP, reads unread emails from
whitelisted senders (RESEARCH_SENDER_WHITELIST), extracts the text body,
and returns content for Gemini summarization. Marks emails as read after
processing so they are never picked up again on a subsequent run.

Credentials used: GMAIL_ADDRESS + GMAIL_APP_PASSWORD (already in secrets).
New secret required: RESEARCH_SENDER_WHITELIST (comma-separated sender addresses).

No attachments are processed — text and HTML body only. Embedded images are lost;
this is acceptable since research report text carries the substance.
"""

import email
import imaplib
import logging
import os
import re
from email.header import decode_header

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993
MAX_BODY_CHARS = 15_000   # Truncate very long reports before sending to Gemini
REQUEST_TIMEOUT = 30


def fetch_research_emails():
    """Fetch unread research emails from whitelisted senders.

    Returns:
        list of {subject: str, body: str} dicts, one per email.
        Returns [] if credentials not configured or no matching emails found.
    """
    address       = os.getenv("GMAIL_ADDRESS")
    password      = os.getenv("GMAIL_APP_PASSWORD")
    whitelist_raw = os.getenv("RESEARCH_SENDER_WHITELIST", "")

    if not all([address, password, whitelist_raw]):
        logger.info("Research email fetch skipped: GMAIL_ADDRESS, GMAIL_APP_PASSWORD, "
                    "or RESEARCH_SENDER_WHITELIST not configured")
        return []

    whitelist = [e.strip().lower() for e in whitelist_raw.split(",") if e.strip()]
    if not whitelist:
        logger.info("Research email fetch skipped: RESEARCH_SENDER_WHITELIST is empty")
        return []

    try:
        results = _fetch_from_imap(address, password, whitelist)
        logger.info(f"Research emails: {len(results)} report(s) fetched")
        return results
    except Exception as e:
        logger.warning(f"Research email fetch failed (non-fatal): {e}")
        return []


# ── IMAP internals ─────────────────────────────────────────────────────────

def _fetch_from_imap(address, password, whitelist):
    """Connect, search, parse, mark-as-read, return results."""
    results = []

    with imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT) as mail:
        mail.login(address, password)
        mail.select("INBOX")

        # Collect unique email IDs matching any whitelisted sender
        email_ids = set()
        for sender in whitelist:
            status, data = mail.search(None, f'(UNSEEN FROM "{sender}")')
            if status == "OK" and data[0]:
                for eid in data[0].split():
                    email_ids.add(eid)

        if not email_ids:
            logger.info("Research emails: no unread messages from whitelisted senders")
            return []

        logger.info(f"Research emails: {len(email_ids)} unread message(s) to process")

        for eid in sorted(email_ids):   # sorted for deterministic order
            status, msg_data = mail.fetch(eid, "(RFC822)")
            if status != "OK" or not msg_data or not msg_data[0]:
                logger.warning(f"Research emails: failed to fetch message id {eid}")
                continue

            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)

            subject = _decode_header_value(msg.get("Subject", "(no subject)"))
            body    = _extract_body(msg)

            if not body.strip():
                logger.warning(f"Research emails: empty body for '{subject}' — skipping")
                mail.store(eid, "+FLAGS", "\\Seen")
                continue

            if len(body) > MAX_BODY_CHARS:
                logger.info(f"Research emails: truncating '{subject}' "
                            f"({len(body)} → {MAX_BODY_CHARS} chars)")
                body = body[:MAX_BODY_CHARS] + "\n[Report truncated due to length]"

            results.append({"subject": subject, "body": body})

            # Mark as read — prevents re-processing on any future run
            mail.store(eid, "+FLAGS", "\\Seen")
            logger.info(f"Research emails: processed and marked as read — '{subject}'")

    return results


# ── Email parsing helpers ───────────────────────────────────────────────────

def _extract_body(msg):
    """Extract readable text from an email, preferring plain text over HTML."""
    plain_parts = []
    html_parts  = []

    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            disposition  = str(part.get("Content-Disposition", ""))
            if "attachment" in disposition:
                continue
            if content_type == "text/plain":
                plain_parts.append(_decode_part(part))
            elif content_type == "text/html":
                html_parts.append(_decode_part(part))
    else:
        content_type = msg.get_content_type()
        if content_type == "text/plain":
            plain_parts.append(_decode_part(msg))
        elif content_type == "text/html":
            html_parts.append(_decode_part(msg))

    if plain_parts:
        return "\n\n".join(plain_parts).strip()
    if html_parts:
        return _html_to_text("\n".join(html_parts))
    return ""


def _decode_part(part):
    """Decode a message part payload to a string."""
    payload = part.get_payload(decode=True)
    if payload is None:
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except (LookupError, UnicodeDecodeError):
        return payload.decode("utf-8", errors="replace")


def _html_to_text(html):
    """Strip HTML markup to readable plain text."""
    try:
        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style", "head"]):
            tag.decompose()
        text  = soup.get_text(separator="\n")
        lines = [line.strip() for line in text.splitlines()]
        lines = [l for l in lines if l]
        return "\n".join(lines)
    except Exception as e:
        logger.warning(f"HTML parsing failed, falling back to regex strip: {e}")
        return re.sub(r"<[^>]+>", " ", html)


def _decode_header_value(value):
    """Decode an RFC2047-encoded email header value to a plain string."""
    if not value:
        return ""
    parts = decode_header(value)
    decoded = []
    for part, charset in parts:
        if isinstance(part, bytes):
            try:
                decoded.append(part.decode(charset or "utf-8", errors="replace"))
            except (LookupError, UnicodeDecodeError):
                decoded.append(part.decode("utf-8", errors="replace"))
        else:
            decoded.append(part)
    return "".join(decoded)
