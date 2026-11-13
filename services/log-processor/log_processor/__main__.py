import argparse
import json
import sys

from shared.common.publishing import SQLitePublisher
from shared.schemas import InvalidRecord, RawLog

from .normalizer import Normalizer
from .pipeline import ProcessingPipeline
from .templates import TemplateEngine


def main() -> int:
    parser = argparse.ArgumentParser(description="Normalize envelope JSONL or export the local sink")
    parser.add_argument("--sink", default=".local/normalized.sqlite3")
    parser.add_argument("--template-mode", choices=["drain3", "masked"], default="drain3")
    parser.add_argument("--timezone", default="UTC")
    parser.add_argument("--export", action="store_true")
    args = parser.parse_args()
    sink = SQLitePublisher(args.sink)
    if args.export:
        for line in sink.export():
            print(line)
        return 0
    pipeline = ProcessingPipeline(Normalizer(TemplateEngine(args.template_mode), args.timezone), sink)
    rejected = 0
    for line in sys.stdin:
        try:
            record = RawLog.from_dict(json.loads(line))
            receipt = pipeline.ingest(record)
            print(json.dumps({"event_id": receipt.event_id, "status": receipt.status}))
        except (InvalidRecord, ValueError, TypeError, RecursionError):
            rejected += 1
            print('{"status":"rejected","reason":"invalid_record"}', file=sys.stderr)
    return 1 if rejected else 0


if __name__ == "__main__":
    raise SystemExit(main())
