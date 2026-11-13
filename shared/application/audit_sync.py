"""Idempotent, read-only import of the existing local MLflow lifecycle audit ledger."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
from uuid import NAMESPACE_URL, UUID, uuid5
from sqlalchemy import insert, select
from shared.common.redaction import redact
from shared.pipeline import models as m
from shared.pipeline.database import transaction
from shared.schemas.logs import utc_time
from .jobs import data_lock

FIELDS = {'model_name','version','alias','previous_version','scope','reason','run_id','package_sha256','evaluation_sha256'}


def synchronize(engine, path, limit=10000):
    ledger=Path(path).resolve()
    if not ledger.is_file():
        raise ValueError('lifecycle_audit_missing')
    with closing(sqlite3.connect(ledger.as_uri()+'?mode=ro',uri=True,timeout=5)) as source:
        rows=source.execute('SELECT id,timestamp,action,actor,status,details FROM audit_events ORDER BY rowid LIMIT ?', (limit,)).fetchall()
    imported=0
    with transaction(engine) as db:
        data_lock(db)
        for identity,timestamp,action,actor,status,raw in rows:
            UUID(identity)
            if action not in ('register','promote'):
                continue
            target=str(uuid5(NAMESPACE_URL,'logsentinel:lifecycle:'+identity))
            if db.execute(select(m.audit_events.c.id).where(m.audit_events.c.id==target)).first():
                continue
            detail=json.loads(raw)
            clean={key:redact(str(value))[:512] for key,value in detail.items() if key in FIELDS}
            clean.update(status=redact(str(status))[:32],lifecycle_event_id=identity)
            db.execute(insert(m.audit_events).values(id=target,action='model_'+action,actor=redact(actor)[:128],
                details=clean,created_at=utc_time(timestamp)))
            imported+=1
    return {'imported':imported,'scanned':len(rows),'limit':limit}


def main():
    import argparse
    from shared.pipeline.database import make_engine
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ledger',required=True)
    args=parser.parse_args()
    try:
        print(json.dumps(synchronize(make_engine(),args.ledger)))
    except Exception:
        raise SystemExit('lifecycle_audit_import_failed') from None


if __name__=='__main__':
    main()
