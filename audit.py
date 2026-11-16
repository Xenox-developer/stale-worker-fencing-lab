"""Recompute outcomes from protocol events and SQLite, without store/harness imports."""
import argparse
import hashlib
import itertools
import json
from pathlib import Path
import random
import sqlite3

ROOT = Path(__file__).resolve().parent


def check(root=ROOT, run_path='results/primary'):
    frozen = json.loads((root / 'frozen.json').read_text())
    for name, digest in frozen['sha256'].items():
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() == digest, name
    fixtures = json.loads((root / 'fixtures.json').read_text())
    by_id = {f['id']: f for f in fixtures}
    expected_order = [{'id': f'{p}-{m}-{s}', 'fixture': p, 'mode': m, 'scenario': s}
                      for p, m, s in itertools.product([f'p{i}' for i in range(8)],
                      ['lease_only', 'precheck', 'fenced'],
                      ['clean', 'handoff_before_check', 'handoff_after_check'])]
    random.Random(20261116).shuffle(expected_order)
    schedule = json.loads((root / 'schedule.json').read_text())
    assert schedule == expected_order
    out = root / run_path
    assert sorted(d.name for d in out.iterdir() if d.is_dir()) == sorted(c['id'] for c in schedule)
    run = json.loads((out / 'run.json').read_text())
    assert run['cells'] == 72
    assert run['finished_unix'] >= run['started_unix']
    assert run['frozen_sha256'] == hashlib.sha256((root / 'frozen.json').read_bytes()).hexdigest()
    controls = json.loads((out / 'scorer-controls.json').read_text())
    assert controls == dict(positive=True, negative=True, missing_event_rejected=True, malformed_response_rejected=True)
    summaries = {}
    audited = []
    for mode in ['lease_only', 'precheck', 'fenced']:
        summaries[mode] = dict(stale_commit=0, stale_final=0, replacement_success=0,
                               clean_success=0, trials=0, handoffs=0, clean=0,
                               stale_by_scenario=dict(handoff_before_check=0, handoff_after_check=0))
    for cell in schedule:
        directory = out / cell['id']
        assert not (directory / 'error.json').exists()
        raw = json.loads((directory / 'raw.json').read_text())
        assert raw['cell'] == cell
        workers = raw['workers']
        assert set(workers) == {'A', 'B'}
        assert workers['A']['pid'] != workers['B']['pid']
        for who in workers:
            assert workers[who]['exit_code'] == 0
            assert (directory / f'{who}.stderr').read_text() == ''
        f = by_id[cell['fixture']]
        mode, scenario = cell['mode'], cell['scenario']
        requests = [('A', 'acquire', ['A', 0, 10])]
        if scenario in ('clean', 'handoff_after_check'):
            requests += [('A', 'prepare', [mode, 'A', 1, 1])]
        if scenario != 'clean':
            requests += [('B', 'acquire', ['B', 11, 10]),
                         ('B', 'prepare', [mode, 'B', 2, 12]),
                         ('B', 'commit', [mode, 'B', 2, f['new'], True])]
        allowed_a = not (mode == 'precheck' and scenario == 'handoff_before_check')
        if scenario == 'handoff_before_check':
            requests += [('A', 'prepare', [mode, 'A', 1, 12])]
        requests += [('A', 'commit', [mode, 'A', 1, f['old'], allowed_a])]
        assert len(raw['events']) == len(requests)
        state = {'lease': [[1, 0, '', 0]], 'resource': [[1, 0, None]], 'writes': []}
        commits = {}
        for e, (who, op, args) in zip(raw['events'], requests):
            assert e['worker'] == who
            assert e['request'] == {'op': op, 'args': args}
            response = e['response']
            assert json.loads(e['raw_response']) == response
            assert response['pid'] == workers[who]['pid']
            value = response['value']
            if op == 'acquire':
                assert type(value) is int
                assert state['lease'][0][3] <= args[1]
                assert value == state['lease'][0][1] + 1
                state['lease'] = [[1, value, who, args[1] + args[2]]]
            elif op == 'prepare':
                assert type(value) is bool
                expected = True if mode != 'precheck' else (state['lease'][0][1:3] == [args[2], who] and args[3] < state['lease'][0][3])
                assert value == expected
            else:
                assert type(value) is bool
                expected = args[4] and (mode != 'fenced' or args[2] >= state['resource'][0][1])
                assert value == expected
                commits[who] = value
                if value:
                    payload = json.dumps(args[3], ensure_ascii=False, sort_keys=True, separators=(',', ':'))
                    state['resource'] = [[1, args[2], payload]]
                    state['writes'].append([len(state['writes']) + 1, who, args[2], payload])
            assert e['snapshot'] == state, (cell['id'], who, op, 'snapshot')
        with sqlite3.connect(directory / 'state.db') as db:
            disk = {name: [list(row) for row in db.execute(query)] for name, query in {
                'lease': 'SELECT id,generation,owner,expires FROM lease ORDER BY id',
                'resource': 'SELECT id,token,payload FROM resource ORDER BY id',
                'writes': 'SELECT seq,owner,token,payload FROM writes ORDER BY seq'}.items()}
            assert db.execute('PRAGMA integrity_check').fetchone() == ('ok',)
        assert disk == state == raw['final']
        handoff = scenario != 'clean'
        # Outcome calculation from observed history and final persisted payload.
        stale = handoff and any(row[1:3] == ['A', 1] for row in disk['writes'])
        stale_final = handoff and json.loads(disk['resource'][0][2]) == f['old']
        outcomes = dict(stale_commit=bool(stale), stale_final=bool(stale_final),
                        replacement_success=bool(handoff and commits['B']),
                        clean_success=bool(not handoff and commits['A']))
        assert outcomes == raw['outcomes']
        s = summaries[mode]
        for key, value in outcomes.items():
            s[key] += value
        s['trials'] += 1
        s['handoffs'] += handoff
        s['clean'] += not handoff
        if handoff:
            s['stale_by_scenario'][scenario] += stale
        audited.append(cell['id'])
    assert summaries == json.loads((out / 'summary.json').read_text())
    return {'passed': True, 'audited_trials': len(audited), 'frozen_files': len(frozen['sha256']), 'summary': summaries}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', default='results/primary')
    parser.add_argument('--output', default='results/audit.json')
    args = parser.parse_args()
    result = check(run_path=args.run)
    (ROOT / args.output).write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
