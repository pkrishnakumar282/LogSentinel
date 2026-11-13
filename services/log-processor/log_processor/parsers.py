"""Strict supported formats. Unsupported records fail with payload-free reason codes."""
from dataclasses import dataclass, field
from datetime import UTC, datetime
import json
import re
from zoneinfo import ZoneInfo

from shared.common.privacy import is_authentication_record
from shared.schemas.logs import InvalidRecord, RawLog, identifier, utc_time

LEVELS = {"DEBUG": "INFO", "TRACE": "INFO", "NOTICE": "INFO", "INFO": "INFO",
          "WARN": "WARNING", "WARNING": "WARNING", "ERROR": "ERROR", "ERR": "ERROR",
          "CRIT": "CRITICAL", "CRITICAL": "CRITICAL", "FATAL": "CRITICAL",
          "ALERT": "CRITICAL", "EMERG": "CRITICAL", "PANIC": "CRITICAL"}
PRIORITIES = {"0": "CRITICAL", "1": "CRITICAL", "2": "CRITICAL", "3": "ERROR",
              "4": "WARNING", "5": "INFO", "6": "INFO", "7": "INFO"}
ACCESS = re.compile(r'^(?P<client>\S+) \S+ \S+ \[(?P<time>[^]]+)\] '
                    r'"(?P<request>(?:[^"\\]|\\.)*)" (?P<status>\d{3}) (?P<bytes>\d+|-)'
                    r'(?: "(?:[^"\\]|\\.)*" "(?:[^"\\]|\\.)*")?$')
NGINX_ERROR = re.compile(r"^(?P<time>\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}) "
                         r"\[(?P<level>\w+)\] \d+#\d+: (?:\*\d+ )?(?P<message>.+)$")
APP = re.compile(r"^(?P<time>\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}"
                 r"(?:[.,]\d+)?(?:Z|[+-]\d{2}:\d{2})?)\s+"
                 r"(?P<level>[A-Za-z]+)\s+(?P<service>[\w.@:-]+)\s+(?:-\s+)?(?P<message>.+)$")
SYSLOG = re.compile(r"^(?P<time>[A-Z][a-z]{2} +\d{1,2} \d{2}:\d{2}:\d{2}) "
                    r"(?P<host>[\w.-]+) (?P<service>[\w.@:-]+?)(?:\[\d+\])?: (?P<message>.+)$")



@dataclass
class Parsed:
    timestamp: str
    severity: str
    message: str
    service: str
    hostname: str
    status_code: int | None = None
    metadata: dict = field(default_factory=dict)


def level(value: str) -> str:
    try:
        return LEVELS[value.upper()]
    except (KeyError, AttributeError):
        raise InvalidRecord("invalid_severity") from None


def timestamp(value: str, timezone: ZoneInfo) -> str:
    try:
        dt = datetime.fromisoformat(value.replace(",", ".").replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone)
        return dt.astimezone(UTC).isoformat()
    except (ValueError, TypeError, AttributeError, OverflowError):
        raise InvalidRecord("invalid_timestamp") from None


def structured(raw: str) -> dict:
    try:
        value = json.loads(raw)
    except (ValueError, RecursionError):
        raise InvalidRecord("invalid_json") from None
    if not isinstance(value, dict):
        raise InvalidRecord("invalid_json_object")
    return value


def parse(record: RawLog, timezone: str = "UTC") -> Parsed:
    tz = ZoneInfo(timezone)
    try:
        if record.source == "nginx":
            result = nginx(record, tz)
        elif record.source == "linux":
            result = linux(record, tz)
        else:
            result = application(record, tz)
        identifier(result.service)
        identifier(result.hostname)
        if not isinstance(result.message, str) or not result.message.strip():
            raise InvalidRecord("empty_message")
        if is_authentication_record(result.service, result.message):
            raise InvalidRecord("sensitive_auth_record")
        return result
    except (KeyError, TypeError, AttributeError, ValueError, OverflowError) as exc:
        if isinstance(exc, InvalidRecord):
            raise
        raise InvalidRecord("malformed_record") from None


