#!/usr/bin/env python3
"""Root-only online SQLite backup (data + access + configuration)."""
import json,os,sqlite3,tempfile,shutil,sys,time
from pathlib import Path
from datetime import datetime,timezone
from contextlib import closing
DB=Path('/var/lib/votemera/votemera.db')
BASE=Path('/var/backups/votemera/daily')
STATUS=DB.parent/'backup-status.json'

def settings():
    with closing(sqlite3.connect(f'file:{DB}?mode=ro',uri=True)) as c:
        return json.loads(c.execute('SELECT data FROM settings WHERE id=1').fetchone()[0])

def publish_status(data):
    # Do not follow untrusted paths; atomic replace is confined to the data directory.
    fd,tmp=tempfile.mkstemp(prefix='.backup-status-',dir=STATUS.parent)
    try:
        with os.fdopen(fd,'w') as f:json.dump(data,f)
        os.chmod(tmp,0o644);os.replace(tmp,STATUS)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)

def copy_db(source,dest):
    with closing(sqlite3.connect(f'file:{source}?mode=ro',uri=True,timeout=30)) as src, closing(sqlite3.connect(dest)) as target:
        src.backup(target)
        if target.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise RuntimeError('Backup integrity check failed')
    os.chmod(dest,0o600)

def main():
    if os.geteuid()!=0:raise SystemExit('Run as root')
    os.umask(0o077);cfg=settings();retention=cfg.get('backup_retention',14)
    retention=max(1,min(90,int(retention)))
    if not cfg.get('backup_enabled',True):publish_status({'state':'disabled','time':time.time()});return
    BASE.mkdir(mode=0o700,parents=True,exist_ok=True)
    name=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    tmp=Path(tempfile.mkdtemp(prefix='.pending-',dir=BASE))
    try:
        copy_db(DB,tmp/'votemera.db')
        copy_db(DB.with_name(DB.name+'.access'),tmp/'votemera.db.access')
        shutil.copyfile('/etc/votemera/server.env',tmp/'server.env')
        os.chmod(tmp/'server.env',0o600)
        target=BASE/name
        if target.exists():raise RuntimeError('Duplicate backup timestamp')
        tmp.rename(target)
        candidates=sorted((p for p in BASE.iterdir() if p.is_dir() and not p.is_symlink() and len(p.name)==16 and p.name.endswith('Z')),reverse=True)
        for p in candidates[retention:]:shutil.rmtree(p)
        publish_status({'state':'ok','time':time.time(),'copies':len(candidates[:retention]),'retention':retention})
    finally:
        if tmp.exists():shutil.rmtree(tmp)

if __name__=='__main__':
    try: main()
    except Exception:
        try: publish_status({'state':'failed','time':time.time()})
        except OSError:pass
        raise
