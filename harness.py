import argparse
from collections import defaultdict
import hashlib
import json
import os
from pathlib import Path
import select
import sqlite3
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent


def snapshot(path):
    with sqlite3.connect(path) as db:
        return {name: [list(row) for row in db.execute(query)] for name, query in {
            'lease': 'SELECT id,generation,owner,expires FROM lease ORDER BY id',
            'resource': 'SELECT id,token,payload FROM resource ORDER BY id',
            'writes': 'SELECT seq,owner,token,payload FROM writes ORDER BY seq'}.items()}


def score(trial):
    events = trial['events']
    a = [e for e in events if e['worker'] == 'A' and e['request']['op'] == 'commit']
    b = [e for e in events if e['worker'] == 'B' and e['request']['op'] == 'commit']
    if len(a) != 1 or len(b) != (trial['cell']['scenario'] != 'clean'):
        raise ValueError('Missing or duplicate commit event')
    if type(a[0]['response']['value']) is not bool or any(type(e['response']['value']) is not bool for e in b):
        raise ValueError('Invalid commit response')
    handoff = bool(b)
    return {'stale_commit': handoff and a[0]['response']['value'],
            'stale_final': handoff and trial['final']['resource'][0][1] == 1,
            'replacement_success': handoff and b[0]['response']['value'],
            'clean_success': not handoff and a[0]['response']['value']}


def scorer_controls():
    def trial(a, b, scenario='handoff_after_check'):
        return {'cell': {'scenario': scenario}, 'events': [
            {'worker': 'B', 'request': {'op': 'commit'}, 'response': {'value': b}},
            {'worker': 'A', 'request': {'op': 'commit'}, 'response': {'value': a}}],
            'final': {'resource': [[1, 1 if a else 2, '{}']]}}
    assert score(trial(True, True))['stale_commit'] is True
    assert score(trial(False, True))['stale_commit'] is False
    broken = trial(False, True)
    broken['events'].pop()
    try:
        score(broken)
    except ValueError:
        pass
    else:
        raise AssertionError('Missing-event control not detected')
    broken = trial('False', True)
    try:
        score(broken)
    except ValueError:
        pass
    else:
        raise AssertionError('Malformed-bool control not detected')
    return {'positive': True, 'negative': True, 'missing_event_rejected': True, 'malformed_response_rejected': True}


def trial(cell, fixture, directory):
    directory.mkdir()
    path = directory / 'state.db'
    processes = {}
    events = []
    handles = []
    def call(who, op, *args):
        process = processes[who]
        request = {'op': op, 'args': list(args)}
        process.stdin.write(json.dumps(request, ensure_ascii=False) + '\n')
        process.stdin.flush()
        if not select.select([process.stdout], [], [], 30)[0]:
            raise TimeoutError(f'{who} {op}')
        line = process.stdout.readline()
        response = json.loads(line)
        assert response['pid'] == process.pid
        events.append({'worker': who, 'request': request, 'raw_response': line,
                       'response': response, 'snapshot': snapshot(path)})
        return response['value']
    try:
        # Launch A first; its first response establishes schema before B starts.
        handle = (directory / 'A.stderr').open('w')
        handles.append(handle)
        processes['A'] = subprocess.Popen([sys.executable, '-B', str(ROOT / 'worker.py'), str(path)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=handle, text=True)
        a = call('A', 'acquire', 'A', 0, 10)
        handle = (directory / 'B.stderr').open('w')
        handles.append(handle)
        processes['B'] = subprocess.Popen([sys.executable, '-B', str(ROOT / 'worker.py'), str(path)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=handle, text=True)
        mode = cell['mode']
        scenario = cell['scenario']
        if scenario in ('clean', 'handoff_after_check'):
            allowed = call('A', 'prepare', mode, 'A', a, 1)
        if scenario != 'clean':
            b = call('B', 'acquire', 'B', 11, 10)
            allowed_b = call('B', 'prepare', mode, 'B', b, 12)
            call('B', 'commit', mode, 'B', b, fixture['new'], allowed_b)
        if scenario == 'handoff_before_check':
            allowed = call('A', 'prepare', mode, 'A', a, 12)
        call('A', 'commit', mode, 'A', a, fixture['old'], allowed)
        for process in processes.values():
            process.stdin.close()
            assert process.wait(timeout=30) == 0
        final = snapshot(path)
        result = {'cell': cell, 'events': events, 'final': final,
                  'workers': {name: {'pid': proc.pid, 'exit_code': proc.returncode} for name, proc in processes.items()}}
        assert processes['A'].pid != processes['B'].pid
        result['outcomes'] = score(result)
        (directory / 'raw.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
        return result
    except Exception as exc:
        (directory / 'error.json').write_text(json.dumps({'cell': cell, 'events': events, 'error': repr(exc)}, ensure_ascii=False, indent=2))
        raise
    finally:
        for process in processes.values():
            if process.poll() is None:
                process.kill()
                process.wait()
        for handle in handles:
            handle.close()


def aggregate(trials):
    out = {}
    for mode in ['lease_only', 'precheck', 'fenced']:
        cells = [t for t in trials if t['cell']['mode'] == mode]
        out[mode] = {key: sum(t['outcomes'][key] for t in cells) for key in ['stale_commit', 'stale_final', 'replacement_success', 'clean_success']}
        out[mode].update(trials=len(cells), handoffs=sum(t['cell']['scenario'] != 'clean' for t in cells), clean=sum(t['cell']['scenario'] == 'clean' for t in cells))
        out[mode]['stale_by_scenario'] = {s: sum(t['outcomes']['stale_commit'] for t in cells if t['cell']['scenario'] == s) for s in ['handoff_before_check', 'handoff_after_check']}
    return out


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', default='results/primary')
    args = parser.parse_args()
    out = ROOT / args.output
    if out.exists():
        raise SystemExit('Refusing to overwrite a measured run')
    frozen = json.loads((ROOT / 'frozen.json').read_text())
    for name, digest in frozen['sha256'].items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest, name
    controls = scorer_controls()
    out.mkdir(parents=True)
    (out / 'scorer-controls.json').write_text(json.dumps(controls, indent=2) + '\n')
    fixtures = {f['id']: f for f in json.loads((ROOT / 'fixtures.json').read_text())}
    schedule = json.loads((ROOT / 'schedule.json').read_text())
    start = time.time()
    results = [trial(c, fixtures[c['fixture']], out / c['id']) for c in schedule]
    summary = aggregate(results)
    (out / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    (out / 'run.json').write_text(json.dumps({'started_unix': start, 'finished_unix': time.time(), 'host_pid': os.getpid(), 'cells': len(results), 'frozen_sha256': hashlib.sha256((ROOT / 'frozen.json').read_bytes()).hexdigest()}, indent=2) + '\n')
    print(json.dumps(summary, indent=2))
