"""Explicit application schema migration command; no auto-create in production workers."""
from pathlib import Path
import argparse
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['upgrade', 'downgrade', 'current'])
    parser.add_argument('--revision', help='Upgrade target (default head); explicit downgrade target required')
    args = parser.parse_args()
    if args.command == 'downgrade' and not args.revision:
        parser.error('--revision is required for downgrade')
    try:
        from alembic import command
        from alembic.config import Config
        config = Config(str(ROOT / 'infrastructure/postgres/alembic.ini'))
        if args.command == 'upgrade':
            command.upgrade(config, args.revision or 'head')
        elif args.command == 'downgrade':
            command.downgrade(config, args.revision)
        else:
            command.current(config)
        return 0
    except Exception:
        print('pipeline_schema_operation_failed', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
