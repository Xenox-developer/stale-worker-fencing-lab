import itertools
import json
from pathlib import Path
import random

ROOT = Path(__file__).resolve().parent
FIXTURES = [
    {'id': f'p{i}', 'old': {'revision': i, 'status': 'old', 'body': text},
     'new': {'revision': i + 100, 'status': 'new', 'body': text + ' updated'}}
    for i, text in enumerate(['plain', 'with spaces', 'Unicode: café', 'JSON: {}',
                              'line\nbreak', 'quote: "', 'empty-array: []', 'path/a/b'])
]
MODES = ['lease_only', 'precheck', 'fenced']
SCENARIOS = ['clean', 'handoff_before_check', 'handoff_after_check']
CELLS = [{'id': f'{p}-{m}-{s}', 'fixture': p, 'mode': m, 'scenario': s}
         for p, m, s in itertools.product([f['id'] for f in FIXTURES], MODES, SCENARIOS)]
random.Random(20261116).shuffle(CELLS)
if __name__ == '__main__':
    for name, value in [('fixtures.json', FIXTURES), ('schedule.json', CELLS)]:
        (ROOT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
