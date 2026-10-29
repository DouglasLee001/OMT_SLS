#!/usr/bin/env python3
"""Smoke-check this source-built Z3 and official API examples."""
from pathlib import Path
import json
import os
import re
import subprocess
import sys
import time

root = Path(__file__).resolve().parent
logs = root / 'logs'
exe = root / 'install' / 'bin' / 'z3'
env = dict(os.environ)
env['PYTHONPATH'] = str(root / 'install/lib/python3.12/site-packages')
env['Z3_LIBRARY_PATH'] = str(root / 'install/lib')

binding_code = '''
from pathlib import Path
import sys
import z3
root = Path(sys.argv[1])
assert Path(z3.__file__).resolve().is_relative_to(root / 'install'), z3.__file__
assert z3.get_version()[:3] == (5, 1, 0), z3.get_version()
loaded_libs = {Path(line.split(maxsplit=5)[-1]).resolve() for line in Path('/proc/self/maps').read_text().splitlines() if '/libz3.so' in line}
assert loaded_libs and all(p.is_relative_to(root / 'install') for p in loaded_libs), loaded_libs
x = z3.Int('x')
opt = z3.Optimize()
opt.add(x >= 0, x <= 10)
h = opt.maximize(x)
assert opt.check() == z3.sat
assert opt.model().eval(x).as_long() == 10
assert h.lower().as_long() == 10
assert h.upper().as_long() == 10
print('integer optimization: x=10, lower=upper=10')
a, b = z3.Bools('a b')
opt = z3.Optimize()
opt.add(z3.Not(z3.And(a, b)))
h = opt.add_soft(a, weight=2)
opt.add_soft(b, weight=1)
assert opt.check() == z3.sat
model = opt.model()
assert z3.is_true(model.eval(a)) and z3.is_false(model.eval(b)), model
assert str(h.lower()) == '1' and str(h.upper()) == '1'
print('weighted MaxSAT: a=true, b=false, cost=1')
print('binding:', z3.__file__)
print('library:', sorted(str(p) for p in loaded_libs))
print('version:', z3.get_full_version())
'''

cases = [
    ('version', [str(exe), '--version'], 10,
     lambda output: 'Z3 version 5.1.0' in output),
    ('linear-integer-opt', [str(exe), str(root / 'tests/linear-integer-opt.smt2')], 20,
     lambda output: bool(re.search(r'^sat\s', output)) and bool(re.search(r'\(x\s+10\)', output))),
    ('weighted-maxsat', [str(exe), str(root / 'tests/weighted-maxsat.smt2')], 20,
     lambda output: bool(re.search(r'^sat\s', output))
       and bool(re.search(r'define-fun a\s*\(\)\s*Bool\s*true', output))
       and bool(re.search(r'define-fun b\s*\(\)\s*Bool\s*false', output))),
    ('python-binding', ['python3', '-c', binding_code, str(root)], 20,
     lambda output: 'weighted MaxSAT: a=true, b=false, cost=1' in output),
    ('official-c-example', [str(root / 'build/examples/c_example_build_dir/c_example')], 60,
     lambda output: '5.1.0' in output),
    ('official-cpp-example', [str(root / 'build/examples/cpp_example_build_dir/cpp_example')], 60,
     lambda output: 'done\n' in output and 'unexpected error:' not in output),
]
results = []
for name, command, timeout, validator in cases:
    start = time.monotonic()
    try:
        result = subprocess.run(command, cwd=root / 'tests', env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, timeout=timeout)
        output, code = result.stdout, result.returncode
    except subprocess.TimeoutExpired as exc:
        output = (exc.stdout or b'').decode() if isinstance(exc.stdout, bytes) else (exc.stdout or '')
        code = 124
    passed = code == 0 and validator(output)
    log = logs / ('verify-' + name + '.log')
    log.write_text('COMMAND: ' + json.dumps(command) + '\n' + output +
                   '\nEXIT_CODE: ' + str(code) + '\nVALIDATED: ' + str(passed) + '\n')
    entry = {'name': name, 'exit_code': code, 'passed': passed,
             'duration_seconds': round(time.monotonic() - start, 3),
             'log': str(log.relative_to(root))}
    results.append(entry)
    print(json.dumps(entry), flush=True)
    if name in ('version', 'linear-integer-opt', 'weighted-maxsat', 'python-binding') or not passed:
        print(output, flush=True)
(root / 'verification-results.json').write_text(json.dumps(results, indent=2) + '\n')
sys.exit(0 if all(result['passed'] for result in results) else 1)
