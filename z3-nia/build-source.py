#!/usr/bin/env python3
"""Rebuild the archived official Z3 source and install only under this directory."""
from pathlib import Path
import datetime
import json
import os
import subprocess
import sys
import time

root = Path(__file__).resolve().parent
logs = root / 'logs'
logs.mkdir(exist_ok=True)
os.environ['CMAKE_BUILD_PARALLEL_LEVEL'] = '12'
commands = [
    ('configure', [
        'cmake', '-S', str(root / 'source'), '-B', str(root / 'build'),
        '-G', 'Ninja', '-DCMAKE_BUILD_TYPE=Release',
        '-DCMAKE_INSTALL_PREFIX=' + str(root / 'install'),
        '-DCMAKE_INSTALL_LIBDIR=lib',
        '-DCMAKE_INSTALL_RPATH=$ORIGIN/../lib',
        '-DZ3_BUILD_LIBZ3_SHARED=ON',
        '-DZ3_ENABLE_EXAMPLE_TARGETS=ON',
        '-DZ3_BUILD_TEST_EXECUTABLES=OFF',
        '-DZ3_BUILD_EXECUTABLE=ON',
        '-DZ3_BUILD_PYTHON_BINDINGS=ON',
        '-DZ3_INSTALL_PYTHON_BINDINGS=ON',
        '-DCMAKE_INSTALL_PYTHON_PKG_DIR=lib/python3.12/site-packages',
        '-DZ3_BUILD_JAVA_BINDINGS=OFF',
        '-DZ3_BUILD_DOTNET_BINDINGS=OFF',
        '-DZ3_BUILD_DOCUMENTATION=OFF',
    ]),
    ('build', ['cmake', '--build', str(root / 'build'), '--parallel', '12']),
    ('examples-build', ['cmake', '--build', str(root / 'build'), '--target', 'c_example', 'cpp_example', '--parallel', '12']),
    ('install', ['cmake', '--install', str(root / 'build')]),
    ('python-install-links', ['python3', str(root / 'repair-python-install.py')]),
]
steps = []
for name, command in commands:
    print(name + ': ' + repr(command), flush=True)
    started = datetime.datetime.now(datetime.timezone.utc).isoformat()
    start = time.monotonic()
    with (logs / (name + '.log')).open('w') as log:
        log.write('COMMAND: ' + json.dumps(command) + '\n')
        log.flush()
        result = subprocess.run(command, cwd=root, stdout=log, stderr=subprocess.STDOUT)
        log.write('\nEXIT_CODE: ' + str(result.returncode) + '\n')
    steps.append({'name': name, 'command': command, 'started_at': started,
                  'duration_seconds': round(time.monotonic() - start, 3),
                  'exit_code': result.returncode, 'log': 'logs/' + name + '.log'})
    (root / 'build-info.json').write_text(json.dumps(steps, indent=2) + '\n')
    print(name + ' exit=' + str(result.returncode), flush=True)
    if result.returncode:
        print((logs / (name + '.log')).read_text()[-16000:], flush=True)
        sys.exit(result.returncode)
print('Source build and local installation finished.', flush=True)
