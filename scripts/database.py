"""Private SQLite engines, transactions and versioned schema upgrades."""

from contextlib import contextmanager
from pathlib import Path
from threading import Lock

from alembic import command
from alembic.config import Config
from sqlalchemy import URL, create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

ROOT = Path(__file__).resolve().parents[1]
_migration_lock = Lock()


def make_engine(path, create=True):
    path = Path(path)
    if path.is_symlink():
        raise ValueError('Use a regular private database file, not a symlink.')
    if create:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.touch(mode=0o600, exist_ok=True)
    path.chmod(0o600)
    engine = create_engine(URL.create('sqlite+pysqlite', database=str(path.resolve())),
                           connect_args={'timeout':10}, poolclass=NullPool, hide_parameters=True)

    @event.listens_for(engine, 'connect')
    def configure(connection, _):
        connection.isolation_level = None

    @event.listens_for(engine, 'begin')
    def begin(connection):
        # Lock before reading on writes; stale-form checks and updates must be atomic.
        connection.exec_driver_sql('BEGIN IMMEDIATE' if connection.get_execution_options().get('sqlite_write') else 'BEGIN')

    return engine


@contextmanager
def transaction(engine, write=False):
    with engine.connect().execution_options(sqlite_write=write) as connection, connection.begin():
        with Session(bind=connection) as session:
            yield session
            session.flush()


def upgrade(engine):
    config = Config(str(ROOT / 'alembic.ini'))
    # Alembic's environment proxy is process-global; serialize startup across event databases.
    with _migration_lock, engine.connect().execution_options(sqlite_write=True) as connection, connection.begin():
        config.attributes['connection'] = connection
        command.upgrade(config, 'head')
