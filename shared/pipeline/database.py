"""SQLAlchemy connection configuration; credentials are never interpolated into log output."""
from contextlib import contextmanager
import os
from pathlib import Path

from sqlalchemy import URL, create_engine, event


def database_url():
    backend = os.environ.get('PIPELINE_DB_BACKEND', 'postgres')
    if backend == 'sqlite':
        path = Path(os.environ.get('PIPELINE_SQLITE_PATH', '.local/pipeline.sqlite3'))
        path.parent.mkdir(parents=True, exist_ok=True)
        return URL.create('sqlite', database=str(path))
    if backend != 'postgres' or not os.environ.get('POSTGRES_PASSWORD'):
        raise ValueError('postgres_configuration_required')
    return URL.create('postgresql+psycopg', username=os.environ.get('POSTGRES_USER', 'logsentinel'),
        password=os.environ['POSTGRES_PASSWORD'], host=os.environ.get('POSTGRES_HOST', 'postgres'),
        port=int(os.environ.get('POSTGRES_PORT', '5432')), database=os.environ.get('POSTGRES_DB', 'logsentinel'))


def make_engine(url=None):
    url = url or database_url()
    options = {'echo': False, 'hide_parameters': True, 'pool_pre_ping': True}
    if str(url).startswith('sqlite'):
        engine = create_engine(url, connect_args={'timeout': 10, 'check_same_thread': False}, **options)
        @event.listens_for(engine, 'connect')
        def sqlite_settings(connection, _record):
            cursor = connection.cursor()
            cursor.execute('PRAGMA foreign_keys=ON')
            cursor.close()
    else:
        engine = create_engine(url, pool_size=5, max_overflow=5, pool_recycle=1800,
            connect_args={'connect_timeout': 5, 'options': '-c statement_timeout=10000 -c lock_timeout=5000'}, **options)
    return engine


@contextmanager
def transaction(engine):
    with engine.connect() as connection:
        if engine.dialect.name == 'sqlite':
            connection.exec_driver_sql('BEGIN IMMEDIATE')
        else:
            connection.begin()
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
