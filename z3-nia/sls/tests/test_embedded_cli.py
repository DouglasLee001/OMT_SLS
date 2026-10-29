"""Small CLI regressions for the source-embedded Z3/NIA shell.

Set NIA_EMBEDDED_Z3 to the newly built shell. No external converter or old
NIA executable is invoked by these tests.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
BINARY = Path(os.environ.get('NIA_EMBEDDED_Z3', ROOT / 'build' / 'z3'))
SOURCE = '(set-logic QF_NIA)\n(declare-const x Int)\n(assert (= x 3))\n(minimize x)\n(check-sat)\n'


class EmbeddedCliTests(unittest.TestCase):
    def run_cli(self, *args, cwd=None, success=True):
        result = subprocess.run([str(BINARY), '--nia-ls', *map(str, args)],
                                cwd=cwd, text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0 if success else 2,
                         (result.stdout, result.stderr))
        return result

    def fixture(self, directory):
        source = Path(directory) / 'input.smt2'
        source.write_text(SOURCE)
        return source

    def test_embedded_help(self):
        result = self.run_cli('--help')
        for token in ('--nia-ls', '--native-input', '--mode', 'sls', 'unbounded', 'optimal'):
            self.assertIn(token, result.stdout)

    def test_import_export_and_absolute_source(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self.fixture(directory)
            native = Path(directory) / 'native.nia'
            result = self.run_cli(source.name, '--import-only', '--keep-definitions',
                                  '--export-native=' + str(native), cwd=directory)
            metadata = json.loads(result.stdout)
            self.assertEqual(metadata['source'], str(source.resolve()))
            self.assertEqual(metadata['direction'], 'min')
            self.assertTrue(native.read_text().startswith('NIA_LS_OMT 1\n'))
            self.assertIn('SOURCE ' + str(source.resolve()).encode().hex(), native.read_text())
            native_metadata = json.loads(self.run_cli('--native-input', native, '--import-only').stdout)
            self.assertEqual(native_metadata, metadata)

    def test_sls_mode_and_legacy_native_passthrough(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self.fixture(directory)
            native = Path(directory) / 'native.nia'
            model = Path(directory) / 'model.tsv'
            result = self.run_cli(source, '--mode=sls', '--keep-definitions', '--target=4',
                                  '--initialize=off', '--restart-repair=off', '--timeout', '.5',
                                  '--model-output', model, '--export-native=' + str(native))
            final = json.loads(result.stdout.splitlines()[-1])
            self.assertEqual(final['event'], 'final')
            self.assertEqual(final['objective_value'], '3')
            self.assertFalse(final['prove_optimal'])
            self.assertFalse(final['is_unbounded'])
            self.assertEqual(final['optimality_attempts'], 0)
            self.assertEqual(final['unbounded_attempts'], 0)
            self.assertTrue(model.exists())
            imported = re.search(r'nia_embedded_import_seconds=([0-9.eE+-]+)', result.stderr)
            self.assertIsNotNone(imported, result.stderr)
            self.assertGreaterEqual(final['elapsed_seconds'], float(imported.group(1)))
            native_result = self.run_cli('--native-input', native, '--optimize', '--target=4',
                                         '--initialize=off', '--restart-repair=off', '--timeout=.5')
            native_final = json.loads(native_result.stdout.splitlines()[-1])
            self.assertEqual(native_final['objective_value'], '3')
            self.assertFalse(native_final['prove_optimal'])
            self.assertIsNone(native_final['unbounded_threshold'])

    def test_modes_select_only_the_requested_proofs(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self.fixture(directory)
            for mode in ('unbounded', 'optimal'):
                args = [source, '--mode=' + mode, '--keep-definitions', '--initialize=off',
                        '--restart-repair=off', '--timeout=.2']
                if mode == 'optimal':
                    args += ['--optimality-stall=.01', '--optimality-timeout=.02']
                final = json.loads(self.run_cli(*args).stdout.splitlines()[-1])
                self.assertEqual(final['prove_optimal'], mode == 'optimal')
                self.assertEqual(final['unbounded_threshold'], '1000000')

    def test_mode_conflicts_and_input_overwrite_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self.fixture(directory)
            original = source.read_text()
            for options in (('--mode=sls', '--prove-optimal'),
                            ('--mode=sls', '--optimality-resume'),
                            ('--mode=unbounded', '--prove-optimal'),
                            ('--mode=optimal', '--first-solution'),
                            ('--mode=unknown',),
                            ('--import-only', '--export-native=' + str(source))):
                result = self.run_cli(source, *options, success=False)
                self.assertTrue(result.stderr.strip())
            self.assertEqual(source.read_text(), original)

    def assert_input_conflict(self, protected, *arguments, absent=()):
        original = {path: path.read_bytes() for path in protected}
        result = subprocess.run([str(BINARY), '--nia-ls', *map(str, arguments)],
                                text=True, capture_output=True, timeout=10)
        for path, contents in original.items():
            self.assertTrue(path.is_file(), (path, result.stdout, result.stderr))
            self.assertEqual(path.read_bytes(), contents,
                             ('protected input changed', path, result.stdout, result.stderr))
        for path in absent:
            self.assertFalse(path.exists(), ('output written before conflict rejection', path))
        self.assertEqual(result.returncode, 2, (result.stdout, result.stderr))
        self.assertIn('input', result.stderr.lower())

    def test_model_output_and_derived_paths_cannot_replace_inputs(self):
        # The writer touches MODEL, MODEL.tmp, MODEL.prev and MODEL.prev.tmp.
        # Keep an old model present to exercise the backup paths on the old binary.
        for suffix in ('', '.tmp', '.prev', '.prev.tmp'):
            for native_route in (False, True):
                with self.subTest(suffix=suffix, native=native_route), tempfile.TemporaryDirectory() as directory:
                    folder = Path(directory)
                    model = folder / 'model'
                    source = folder / ('model' + suffix)
                    source.write_text(SOURCE)
                    if source != model:
                        model.write_text('previous model\n')
                    native = folder / 'native.nia'
                    if native_route:
                        self.run_cli(source, '--import-only', '--keep-definitions',
                                     '--export-native=' + str(native))
                    exported = folder / 'must-not-be-exported.nia'
                    inputs = ['--native-input', native] if native_route else [source, '--keep-definitions']
                    protected = [source, native] if native_route else [source]
                    self.assert_input_conflict(protected, *inputs, '--mode=sls', '--target=4',
                        '--initialize=off', '--restart-repair=off', '--timeout=.2',
                        '--model-output=' + str(model), '--export-native=' + str(exported),
                        absent=[exported])

    def test_native_metadata_source_and_native_ir_are_protected(self):
        for output_kind, protected_kind in (('export', 'source'), ('model', 'native'),
                                            ('query', 'source'), ('query', 'native')):
            with self.subTest(output=output_kind, protected=protected_kind), tempfile.TemporaryDirectory() as directory:
                folder = Path(directory)
                source = self.fixture(directory)
                native = folder / 'native.nia'
                self.run_cli(source, '--import-only', '--keep-definitions',
                             '--export-native=' + str(native))
                target = source if protected_kind == 'source' else native
                options = ['--import-only', '--export-native=' + str(target)] if output_kind == 'export' else [
                    '--mode=' + ('optimal' if output_kind == 'query' else 'sls'),
                    '--initialize=off', '--restart-repair=off', '--timeout=.2',
                    ('--optimality-query-output=' if output_kind == 'query' else '--model-output=') + str(target)]
                if output_kind == 'query':
                    options += ['--optimality-stall=.01', '--optimality-timeout=.02']
                self.assert_input_conflict([source, native], '--native-input', native, *options)

        # The legacy default query output is derived from the native path too.
        with tempfile.TemporaryDirectory() as directory:
            native = Path(directory) / 'native.nia'
            source = Path(str(native) + '.optimality.smt2')
            source.write_text(SOURCE)
            self.run_cli(source, '--import-only', '--keep-definitions',
                         '--export-native=' + str(native))
            self.assert_input_conflict([source, native], '--native-input', native,
                '--mode=optimal', '--initialize=off', '--restart-repair=off', '--timeout=.2',
                '--optimality-stall=.01', '--optimality-timeout=.02')

    def test_initial_query_derived_output_cannot_replace_metadata_source(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            query = folder / 'proof.smt2'
            source = Path(str(query) + '.initial.smt2')
            source.write_text(SOURCE)
            native = folder / 'native.nia'
            self.run_cli(source, '--import-only', '--keep-definitions',
                         '--export-native=' + str(native))
            self.assert_input_conflict([source, native], '--native-input', native,
                '--mode=optimal', '--initialize=off', '--restart-repair=off', '--timeout=.2',
                '--initial-z3-after=.01', '--optimality-query-output=' + str(query))

    def test_output_input_aliases_are_rejected(self):
        for alias_kind, output_kind in (('symlink', 'model'), ('hardlink', 'model'),
                                        ('normalized', 'model'), ('symlink', 'export'),
                                        ('hardlink', 'query')):
            with self.subTest(alias=alias_kind, output=output_kind), tempfile.TemporaryDirectory() as directory:
                folder = Path(directory)
                source = self.fixture(directory)
                native = folder / 'native.nia'
                self.run_cli(source, '--import-only', '--keep-definitions',
                             '--export-native=' + str(native))
                alias = folder / 'input-alias'
                if alias_kind == 'symlink':
                    alias.symlink_to(source)
                elif alias_kind == 'hardlink':
                    os.link(source, alias)
                else:
                    (folder / 'subdir').mkdir()
                    alias = folder / 'subdir' / '..' / source.name
                options = ['--import-only', '--export-native=' + str(alias)] if output_kind == 'export' else [
                    '--mode=' + ('optimal' if output_kind == 'query' else 'sls'),
                    '--initialize=off', '--restart-repair=off', '--timeout=.2',
                    ('--optimality-query-output=' if output_kind == 'query' else '--model-output=') + str(alias)]
                if output_kind == 'query':
                    options += ['--optimality-stall=.01', '--optimality-timeout=.02']
                self.assert_input_conflict([source, native], '--native-input', native, *options)
                if alias_kind == 'symlink':
                    self.assertTrue(alias.is_symlink(), 'input alias was replaced before rejection')

    def test_native_flags_are_validated_before_metadata_or_export(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            native = folder / 'incomplete.nia'
            native.write_text('NIA_LS_OMT 1\n')
            exported = folder / 'must-not-be-exported.nia'
            for options, diagnostic in (
                    (['--optimize', '--prove-optimal', '--initial-z3-after=-1'], 'invalid initial Z3 timing'),
                    (['--optimize', '--optimality-resume'], 'require --prove-optimal'),
                    (['--optimize', '--timeout='], 'invalid timeout')):
                with self.subTest(options=options):
                    result = self.run_cli('--native-input', native, *options,
                        '--export-native=' + str(exported), success=False)
                    self.assertIn(diagnostic, result.stderr)
                    self.assertFalse(exported.exists())
                    self.assertEqual(native.read_text(), 'NIA_LS_OMT 1\n')
            # Successful flag validation still fails closed on malformed metadata.
            result = self.run_cli('--native-input', native, '--import-only',
                '--export-native=' + str(exported), success=False)
            self.assertIn('native input header', result.stderr)
            self.assertFalse(exported.exists())

    def test_normal_z3_entrypoint_remains_available(self):
        result = subprocess.run([str(BINARY), '-version'], text=True,
                                capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('5.1.0', result.stdout)


if __name__ == '__main__':
    unittest.main()
