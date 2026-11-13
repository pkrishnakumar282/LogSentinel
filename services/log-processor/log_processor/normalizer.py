from shared.common.redaction import REDACTION_VERSION, redact
from shared.schemas import InvalidRecord, NormalizedLog, RawLog

from .parsers import parse
from .templates import TemplateEngine


class Normalizer:
    def __init__(self, templates: TemplateEngine, timezone: str = "UTC"):
        # Validate timezone at startup, not on the first log event.
        from zoneinfo import ZoneInfo
        ZoneInfo(timezone)
        self.templates = templates
        self.timezone = timezone

    def normalize(self, record: RawLog) -> NormalizedLog:
        parsed = parse(record, self.timezone)
        message = redact(parsed.message)
        if not message:
            raise InvalidRecord("empty_message")
        return NormalizedLog(
            event_id=record.event_id, timestamp=parsed.timestamp, source=record.source,
            service=parsed.service, hostname=parsed.hostname, severity=parsed.severity,
            message=message, template=self.templates.extract(message), status_code=parsed.status_code,
            metadata={**parsed.metadata, "synthetic": record.synthetic,
                      "redaction_version": REDACTION_VERSION,
                      "template_engine": self.templates.mode},
        )
