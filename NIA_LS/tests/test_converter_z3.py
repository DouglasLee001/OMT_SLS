"""One Z3 5.1 runtime, with converter-local nonlinear rewrite settings."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
CONVERTER = ROOT / 'NIA_LS/converter/omt_to_ls.py'
Z3_ROOT = ROOT / 'competitor/z3-5.1.0'


class ConverterZ3Tests(unittest.TestCase):
    def run_loader(self, converter, environment=None):
        code = '''
import importlib.util, json, os, pathlib, sys
loads = []
sys.addaudithook(lambda event, args: loads.append(args[0])
                if event == 'ctypes.dlopen' and args[0] and 'libz3' in args[0] else None)
before = {key: os.environ.get(key) for key in ('Z3_LIBRARY_PATH', 'PATH', 'PYTHONPATH')}
spec = importlib.util.spec_from_file_location('omt_to_ls', sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
try:
    z3 = module.load_z3()
    print(json.dumps({'version': z3.get_version_string(), 'module': z3.__file__,
                      'loads': loads, 'reused': module.load_z3() is z3,
                      'environment_preserved': all(os.environ.get(k) == v for k, v in before.items())}))
except module.ConversionError as error:
    print(str(error), file=sys.stderr)
    sys.exit(2)
'''
        return subprocess.run([sys.executable, '-B', '-c', code, str(converter)],
                              text=True, capture_output=True, timeout=30,
                              env=environment)

    def test_nonlinear_product_equality_keeps_original_polynomial_encoding(self):
        source = '''(set-logic QF_NIA)
(declare-const x Int)
(declare-const y Int)
(assert (<= (- 2) x))
(assert (<= x 2))
(assert (<= (- 2) y))
(assert (<= y 2))
(assert (= (* x y) 0))
(minimize (+ x y))
(check-sat)
'''
        code = '''
import importlib.util, sys
spec = importlib.util.spec_from_file_location('omt_to_ls', sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
sys.stdout.write(module.convert_text(sys.stdin.read()))
'''
        result = subprocess.run([sys.executable, '-B', '-c', code, str(CONVERTER)],
                                input=source, text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        encoding = result.stdout.split('VARIABLES ', 1)[1]
        self.assertEqual(encoding, '''2
0 I 78
1 I 79
LITERALS 5
1 L -2 1
-1 1 0 1
2 L -2 1
1 1 0 1
3 L -2 1
-1 1 1 1
4 L -2 1
1 1 1 1
5 E 0 1
1 2 0 1 1 1
CLAUSES 5
1 1
1 2
1 3
1 4
1 5
OBJECTIVE 0 2
1 1 0 1
1 1 1 1
END
''')

    def test_live_converter_loads_competitor_5_1_sdk_and_library(self):
        result = self.run_loader(CONVERTER)
        self.assertEqual(result.returncode, 0, result.stderr)
        loaded = json.loads(result.stdout)
        self.assertEqual(loaded['version'], '5.1.0')
        self.assertEqual(Path(loaded['module']).parent, Z3_ROOT / 'build/python/z3')
        self.assertEqual(len(loaded['loads']), 1)
        self.assertEqual(Path(loaded['loads'][0]).parent, Z3_ROOT / 'build')
        self.assertEqual(Path(loaded['loads'][0]).resolve(), (Z3_ROOT / 'build/libz3.so').resolve())
        self.assertTrue(loaded['reused'])

    def test_adjacent_bundle_wins_over_live_and_environment_libraries(self):
        with tempfile.TemporaryDirectory(prefix='nia-converter-z3-') as directory:
            folder = Path(directory)
            converter = folder / 'omt_to_ls.py'
            shutil.copyfile(CONVERTER, converter)
            bundle = folder / 'z3-5.1.0/build'
            shutil.copytree(Z3_ROOT / 'build/python/z3', bundle / 'python/z3',
                            ignore=shutil.ignore_patterns('__pycache__'))
            (bundle / 'libz3.so').symlink_to((Z3_ROOT / 'build/libz3.so').resolve())
            competing = folder / 'competing'
            competing.mkdir()
            (competing / 'libz3.so.5.1').symlink_to((Z3_ROOT / 'build/libz3.so').resolve())
            environment = dict(os.environ, Z3_LIBRARY_PATH=str(competing),
                               PYTHONPATH=str(competing), PATH=str(competing))
            result = self.run_loader(converter, environment)
            self.assertEqual(result.returncode, 0, result.stderr)
            loaded = json.loads(result.stdout)
            self.assertEqual(loaded['version'], '5.1.0')
            self.assertEqual(Path(loaded['module']).parent, bundle / 'python/z3')
            self.assertEqual(loaded['loads'], [str(bundle / 'libz3.so')])
            self.assertTrue(loaded['environment_preserved'])

    def test_incomplete_adjacent_bundle_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix='nia-converter-z3-') as directory:
            folder = Path(directory)
            converter = folder / 'omt_to_ls.py'
            shutil.copyfile(CONVERTER, converter)
            (folder / 'z3-5.1.0').mkdir()
            result = self.run_loader(converter)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn(str(folder / 'z3-5.1.0'), result.stderr)
            self.assertIn('5.1.0 SDK is missing', result.stderr)

    def test_legacy_adjacent_bundle_is_not_a_fallback(self):
        with tempfile.TemporaryDirectory(prefix='nia-converter-z3-') as directory:
            folder = Path(directory)
            converter = folder / 'NIA_LS/converter/omt_to_ls.py'
            converter.parent.mkdir(parents=True)
            shutil.copyfile(CONVERTER, converter)
            legacy = converter.parent / 'z3-4.12.3/build'
            (legacy / 'python/z3').mkdir(parents=True)
            (legacy / 'python/z3/__init__.py').write_text(
                'def get_version(): return (4, 12, 3, 0)\n'
                'def get_version_string(): return "4.12.3"\n')
            (legacy / 'libz3.so').write_bytes(b'legacy library')
            result = self.run_loader(converter)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn(str(folder / 'competitor/z3-5.1.0'), result.stderr)
            self.assertIn('5.1.0 SDK is missing', result.stderr)

    def test_sdk_reporting_a_different_version_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix='nia-converter-z3-') as directory:
            folder = Path(directory)
            converter = folder / 'omt_to_ls.py'
            shutil.copyfile(CONVERTER, converter)
            bundle = folder / 'z3-5.1.0/build'
            package = bundle / 'python/z3'
            package.mkdir(parents=True)
            (package / '__init__.py').write_text(
                'def get_version(): return (4, 12, 3, 0)\n'
                'def get_version_string(): return "4.12.3"\n')
            (bundle / 'libz3.so').write_bytes(b'fixture')
            result = self.run_loader(converter)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn('expected Z3 5.1.0', result.stderr)
            self.assertIn('4.12.3', result.stderr)

    def test_conversions_preserve_default_factoring_in_the_same_process(self):
        code = '''
import importlib.util, json, sys
spec = importlib.util.spec_from_file_location('omt_to_ls', sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
z3 = module.load_z3()
x, y = z3.Ints('x y')
product_equality = x*y == 0
before = z3.simplify(product_equality)
assert z3.is_or(before), before
goal = z3.Goal()
goal.add(product_equality)
local = z3.With(z3.Tactic('simplify'), factor_nonlinear=False)(goal)[0]
assert len(local) == 1 and z3.is_eq(local[0]), local
source = '(declare-const x Int)(declare-const y Int)' \
         '(assert (= (* x y) 0))(minimize (+ x y))(check-sat)'
def forbidden(*args, **kwargs):
    raise AssertionError('conversion must not change global settings or solve')
z3.set_param = forbidden
z3.Context.set = forbidden
z3.Solver.check = forbidden
z3.Optimize.check = forbidden
outputs = []
for _ in range(2):
    outputs.append(module.convert_text(source))
    after = z3.simplify(product_equality)
    assert before.eq(after), (before, after)
assert outputs[0] == outputs[1], outputs
assert '1 E 0 1\\n1 2 0 1 1 1\\nCLAUSES 1\\n1 1\\n' in outputs[0], outputs[0]
print(json.dumps({'version': z3.get_version_string(), 'default': str(after)}))
'''
        result = subprocess.run([sys.executable, '-B', '-c', code, str(CONVERTER)],
                                text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['version'], '5.1.0')

    def test_nonlinear_atoms_survive_all_preprocessing_modes(self):
        # Product aliases exercise solve-eqs' own arithmetic rewriter. Boolean
        # nesting also sends the atoms through the CNF tactic's fallback path.
        code = '''
import importlib.util, json, sys
spec = importlib.util.spec_from_file_location('omt_to_ls', sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
outputs = []
for comparison in ('=', '<='):
    for alias in (False, True):
        for preprocess, keep in ((False, False), (True, False), (True, True)):
            source = '(declare-const x Int)(declare-const y Int)(declare-const p Int)' \
                     '(declare-const b Bool)(declare-const c Bool)'
            expression = '(* x y)'
            if alias:
                source += '(assert (= p (* x y)))'
                expression = 'p'
            source += '(assert (or (and b c) (' + comparison + ' ' + expression + ' 0)))' \
                      '(minimize (+ x y))(check-sat)'
            outputs.append(module.convert_text(source, preprocess=preprocess,
                                               keep_definitions=keep))
print(json.dumps(outputs))
'''
        result = subprocess.run([sys.executable, '-B', '-c', code, str(CONVERTER)],
                                text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        for encoding in json.loads(result.stdout):
            with self.subTest(encoding=encoding):
                # Every mode retains a degree-two product atom. Factoring
                # instead introduces separate linear tests of x and y.
                self.assertRegex(encoding, r'(?m)^-?1 2 \d+ 1 \d+ 1$')


if __name__ == '__main__':
    unittest.main()