def nginx(record: RawLog, tz: ZoneInfo) -> Parsed:
    match = ACCESS.fullmatch(record.raw.strip())
    if match:
        status = int(match["status"])
        if not 100 <= status <= 599:
            raise InvalidRecord("invalid_status_code")
        dt = datetime.strptime(match["time"], "%d/%b/%Y:%H:%M:%S %z")
        # Drop client identity, remote_user, referrer and user agent entirely.
        return Parsed(dt.astimezone(UTC).isoformat(),
                      "ERROR" if status >= 500 else "WARNING" if status >= 400 else "INFO",
                      f'{match["request"]} status={status}', record.service, record.hostname,
                      status, {"format": "nginx_access", "bytes_sent": int(match["bytes"])
                               if match["bytes"] != "-" else None})
    match = NGINX_ERROR.fullmatch(record.raw.strip())
    if not match:
        raise InvalidRecord("unsupported_nginx_format")
    return Parsed(timestamp(match["time"].replace("/", "-"), tz), level(match["level"]),
                  match["message"], record.service, record.hostname,
                  metadata={"format": "nginx_error"})


def application(record: RawLog, tz: ZoneInfo) -> Parsed:
    if record.raw.lstrip().startswith("{"):
        value = structured(record.raw)
        status = value.get("status_code")
        if status is not None and (type(status) is not int or not 100 <= status <= 599):
            raise InvalidRecord("invalid_status_code")
        return Parsed(timestamp(value["timestamp"], tz), level(value.get("severity", value.get("level", "INFO"))),
                      value["message"], value.get("service", record.service), record.hostname,
                      status, {"format": "application_json"})
    match = APP.fullmatch(record.raw.strip())
    if not match:
        raise InvalidRecord("unsupported_application_format")
    return Parsed(timestamp(match["time"], tz), level(match["level"]), match["message"],
                  match["service"], record.hostname, metadata={"format": "application_text"})


def linux(record: RawLog, tz: ZoneInfo) -> Parsed:
    if record.raw.lstrip().startswith("{"):
        value = structured(record.raw)
        priority = str(value.get("PRIORITY", "6"))
        if priority not in PRIORITIES:
            raise InvalidRecord("invalid_priority")
        # systemd __REALTIME_TIMESTAMP is integer microseconds since Unix epoch.
        micros = int(value["__REALTIME_TIMESTAMP"])
        seconds, remainder = divmod(micros, 1_000_000)
        dt = datetime.fromtimestamp(seconds, UTC).replace(microsecond=remainder)
        service = value.get("_SYSTEMD_USER_UNIT", value.get("_SYSTEMD_UNIT",
                            value.get("SYSLOG_IDENTIFIER", record.service)))
        return Parsed(dt.isoformat(), PRIORITIES[priority], value["MESSAGE"], service,
                      value.get("_HOSTNAME", record.hostname), metadata={"format": "journal_json"})
    match = SYSLOG.fullmatch(record.raw.strip())
    if not match:
        raise InvalidRecord("unsupported_linux_format")
    observed = utc_time(record.observed_at).astimezone(tz)
    # RFC3164 has no year: pick the closest year to collection time (including New Year).
    candidates = []
    for year in (observed.year - 1, observed.year, observed.year + 1):
        try:
            dt = datetime.strptime(f'{year} {match["time"]}', "%Y %b %d %H:%M:%S").replace(tzinfo=tz)
            candidates.append(dt)
        except ValueError:
            pass
    if not candidates:
        raise InvalidRecord("invalid_timestamp")
    dt = min(candidates, key=lambda candidate: abs((candidate - observed).total_seconds()))
    message = match["message"]
    severity_match = re.match(r"(?:\[)?(DEBUG|INFO|NOTICE|WARN(?:ING)?|ERR(?:OR)?|CRIT(?:ICAL)?)(?:\])?\b", message, re.I)
    severity = level(severity_match[1]) if severity_match else "INFO"
    return Parsed(dt.astimezone(UTC).isoformat(), severity, message, match["service"],
                  match["host"], metadata={"format": "syslog", "year_inferred": True})
