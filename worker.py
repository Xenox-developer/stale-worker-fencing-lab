import json
import os
import sys
from store import Store

store = Store(sys.argv[1])
for line in sys.stdin:
    request = json.loads(line)
    value = getattr(store, request['op'])(*request['args'])
    print(json.dumps({'pid': os.getpid(), 'value': value}, ensure_ascii=False), flush=True)
