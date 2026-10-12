import os
from pathlib import Path

from alembic import context

from database import ROOT, make_engine
from models import Base


def run(connection):
    context.configure(connection=connection, target_metadata=Base.metadata, render_as_batch=True,
                      compare_type=True, transactional_ddl=True)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    raise ValueError('Run migrations against a database so the existing schema can be checked.')

connection = context.config.attributes.get('connection')
if connection is not None:
    run(connection)
else:
    path = Path(os.environ.get('D3_DATABASE', Path(os.environ.get('D3_DATA_DIR', ROOT / 'data')) / 'responses.sqlite3'))
    engine = make_engine(path)
    try:
        with engine.connect().execution_options(sqlite_write=True) as connection, connection.begin():
            run(connection)
    finally:
        engine.dispose()
