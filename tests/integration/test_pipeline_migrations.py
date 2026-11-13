from pathlib import Path
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]
READY=all(importlib.util.find_spec(name) for name in ['sqlalchemy','alembic'])


@unittest.skipUnless(READY,'SQLAlchemy/Alembic unavailable')
class PipelineMigrations(unittest.TestCase):
    def test_actual_upgrade_downgrade_reupgrade_and_foreign_keys(self):
        from sqlalchemy import inspect,insert
        from sqlalchemy.exc import IntegrityError
        from shared.pipeline.database import make_engine
        from shared.pipeline.models import predictions
        from shared.pipeline.store import now
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'migrations.db'
            env=dict(os.environ,PIPELINE_DB_BACKEND='sqlite',PIPELINE_SQLITE_PATH=str(path))
            for arguments in [('upgrade',),('downgrade','--revision','base'),('upgrade',)]:
                result=subprocess.run([sys.executable,str(ROOT/'scripts/manage_pipeline.py'),*arguments],
                    cwd=ROOT,env=env,capture_output=True,text=True,timeout=20)
                self.assertEqual(result.returncode,0,result.stderr)
            engine=make_engine('sqlite:///'+str(path))
            self.assertIn('alerts',inspect(engine).get_table_names())
            with self.assertRaises(IntegrityError):
                with engine.begin() as db:
                    db.execute(insert(predictions).values(id='missing-prediction',event_id='missing-event',
                        model_id='missing-model',anomaly_score=.5,is_anomaly=False,context_warm=False,created_at=now()))
            engine.dispose()

    def test_postgres_schema_compiles_with_real_dialect(self):
        from sqlalchemy.dialects import postgresql
        from sqlalchemy.schema import CreateTable
        from shared.pipeline.models import metadata
        for table in metadata.sorted_tables:
            sql=str(CreateTable(table).compile(dialect=postgresql.dialect()))
            self.assertIn('CREATE TABLE',sql)


if __name__=='__main__':unittest.main()
