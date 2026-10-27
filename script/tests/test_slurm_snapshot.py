"""Frozen Slurm runtime contracts; no Slurm allocation or compiler required."""

import hashlib
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]


def load_snapshot():
    path = ROOT / 'script/slurm_snapshot.py'
    assert path.is_file(), 'Slurm runtime snapshot is not implemented'
    spec = importlib.util.spec_from_file_location('slurm_snapshot', path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class SlurmSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.module = load_snapshot()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / 'repo'
        self.run = self.root / 'run'
        self.solver = self.write('NIA_LS/src/nia_ls', b'native-v1\n', executable=True)
        self.converter = self.write('NIA_LS/converter/omt_to_ls.py', b'# converter-v1\n')
        self.baseline = self.write('result/competitor.csv', b'filename,value\nold,7\n')
        self.write('result/original/original.csv', b'filename\noriginal.smt2\n')
        self.write('result/new/new.csv', b'filename\nnew.smt2\n')
        self.write('script/run_optimize_slurm.py', b'# launcher-v1\n')
        self.write('script/run_optimize.py', b'# runner-v1\n')
        self.write('script/__pycache__/ignored.pyc', b'ignored')
        self.write('new_benchmark/type/instance/new.smt2', b'(check-sat)\n')
        self.sdk = self.write('competitor/z3-5.1.0/build/python/z3/__init__.py', b'# sdk-v1\n')
        self.write('competitor/z3-5.1.0/build/python/z3/z3core.py', b'# core-v1\n')
        self.write('competitor/z3-5.1.0/build/python/z3/__pycache__/ignored.pyc', b'ignored')
        self.z3_library = self.write('competitor/z3-5.1.0/build/libz3.so', b'z3-5.1-library-v1\n')
        self.write('competitor/z3-4.12.3-converter/build/python/z3/__init__.py', b'# legacy sdk\n')
        self.write('competitor/z3-4.12.3-converter/build/libz3.so', b'z3-4.12.3-library-v1\n')
        self.libraries = {}
        for name in ('libz3.so.5.1', 'libgmpxx.so.4', 'libgmp.so.10', 'libstdc++.so.6', 'libgcc_s.so.1'):
            path = self.root / 'system' / name
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(name.encode() + b'-v1\n')
            self.libraries[name] = path

    def write(self, relative, content, executable=False):
        path = self.repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        if executable:
            path.chmod(0o755)
        return path

    def create(self, run=None):
        with mock.patch.object(self.module, 'native_libraries', return_value=self.libraries):
            return self.module.create_snapshot(run or self.run, self.repo,
                                               self.solver, self.converter, self.baseline)

    def test_snapshot_survives_changes_to_live_solver_code_sdk_and_baseline(self):
        snapshot = self.create()
        before = dict(snapshot['files'])
        for path in (self.solver, self.converter, self.sdk, self.z3_library,
                     self.baseline, self.repo / 'script/run_optimize.py',
                     self.repo / 'result/original/original.csv',
                     *self.libraries.values()):
            path.write_bytes(b'changed-live-file\n')
        self.module.check_snapshot(self.run, snapshot)
        self.assertEqual(snapshot['files'], before)
        self.assertEqual(Path(snapshot['solver']).read_bytes(), b'native-v1\n')
        self.assertEqual(Path(snapshot['baseline']).read_bytes(), b'filename,value\nold,7\n')
        self.assertTrue(os.access(snapshot['solver'], os.X_OK))
        self.assertEqual(snapshot['solver_sha256'], hashlib.sha256(b'native-v1\n').hexdigest())
        self.assertEqual(snapshot['converter_sha256'], hashlib.sha256(b'# converter-v1\n').hexdigest())

    def test_sdk_layout_real_library_copies_and_shared_benchmark_link(self):
        target = self.root / 'real-z3-5.1.so'
        self.z3_library.rename(target)
        self.z3_library.symlink_to(target)
        snapshot = self.create()
        converter = Path(snapshot['converter'])
        sdk = converter.parent / 'z3-5.1.0/build'
        self.assertTrue(sdk.is_dir(), 'frozen converter must contain its Z3 5.1 SDK')
        self.assertEqual((sdk / 'python/z3/z3core.py').read_bytes(), b'# core-v1\n')
        self.assertEqual((sdk / 'libz3.so').read_bytes(), b'z3-5.1-library-v1\n')
        self.assertFalse((sdk / 'libz3.so').is_symlink())
        self.assertFalse((converter.parent / 'z3-4.12.3').exists())
        native_z3 = Path(snapshot['library_dir']) / 'libz3.so.5.1'
        self.assertEqual(native_z3.read_bytes(), b'libz3.so.5.1-v1\n')
        self.assertEqual(Path(snapshot['launcher']).name, 'run_optimize_slurm.py')
        self.assertEqual(Path(snapshot['launcher']).parent, self.run / 'source/script')
        self.assertTrue((self.run / 'source/new_benchmark').is_symlink())
        self.assertEqual((self.run / 'source/new_benchmark').resolve(), self.repo / 'new_benchmark')
        self.assertFalse(any('__pycache__' in key or key.endswith('.pyc') for key in snapshot['files']))
        for name, original in self.libraries.items():
            frozen = Path(snapshot['library_dir']) / name
            self.assertEqual(frozen.read_bytes(), original.read_bytes())
            self.assertFalse(frozen.is_symlink())

    def test_missing_or_tampered_frozen_file_is_rejected(self):
        for operation in ('change', 'remove', 'symlink'):
            with self.subTest(operation=operation):
                run = self.root / operation
                snapshot = self.create(run)
                path = Path(snapshot['solver'])
                if operation == 'change':
                    path.write_bytes(b'tampered\n')
                else:
                    path.unlink()
                    if operation == 'symlink':
                        path.symlink_to(self.solver)
                with self.assertRaises(ValueError):
                    self.module.check_snapshot(run, snapshot)

    def test_snapshot_uses_the_converters_adjacent_bundle_when_present(self):
        self.write('NIA_LS/converter/z3-5.1.0/build/python/z3/__init__.py', b'# bundled sdk\n')
        self.write('NIA_LS/converter/z3-5.1.0/build/libz3.so', b'bundled library\n')
        snapshot = self.create()
        bundle = Path(snapshot['converter']).parent / 'z3-5.1.0/build'
        self.assertTrue(bundle.is_dir(), 'frozen converter must contain its adjacent Z3 5.1 SDK')
        self.assertEqual((bundle / 'python/z3/__init__.py').read_bytes(), b'# bundled sdk\n')
        self.assertEqual((bundle / 'libz3.so').read_bytes(), b'bundled library\n')

    def test_identical_native_and_converter_libraries_remain_identical(self):
        self.libraries['libz3.so.5.1'] = self.z3_library
        snapshot = self.create()
        converter_library = Path(snapshot['converter']).parent / 'z3-5.1.0/build/libz3.so'
        native_library = Path(snapshot['library_dir']) / 'libz3.so.5.1'
        self.assertTrue(converter_library.is_file(), 'snapshot must freeze the 5.1 converter library')
        self.assertEqual(converter_library.read_bytes(), native_library.read_bytes())
        self.assertEqual(snapshot['files'][converter_library.relative_to(self.run).as_posix()],
                         snapshot['files'][native_library.relative_to(self.run).as_posix()])

    def test_legacy_sdk_is_not_used_when_5_1_sdk_is_missing(self):
        self.sdk.unlink()
        with self.assertRaisesRegex(ValueError, 'converter Python SDK is missing.*z3-5.1.0'):
            self.create()

    def test_previous_sdk_snapshot_metadata_remains_verifiable(self):
        for sdk_name, native_soname in (('z3-4.12.3', 'libz3.so.5.1'),
                                        ('z3-5.1.0', 'libz3.so.5.1'),
                                        ('z3_fork', 'libz3.so.4')):
            with self.subTest(sdk=sdk_name):
                run = self.root / sdk_name
                source = run / 'source'
                payloads = {
                    'nia_ls': b'old solver', 'converter/omt_to_ls.py': b'old converter',
                    'script/run_optimize_slurm.py': b'old launcher',
                    'result/competitor.csv': b'old baseline',
                    f'lib/{native_soname}': b'old native library',
                    f'converter/{sdk_name}/build/python/z3/__init__.py': b'old sdk',
                    f'converter/{sdk_name}/build/libz3.so': b'old SDK library',
                }
                files = {}
                for relative, payload in payloads.items():
                    path = source / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(payload)
                    files['source/' + relative] = hashlib.sha256(payload).hexdigest()
                snapshot = {'solver': str(source / 'nia_ls'),
                            'converter': str(source / 'converter/omt_to_ls.py'),
                            'launcher': str(source / 'script/run_optimize_slurm.py'),
                            'baseline': str(source / 'result/competitor.csv'),
                            'library_dir': str(source / 'lib'), 'files': files,
                            'solver_sha256': files['source/nia_ls'],
                            'converter_sha256': files['source/converter/omt_to_ls.py']}
                self.module.check_snapshot(run, snapshot)

    def test_live_path_or_escaping_hash_entry_is_rejected(self):
        snapshot = self.create()
        changed = dict(snapshot, solver=str(self.solver))
        with self.assertRaises(ValueError):
            self.module.check_snapshot(self.run, changed)
        changed = dict(snapshot, files=dict(snapshot['files']))
        changed['files']['../repo/NIA_LS/src/nia_ls'] = snapshot['solver_sha256']
        with self.assertRaises(ValueError):
            self.module.check_snapshot(self.run, changed)

    def test_existing_snapshot_is_not_overwritten(self):
        snapshot = self.create()
        with self.assertRaises((ValueError, FileExistsError)):
            self.create()
        self.module.check_snapshot(self.run, snapshot)

    def test_environment_prepends_frozen_libraries_without_mutating_parent(self):
        snapshot = self.create()
        with mock.patch.dict(os.environ, {'PATH': '/test/bin', 'LD_LIBRARY_PATH': '/existing/lib'}, clear=True):
            environment = self.module.snapshot_environment(snapshot)
            self.assertEqual(environment['PATH'], '/test/bin')
            self.assertEqual(environment['LD_LIBRARY_PATH'], snapshot['library_dir'] + ':/existing/lib')
            self.assertEqual(environment['PYTHONDONTWRITEBYTECODE'], '1')
            self.assertEqual(os.environ['LD_LIBRARY_PATH'], '/existing/lib')
            self.assertNotIn('PYTHONDONTWRITEBYTECODE', os.environ)

    def test_native_libraries_uses_sonames_and_excludes_glibc(self):
        text = '''linux-vdso.so.1 (0x001)
libz3.so.5.1 => /lib/libz3.so.5.1 (0x002)
libgmpxx.so.4 => /lib/libgmpxx.so.4 (0x003)
libgmp.so.10 => /lib/libgmp.so.10 (0x004)
libstdc++.so.6 => /lib/libstdc++.so.6 (0x005)
libgcc_s.so.1 => /lib/libgcc_s.so.1 (0x006)
libm.so.6 => /lib/libm.so.6 (0x007)
libc.so.6 => /lib/libc.so.6 (0x008)
/lib64/ld-linux-x86-64.so.2 (0x009)
'''
        with mock.patch.object(self.module.subprocess, 'run', return_value=SimpleNamespace(stdout=text)):
            libraries = self.module.native_libraries(self.solver)
        self.assertEqual(set(libraries), set(self.libraries))
        self.assertEqual(libraries['libz3.so.5.1'], Path('/lib/libz3.so.5.1'))
        with mock.patch.object(self.module.subprocess, 'run',
                               return_value=SimpleNamespace(stdout='libz3.so.4 => not found\n')):
            with self.assertRaises(ValueError):
                self.module.native_libraries(self.solver)


if __name__ == '__main__':
    unittest.main()
