"""Validate and record ballots within the caller's SQLite write transaction."""

import hashlib
import json

from fastapi import HTTPException

from poll_results import external_source, questions


def deadline(connection):
    return connection.execute('SELECT deadline FROM runtime_config WHERE id=1').fetchone()[0]


def is_open(settings, end, now):
    return bool(settings['opened'] and (end is None or end > now))


def terminal_locked(config, event_id):
    return bool(config and (config['locked'] or config['event_id'] not in (None, event_id)))


def active_questions(connection, vote, terminal, now):
    settings = connection.execute('SELECT * FROM settings WHERE id=1').fetchone()
    event_id = settings['active_event']
    if external_source(connection, event_id):
        raise HTTPException(409, 'Заполните Яндекс Форму')
    config = connection.execute('SELECT locked,event_id FROM terminal_config WHERE id=?', (terminal,)).fetchone()
    if terminal_locked(config, event_id):
        raise HTTPException(423, 'Терминал заблокирован или назначен другому мероприятию')
    if not is_open(settings, deadline(connection), now) or (settings['active_round'], settings['epoch']) != (vote.round, vote.epoch):
        raise HTTPException(409, 'Этап изменился или закрыт. Начните заново.')
    event = connection.execute('SELECT * FROM events WHERE id=?', (event_id,)).fetchone()
    return questions(event) if event else [], json.loads(settings['data'])


def check_cooldown(connection, terminal, now, cooldown):
    last = connection.execute('SELECT max(created) FROM votes WHERE terminal=?', (terminal,)).fetchone()[0]
    if last is not None and now - last < cooldown / 1000:
        raise HTTPException(429, 'Терминал ещё не готов')


def valid_ballot(questions, choices):
    if len(questions) != len(choices):
        return False
    for question, choice in zip(questions, choices):
        rule = question.get('show_if')
        visible = not rule or choices[rule['question']] == rule['choice']
        if visible and (choice is None or not 0 <= choice < len(question['options'])):
            return False
        if not visible and choice is not None:
            return False
    return True


def record_vote(connection, vote, terminal, now, default_cooldown):
    old = connection.execute('SELECT * FROM votes WHERE nonce=?', (vote.nonce,)).fetchone()
    if old:
        if (old['round_id'], old['choice'], old['terminal']) != (vote.round, vote.choice, terminal):
            raise HTTPException(409, 'Конфликт идентификатора')
        return {'ok': True, 'duplicate': True}
    qs, settings = active_questions(connection, vote, terminal, now)
    if len(qs) > 1:
        raise HTTPException(409, 'Для нескольких вопросов отправьте анкету целиком')
    if not qs or vote.choice >= len(qs[0]['options']):
        raise HTTPException(422, 'Неизвестный вариант')
    check_cooldown(connection, terminal, now, settings.get('cooldown', default_cooldown))
    connection.execute('INSERT INTO votes(round_id,choice,nonce,terminal,created,ballot_id) VALUES(?,?,?,?,?,?)',
                       (vote.round, vote.choice, vote.nonce, terminal, now, vote.nonce))
    return {'ok': True, 'duplicate': False}


def record_ballot(connection, ballot, terminal, now, default_cooldown):
    # The first answer retains the request nonce. Subsequent answers use distinct deterministic nonces.
    nonces = [ballot.nonce] + [
        hashlib.sha256(f'ballot:{ballot.nonce}:{i}'.encode()).hexdigest()[:32]
        for i in range(1, len(ballot.choices))
    ]
    if connection.execute('SELECT 1 FROM votes WHERE nonce=?', (ballot.nonce,)).fetchone():
        rows = connection.execute('SELECT * FROM votes WHERE round_id=? AND ballot_id=?', (ballot.round, ballot.nonce))
        expected = {(i, n, nonces[i], terminal) for i, n in enumerate(ballot.choices) if n is not None}
        if {(r['question_index'], r['choice'], r['nonce'], r['terminal']) for r in rows} != expected:
            raise HTTPException(409, 'Конфликт идентификатора')
        event = connection.execute('SELECT events.* FROM events JOIN rounds ON rounds.event_id=events.id WHERE rounds.id=?',
                                   (ballot.round,)).fetchone()
        if not event or not valid_ballot(questions(event), ballot.choices):
            raise HTTPException(409, 'Конфликт анкеты')
        return {'ok': True, 'duplicate': True}

    qs, settings = active_questions(connection, ballot, terminal, now)
    if not valid_ballot(qs, ballot.choices):
        raise HTTPException(422, 'Неверные ответы или пропуски анкеты')
    check_cooldown(connection, terminal, now, settings.get('cooldown', default_cooldown))
    if any(connection.execute('SELECT 1 FROM votes WHERE nonce=?', (nonce,)).fetchone() for nonce in nonces):
        raise HTTPException(409, 'Конфликт идентификатора')
    connection.executemany(
        'INSERT INTO votes(round_id,choice,nonce,terminal,created,question_index,ballot_id) VALUES(?,?,?,?,?,?,?)',
        [(ballot.round, choice, nonces[i], terminal, now, i, ballot.nonce)
         for i, choice in enumerate(ballot.choices) if choice is not None],
    )
    return {'ok': True, 'duplicate': False}
