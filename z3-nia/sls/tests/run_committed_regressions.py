#!/usr/bin/env python3
"""Run unchanged committed search/proof tests against either executable.

The tiny test adapter dispatches directly into the embedded native-IR entry.
It does not replace the C++ integration or perform any conversion itself.
"""
import argparse
import hashlib
import importlib
import json
import os
from pathlib import Path
import shlex
import tempfile
import unittest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--variant', choices=('baseline', 'embedded'), default='embedded')
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    if args.report:
        args.report = args.report.resolve()
    tree = Path(__file__).resolve().parents[2]
    reference = tree / 'reference'
    binary = (reference / 'NIA_LS/src/nia_ls' if args.variant == 'baseline' else tree / 'build/z3')
    if not binary.is_file():
        parser.error('build the executable before running these tests: ' + str(binary))
    import sys
    sys.path.insert(0, str(reference / 'NIA_LS/tests'))
    sys.path.insert(0, str(reference))
    os.chdir(reference)
    with tempfile.TemporaryDirectory(prefix='nia-committed-tests-') as temporary:
        native = binary
        if args.variant == 'embedded':
            native = Path(temporary) / 'native-entry'
            native.write_text('#!/bin/sh\nexec ' + shlex.quote(str(binary)) + ' --nia-ls --native-input "$@"\n')
            native.chmod(0o755)
        base = importlib.import_module('test_omt_import')
        base.NATIVE = native
        modules = ('test_first_solution', 'test_optimization', 'test_unbounded_optimization',
                   'test_optimality_optimization')
        suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromModule(importlib.import_module(name))
                                   for name in modules)
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        record = dict(variant=args.variant, executable=str(binary),
                      executable_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
                      tests_run=result.testsRun, successful=result.wasSuccessful(),
                      failures=[str(test) for test, _ in result.failures],
                      errors=[str(test) for test, _ in result.errors],
                      skipped=[str(test) for test, _ in result.skipped])
        if args.report:
            args.report.resolve().write_text(json.dumps(record, indent=2) + '\n')
        print(json.dumps(record, indent=2))
        return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    raise SystemExit(main())
