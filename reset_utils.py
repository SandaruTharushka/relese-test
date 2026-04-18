from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence

from sqlalchemy import inspect as sa_inspect, text
from sqlalchemy.schema import MetaData, Table
from sqlalchemy.orm import Session


BeforeEachHook = Callable[[Table], None]


def ordered_delete_tables(metadata: MetaData, table_names: Iterable[str]) -> list[Table]:
    """Return child-first delete order for the selected tables."""
    requested_names = list(dict.fromkeys(table_names))
    requested_set = set(requested_names)
    sorted_tables = [table for table in metadata.sorted_tables if table.name in requested_set]
    resolved_names = {table.name for table in sorted_tables}
    missing_names = requested_set - resolved_names
    if missing_names:
        missing = ', '.join(sorted(missing_names))
        raise RuntimeError(f'Could not resolve reset tables from metadata: {missing}')
    return list(reversed(sorted_tables))


def _reset_sqlite_sequences(session: Session, tables: Sequence[Table]) -> None:
    bind = session.get_bind()
    if bind is None or bind.dialect.name != 'sqlite':
        return

    inspector = sa_inspect(bind)
    if 'sqlite_sequence' not in inspector.get_table_names():
        return

    for table in tables:
        session.execute(
            text('DELETE FROM sqlite_sequence WHERE name = :table_name'),
            {'table_name': table.name},
        )


def delete_all_rows(
    session: Session,
    tables: Sequence[Table],
    *,
    before_each: BeforeEachHook | None = None,
    disable_sqlite_foreign_keys: bool = False,
) -> list[dict[str, int | str]]:
    """Delete all rows from the supplied tables in order using one transaction."""
    bind = session.get_bind()
    sqlite_fk_toggled = False
    if (
        disable_sqlite_foreign_keys
        and bind is not None
        and bind.dialect.name == 'sqlite'
    ):
        session.execute(text('PRAGMA foreign_keys=OFF'))
        sqlite_fk_toggled = True

    existing_table_names: set[str] | None = None
    if bind is not None:
        try:
            existing_table_names = set(sa_inspect(bind).get_table_names())
        except Exception:
            existing_table_names = None

    deleted_rows: list[dict[str, int | str]] = []
    try:
        for table in tables:
            if existing_table_names is not None and table.name not in existing_table_names:
                deleted_rows.append({
                    'table': table.name,
                    'rows': 0,
                    'skipped': 'table_not_present',
                })
                continue
            if before_each is not None:
                before_each(table)
            result = session.execute(table.delete())
            deleted_rows.append({
                'table': table.name,
                'rows': int(result.rowcount or 0),
            })

        _reset_sqlite_sequences(session, tables)
        return deleted_rows
    finally:
        if sqlite_fk_toggled:
            session.execute(text('PRAGMA foreign_keys=ON'))
