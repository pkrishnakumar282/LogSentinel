"""Shared authentication-log exclusion policy for ingestion and training imports."""
import re

AUTH_SERVICE = re.compile(
    r"(?i)(?:ssh|sshd|sudo|su|login|systemd-logind|polkit|pam|gdm|sddm)(?:[.@:-]|$)"
)
AUTH_MESSAGE = re.compile(
    r"(?i)\b(?:authentication|authorization failure|failed password|accepted password|"
    r"accepted publickey|pam_unix|session opened for user|session closed for user)\b"
)


def is_authentication_record(service: str, message: str) -> bool:
    return bool(AUTH_SERVICE.search(service) or AUTH_MESSAGE.search(message))
