import imaplib, email, re, json
from typing import List, Tuple, Optional
from django.conf import settings

JSON_BLOCK_RE = re.compile(r"\{[\s\S]*\}", re.MULTILINE)

def _get_body_text(msg: email.message.Message) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = str(part.get("Content-Disposition") or "")
            if ctype == "text/plain" and "attachment" not in disp:
                charset = part.get_content_charset() or "utf-8"
                return part.get_payload(decode=True).decode(charset, errors="ignore")
    # fallback
    payload = msg.get_payload(decode=True)
    if isinstance(payload, bytes):
        return payload.decode(msg.get_content_charset() or "utf-8", errors="ignore")
    return str(payload)

def _extract_json(text: str) -> Optional[dict]:
    # try to find the first {...} block
    m = JSON_BLOCK_RE.search(text)
    if not m:
        return None
    raw = m.group(0)
    try:
        return json.loads(raw)
    except Exception:
        return None

def fetch_emails_and_parse() -> List[dict]:
    """Return list of parsed alert dicts; marks processed mails as seen."""
    host = settings.TV_IMAP_HOST; port = settings.TV_IMAP_PORT
    user = settings.TV_IMAP_USER; pw = settings.TV_IMAP_PASSWORD
    folder = settings.TV_IMAP_FOLDER
    allow_from = (settings.TV_ALLOWED_FROM or "").lower()
    subj_contains = settings.TV_SUBJECT_CONTAINS

    if not all([host, port, user, pw]):
        return []

    imap = imaplib.IMAP4_SSL(host, port)
    try:
        imap.login(user, pw)
        imap.select(folder)
        # unseen messages that likely are alerts
        typ, data = imap.search(None, '(UNSEEN)')
        if typ != "OK":
            return []
        ids = data[0].split()
        parsed = []
        for _id in ids:
            typ, msgbytes = imap.fetch(_id, '(RFC822)')
            if typ != "OK":
                continue
            msg = email.message_from_bytes(msgbytes[0][1])
            from_ok = allow_from in (msg.get("From","").lower())
            subj_ok = subj_contains.lower() in (msg.get("Subject","").lower())
            if not (from_ok or subj_ok):
                # leave it unseen if not an alert
                continue

            body = _get_body_text(msg)
            payload = _extract_json(body)
            if payload:
                parsed.append(payload)
                # mark seen
                imap.store(_id, '+FLAGS', '\\Seen')
        return parsed
    finally:
        try:
            imap.close()
        except Exception:
            pass
        try:
            imap.logout()
        except Exception:
            pass
