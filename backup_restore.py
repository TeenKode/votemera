"""Validate and prepare SQLite backups before replacing the live files."""

import sqlite3
from contextlib import closing
from uuid import uuid4

from storage_schema import ACCESS_MIGRATIONS, DATA_MIGRATIONS, migrate


def schema(connection):
    return set(tuple(row) for row in connection.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
    ))


def migrated_schema(migrations):
    with closing(sqlite3.connect(':memory:')) as c:
        c.row_factory = sqlite3.Row
        migrate(c, migrations)
        return schema(c)


def check_integrity(connection):
    if connection.execute('PRAGMA quick_check').fetchone()[0] != 'ok' or connection.execute('PRAGMA foreign_key_check').fetchone():
        raise ValueError('Повреждённая база SQLite')


def prepare_data(connection, expected_schema):
    actual_schema = schema(connection)
    additions = {'event_meta', 'runtime_config', 'terminal_config', 'audit_log', 'yandex_source', 'yandex_sync_jobs'}
    missing = expected_schema - actual_schema
    if actual_schema - expected_schema or any(row[0] != 'table' or row[1] not in additions for row in missing):
        raise ValueError('Несовместимая схема базы голосования')
    migrate(connection, DATA_MIGRATIONS)
    if schema(connection) != expected_schema:
        raise ValueError('Несовместимая схема базы голосования')
    check_integrity(connection)
    active = connection.execute('''SELECT 1 FROM settings s JOIN rounds r ON r.id=s.active_round
        AND r.event_id=s.active_event WHERE s.id=1''').fetchone()
    if not active or connection.execute('SELECT count(*) FROM settings').fetchone()[0] != 1:
        raise ValueError('Нет корректного активного мероприятия')
    connection.execute("UPDATE settings SET opened=0,epoch=?,display='hidden' WHERE id=1", (uuid4().hex,))
    connection.execute('UPDATE runtime_config SET deadline=NULL WHERE id=1')
    connection.execute('UPDATE yandex_source SET enabled=0,revision=?,error=?',
                       (uuid4().hex, 'Восстановлено из резервной копии; включите синхронизацию'))
    connection.execute('DELETE FROM yandex_sync_jobs')


def prepare_access(connection):
    expected_schema = migrated_schema(ACCESS_MIGRATIONS)
    old_schema = migrated_schema(ACCESS_MIGRATIONS[:1])
    actual_schema = schema(connection)
    if actual_schema != expected_schema and (actual_schema - old_schema or
            any(row[1] != 'yandex_credentials' for row in old_schema - actual_schema)):
        raise ValueError('Несовместимая схема базы доступа')
    migrate(connection, ACCESS_MIGRATIONS)
    if schema(connection) != expected_schema:
        raise ValueError('Несовместимая схема базы доступа')
    check_integrity(connection)
    row = connection.execute('SELECT password_hash FROM access_settings WHERE id=1').fetchone()
    if not row or not row[0]:
        raise ValueError('В копии отсутствует пароль администратора')
    connection.execute('DELETE FROM access_sessions')
