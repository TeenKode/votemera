"""Transactional upgrades from the historical, unversioned SQLite files."""

import hashlib


def migrate(connection, migrations):
    version = connection.execute('PRAGMA user_version').fetchone()[0]
    if not 0 <= version <= len(migrations):
        raise ValueError(f'Неподдерживаемая версия схемы SQLite: {version}')
    for number, upgrade in enumerate(migrations[version:], start=version + 1):
        upgrade(connection)
        connection.execute(f'PRAGMA user_version={number}')


def data_v1(c):
    c.execute("CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL, active_event INTEGER, active_round INTEGER, opened INTEGER NOT NULL DEFAULT 0, epoch TEXT NOT NULL DEFAULT '', display TEXT NOT NULL DEFAULT 'live')")
    c.execute('CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, title TEXT NOT NULL, question TEXT NOT NULL, options TEXT NOT NULL, created REAL NOT NULL)')
    c.execute('CREATE TABLE IF NOT EXISTS rounds(id INTEGER PRIMARY KEY, event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE, name TEXT NOT NULL, position INTEGER NOT NULL)')
    c.execute('CREATE TABLE IF NOT EXISTS votes(id INTEGER PRIMARY KEY, round_id INTEGER NOT NULL REFERENCES rounds(id) ON DELETE CASCADE, choice INTEGER NOT NULL, nonce TEXT NOT NULL UNIQUE, terminal TEXT NOT NULL, created REAL NOT NULL)')
    c.execute('CREATE INDEX IF NOT EXISTS votes_round ON votes(round_id,choice)')
    c.execute('CREATE INDEX IF NOT EXISTS votes_terminal ON votes(terminal,created)')
    if 'questions' not in [r['name'] for r in c.execute('PRAGMA table_info(events)')]:
        c.execute('ALTER TABLE events ADD COLUMN questions TEXT')
    if 'question_index' not in [r['name'] for r in c.execute('PRAGMA table_info(votes)')]:
        c.execute('ALTER TABLE votes ADD COLUMN question_index INTEGER NOT NULL DEFAULT 0')
    if 'ballot_id' not in [r['name'] for r in c.execute('PRAGMA table_info(votes)')]:
        # Recover exact v0.2/v0.3 links from deterministic nonces, never from time proximity.
        c.execute('ALTER TABLE votes ADD COLUMN ballot_id TEXT')
        for v in c.execute('SELECT * FROM votes WHERE question_index=0').fetchall():
            c.execute('UPDATE votes SET ballot_id=? WHERE id=?',(v['nonce'],v['id']))
            for i in range(1,12):
                n=hashlib.sha256(('ballot:'+v['nonce']+':'+str(i)).encode()).hexdigest()[:32]
                c.execute('UPDATE votes SET ballot_id=? WHERE nonce=? AND round_id=? AND question_index=? AND terminal=? AND created=?',(v['nonce'],n,v['round_id'],i,v['terminal'],v['created']))
    c.execute('CREATE INDEX IF NOT EXISTS votes_ballot ON votes(round_id,ballot_id,question_index)')
    c.execute('CREATE TABLE IF NOT EXISTS analysis_config(event_id INTEGER PRIMARY KEY REFERENCES events(id) ON DELETE CASCADE, first_q INTEGER NOT NULL, second_q INTEGER NOT NULL, chart TEXT NOT NULL)')
    c.execute('CREATE TABLE IF NOT EXISTS presentation_config(event_id INTEGER PRIMARY KEY REFERENCES events(id) ON DELETE CASCADE,data TEXT NOT NULL)')
    c.execute('CREATE INDEX IF NOT EXISTS votes_question ON votes(round_id,question_index,choice)')
    c.execute('CREATE TABLE IF NOT EXISTS event_meta(event_id INTEGER PRIMARY KEY REFERENCES events(id) ON DELETE CASCADE,archived INTEGER NOT NULL DEFAULT 0,template INTEGER NOT NULL DEFAULT 0,brand TEXT NOT NULL DEFAULT "",accent TEXT NOT NULL DEFAULT "",logo TEXT NOT NULL DEFAULT "")')
    c.execute('CREATE TABLE IF NOT EXISTS runtime_config(id INTEGER PRIMARY KEY CHECK(id=1),deadline REAL)')
    c.execute('INSERT OR IGNORE INTO runtime_config(id) VALUES(1)')
    c.execute('CREATE TABLE IF NOT EXISTS terminal_config(id TEXT PRIMARY KEY,label TEXT NOT NULL DEFAULT "",locked INTEGER NOT NULL DEFAULT 0,event_id INTEGER REFERENCES events(id) ON DELETE SET NULL,revision INTEGER NOT NULL DEFAULT 0)')
    c.execute('CREATE TABLE IF NOT EXISTS yandex_source(event_id INTEGER PRIMARY KEY REFERENCES events(id) ON DELETE CASCADE, org TEXT NOT NULL, form_id TEXT NOT NULL, spec TEXT NOT NULL, date_from TEXT NOT NULL, date_to TEXT NOT NULL, interval INTEGER NOT NULL, revision TEXT NOT NULL, aggregate TEXT NOT NULL, last_sync REAL, error TEXT NOT NULL DEFAULT "", enabled INTEGER NOT NULL DEFAULT 1)')
    c.execute('CREATE TABLE IF NOT EXISTS audit_log(id INTEGER PRIMARY KEY,created REAL NOT NULL,action TEXT NOT NULL,detail TEXT NOT NULL)')

def data_v2(c):
    c.execute("""CREATE TABLE IF NOT EXISTS yandex_sync_jobs(
        event_id INTEGER PRIMARY KEY REFERENCES yandex_source(event_id) ON DELETE CASCADE,
        request_id TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('queued','running','success','failed')),
        requested REAL NOT NULL, finished REAL
    )""")


def access_v1(c):
    c.execute('CREATE TABLE IF NOT EXISTS access_settings(id INTEGER PRIMARY KEY CHECK(id=1),password_salt BLOB NOT NULL,password_hash TEXT NOT NULL,key_salt BLOB NOT NULL,required INTEGER NOT NULL)')
    c.execute('CREATE TABLE IF NOT EXISTS terminal_keys(id INTEGER PRIMARY KEY,label TEXT NOT NULL,digest TEXT UNIQUE NOT NULL,created REAL NOT NULL,enabled INTEGER NOT NULL DEFAULT 1)')
    c.execute('CREATE TABLE IF NOT EXISTS yandex_credentials(id INTEGER PRIMARY KEY CHECK(id=1),org TEXT NOT NULL,token TEXT NOT NULL,revision TEXT NOT NULL)')


def access_v2(c):
    c.execute('ALTER TABLE terminal_keys ADD COLUMN device_id TEXT')
    c.execute('UPDATE terminal_keys SET device_id=lower(hex(randomblob(16)))')
    c.execute('CREATE UNIQUE INDEX terminal_keys_device ON terminal_keys(device_id)')
    c.execute('CREATE TABLE access_sessions(token_hash BLOB PRIMARY KEY, expires REAL NOT NULL)')
    c.execute('CREATE INDEX access_sessions_expiry ON access_sessions(expires)')


DATA_MIGRATIONS = (data_v1, data_v2)
ACCESS_MIGRATIONS = (access_v1, access_v2)
