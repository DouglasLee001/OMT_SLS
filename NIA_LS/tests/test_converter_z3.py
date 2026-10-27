"""The converter must use one matching Z3 5.1.0 SDK and shared library."""

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


if __name__ == '__main__':
    unittest.main()
