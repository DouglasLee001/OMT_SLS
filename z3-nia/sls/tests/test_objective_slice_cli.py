"""Source-subset proofs must survive CLI, frozen options and worker boundaries."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
BINARY = Path(os.environ.get('NIA_EMBEDDED_Z3', ROOT / 'build/z3'))
SOURCE = ('(set-logic QF_NIA)\n(declare-const x Int)\n(declare-const z Int)\n'
          '(assert (= z (* x x)))\n' +
          ''.join(f'(declare-const u{i} Int)\n(assert (>= u{i} 0))\n' for i in range(24)) +
          '(minimize z)\n(check-sat)\n')


class ObjectiveSliceCliTests(unittest.TestCase):
    def invoke(self, folder, options, seconds='1'):
        source = folder / 'source.smt2'
        source.write_text(SOURCE)
        result = subprocess.run([str(BINARY), '--nia-ls', str(source),
            '--initialize=off', '--timeout=' + seconds, *options],
            text=True, capture_output=True, timeout=15)
        self.assertEqual(source.read_text(), SOURCE)
        return result

    def test_subset_proof_reaches_original_optimum_in_both_worker_modes(self):
        for resume in (False, True):
            with self.subTest(resume=resume), tempfile.TemporaryDirectory() as d:
                folder = Path(d)
                proof = folder / 'proof.smt2'
                options = ['--mode=optimal', '--objective-slice', '--objective-slice-budget=.25',
                    '--optimality-stall=.001', '--optimality-timeout=.5',
                    '--optimality-query-output=' + str(proof)]
                if resume:
                    options.append('--optimality-resume')
                result = self.invoke(folder, options, '2')
                self.assertEqual(result.returncode, 0, (result.stdout, result.stderr))
                final = json.loads(result.stdout.splitlines()[-1])
                self.assertEqual(final['objective_value'], '0')
                self.assertTrue(final['is_optimal'], (final, result.stderr))
                self.assertIs(final.get('objective_slice_enabled'), True)
                self.assertEqual(final.get('objective_slice_budget_seconds'), .25)
                self.assertIn('objective_slice', final['optimality_reason'])
                self.assertIn('budget=0.25', result.stderr)
                certificates = list(folder.glob('proof.smt2.slice.*.json'))
                self.assertEqual(len(certificates), 1, (certificates, result.stderr))
                certificate = json.loads(certificates[0].read_text())
                self.assertEqual(certificate['version'], 'omt-objective-slice-v1')
                self.assertLess(len(certificate['selected_indices']), certificate['flattened_source_count'])

    def test_off_by_default_and_requires_original_optimality_mode(self):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d)
            result = self.invoke(folder, ['--mode=sls'], '.1')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIs(json.loads(result.stdout.splitlines()[-1]).get('objective_slice_enabled'), False)
            self.assertEqual(json.loads(result.stdout.splitlines()[-1]).get('objective_slice_budget_seconds'), 2)
            result = self.invoke(folder, ['--mode=sls', '--objective-slice'])
            self.assertEqual(result.returncode, 2)
            self.assertIn('--prove-optimal', result.stderr)

    def test_frontend_help_exposes_freezable_option(self):
        result = subprocess.run([str(BINARY), '--nia-ls', '--help'],
                                text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0)
        self.assertIn('--objective-slice', result.stdout)
        self.assertIn('--objective-slice-budget=SECONDS', result.stdout)

    def test_budget_is_positive_finite_and_requires_the_slice_switch(self):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d)
            for value in ('0', '-1', 'nan', 'inf', '1x', '', ' 2'):
                with self.subTest(value=value):
                    result = self.invoke(folder, ['--mode=optimal', '--objective-slice',
                                                 '--objective-slice-budget=' + value])
                    self.assertEqual(result.returncode, 2, (result.stdout, result.stderr))
                    self.assertIn('finite positive', result.stderr)
            result = self.invoke(folder, ['--mode=optimal', '--objective-slice-budget=2'])
            self.assertEqual(result.returncode, 2)
            self.assertIn('requires --objective-slice', result.stderr)

    def test_frozen_adapter_forwards_only_to_optimization(self):
        spec = importlib.util.spec_from_file_location('slice_runtime_adapter', ROOT / 'experiment/runtime_adapter.py')
        adapter = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(adapter)
        options = ('--objective-slice', '--objective-slice-budget=8')
        try:
            actual = adapter.validated_solver_options(options)
        except ValueError as error:
            self.fail('valid objective-slice control must be accepted: ' + str(error))
        self.assertEqual(actual, options)
        optimize = adapter.translate_command(['/native', '/input.nia', '--optimize'],
                    'embedded', '/solver', '/converter.py', options)
        self.assertEqual(tuple(optimize[-2:]), options)
        imported = adapter.translate_command(['python3', '/converter.py', '/source.smt2', '-o', '/input.nia'],
                    'embedded', '/solver', '/converter.py', options)
        self.assertNotIn('--objective-slice', imported)
        invalid_options = [('--objective-slice=yes',), ('--objective-slice', '--objective-slice'),
                           ('--objective-slice-budget=2',)]
        invalid_options += [('--objective-slice', '--objective-slice-budget=' + value)
                            for value in ('', '0', '-1', 'nan', 'inf', '1x', ' 2')]
        for invalid in invalid_options:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                adapter.validated_solver_options(invalid)


if __name__ == '__main__':
    unittest.main()
