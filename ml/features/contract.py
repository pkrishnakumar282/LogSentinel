"""Explicit allowlist: identities, labels, timestamps and arbitrary metadata are not predictors."""
FEATURE_VERSION = "1.0.0"
CATEGORICAL_COLUMNS = ["severity", "source", "service"]
NUMERIC_COLUMNS = [
    "status_code", "has_http_status", "http_4xx", "http_5xx", "message_length",
    "event_frequency", "source_frequency", "global_frequency", "events_per_second",
    "template_frequency", "error_rate", "window_observed_seconds", "window_is_warm",
]
FEATURE_COLUMNS = ["text", *CATEGORICAL_COLUMNS, *NUMERIC_COLUMNS]
