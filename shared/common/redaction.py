"""Conservative redaction before persistence, templates or stream publication.

This is defense in depth, not a guarantee that arbitrary prose contains no PII.
Structured unknown fields are discarded by parsers, not copied into metadata.
"""
import ipaddress
import re

REDACTION_VERSION = "1"
REDACTED = "[REDACTED]"
RULES = [
    # Terminal escapes and control characters cannot become dashboard/log injection.
    (re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]"), ""),
    (re.compile(r"[\x00-\x08\x0b-\x1f\x7f]"), " "),
    (re.compile(r"(?i)\b(authorization|proxy-authorization)\s*[:=]\s*(?:bearer|basic)\s+[^\s,;\"']+"),
     r"\1=[REDACTED]"),
    (re.compile(r"(?i)\b(cookie|set-cookie)\s*[:=][^\r\n]+"), r"\1=[REDACTED]"),
    (re.compile(r'''(?ix)(["']?\b(?:password|passwd|pwd|secret|token|access_token|refresh_token|
        api[_-]?key|session[_-]?id|credential|email|username|user|remote_user|
        client_secret|secret[_-]?key|access[_-]?key|authorization|proxy-authorization)["']?\s*[:=]\s*)
        (?:"[^"\r\n]*"|'[^'\r\n]*'|[^\s,;&]+)'''), r"\1[REDACTED]"),
    (re.compile(r"(?i)(https?://)[^/@\s]+:[^/@\s]+@"), r"\1[REDACTED]@"),
    (re.compile(r"(?i)\bBearer\s+[^\s,;\"']+"), "Bearer [REDACTED]"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"), REDACTED),
    (re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b"), "[EMAIL]"),
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "[IP]"),
    # URLs often contain arbitrary sensitive query names; mask the entire query.
    (re.compile(r"\?[^\s\"'<>]*"), "?[REDACTED]"),
]
IPV6_CANDIDATE = re.compile(r"(?<![\w:])(?:[0-9a-fA-F]*:){2,}[0-9a-fA-F:.]+(?:%\w+)?")


def _ipv6(match: re.Match) -> str:
    try:
        ipaddress.IPv6Address(match.group().split("%", 1)[0])
        return "[IP]"
    except ValueError:
        return match.group()


def redact(message: str) -> str:
    # Decode Nginx escaped bytes before applying any rules (e.g. token\x3dsecret).
    message = re.sub(r"\\x([0-9a-fA-F]{2})", lambda m: chr(int(m[1], 16)), message)
    for pattern, replacement in RULES:
        message = pattern.sub(replacement, message)
    message = IPV6_CANDIDATE.sub(_ipv6, message)
    return " ".join(message.split())
