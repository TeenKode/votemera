"""SQLite queries for poll results and event configuration."""

import json


def questions(e):
    return json.loads(e['questions']) if e['questions'] else [dict(question=e['question'],options=json.loads(e['options']))]


def external_source(c,eid):
    return c.execute('SELECT * FROM yandex_source WHERE event_id=?',(eid,)).fetchone()


def external_round(c,rid):
    return c.execute('SELECT y.* FROM yandex_source y JOIN rounds r ON r.event_id=y.event_id WHERE r.id=?',(rid,)).fetchone()


def results(c,rid,qs):
    ext=external_round(c,rid)
    if ext: return json.loads(ext['aggregate'])['results']
    items=[dict(counts=[0]*len(q['options'])) for q in qs]
    for v in c.execute('SELECT question_index,choice,count(*) n FROM votes WHERE round_id=? GROUP BY question_index,choice',(rid,)):
        items[v['question_index']]['counts'][v['choice']]=v['n']
    for x in items: x['total']=sum(x['counts'])
    return items


def analysis_config(c,eid,qs):
    if len(qs)<2 or external_source(c,eid): return None
    row=c.execute('SELECT first_q,second_q,chart FROM analysis_config WHERE event_id=?',(eid,)).fetchone()
    if row and row['first_q']!=row['second_q'] and max(row['first_q'],row['second_q'])<len(qs):
        return dict(first=row['first_q'],second=row['second_q'],chart=row['chart'])
    return dict(first=0,second=1,chart='stack')


def cross_results(c,rid,qs,first,second):
    matrix=[[0]*len(qs[second]['options']) for _ in qs[first]['options']]
    for v in c.execute('''SELECT a.choice x,b.choice y,count(*) n FROM votes a JOIN votes b
        ON a.round_id=b.round_id AND a.ballot_id=b.ballot_id
        WHERE a.round_id=? AND a.question_index=? AND b.question_index=? AND a.ballot_id IS NOT NULL
        GROUP BY a.choice,b.choice''',(rid,first,second)):
        matrix[v['x']][v['y']]=v['n']
    totals=[sum(row) for row in matrix]
    submitted=c.execute('SELECT count(*) FROM votes WHERE round_id=? AND question_index=0',(rid,)).fetchone()[0]
    return dict(matrix=matrix,totals=totals,total=sum(totals),unpaired=max(0,submitted-sum(totals)))


def presentation_config(c,eid,qs):
    row=c.execute('SELECT data FROM presentation_config WHERE event_id=?',(eid,)).fetchone()
    return json.loads(row['data']) if row else dict(order=list(range(len(qs))),slides=[],seconds=15)


def meta(c,eid):
    row=c.execute('SELECT archived,template,brand,accent,logo FROM event_meta WHERE event_id=?',(eid,)).fetchone()
    return dict(row) if row else dict(archived=0,template=0,brand='',accent='',logo='')
