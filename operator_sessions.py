"""Opaque, revocable administrator sessions stored in the access database."""

import hashlib
import re
import secrets
import time

from fastapi import HTTPException


COOKIE = 'votemera_session'
LIFETIME = 2 * 60 * 60
COOKIE_PATH = '/api/admin'


def token_hash(token):
    if not re.fullmatch(r'[A-Za-z0-9_-]{43}', token):
        return None
    return hashlib.sha256(token.encode('ascii')).digest()


def issue(connection):
    token = secrets.token_urlsafe(32)
    now = time.time()
    connection.execute('DELETE FROM access_sessions WHERE expires<=?', (now,))
    connection.execute('INSERT INTO access_sessions VALUES(?,?)', (token_hash(token), now + LIFETIME))
    return token


def verify(connection, token):
    return connection.execute(
        'SELECT 1 FROM access_sessions WHERE token_hash=? AND expires>?', (token_hash(token), time.time())
    ).fetchone() is not None


def check_origin(request):
    origin = request.headers.get('origin')
    expected = f'{request.url.scheme}://{request.url.netloc}'
    if origin is not None and origin != expected:
        raise HTTPException(403, 'Запрос с другого сайта отклонён')


def set_cookie(response, request, token):
    response.set_cookie(
        COOKIE, token, max_age=LIFETIME, path=COOKIE_PATH,
        secure=request.url.scheme == 'https', httponly=True, samesite='strict',
    )


def clear_cookie(response, request):
    response.delete_cookie(COOKIE, path=COOKIE_PATH, secure=request.url.scheme == 'https',
                           httponly=True, samesite='strict')
