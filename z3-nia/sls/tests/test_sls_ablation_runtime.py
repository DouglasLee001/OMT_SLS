"""SLS ablations reach only optimization and preserve the full proof baseline."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[3]
ADAPTER_PATH = ROOT / 'z3-nia/experiment/runtime_adapter.py'
SPEC = importlib.util.spec_from_file_location('sls_ablation_adapter', ADAPTER_PATH)
ADAPTER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ADAPTER)
VARIANTS = {
    'full': None,
    'no-objective-neighborhoods': '--no-sls-objective-neighborhoods',
}


class SlsAblationAdapterTests(unittest.TestCase):
    def test_removed_block_repair_control_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'unsupported solver option'):
            ADAPTER.validated_solver_options(['--no-sls-block-repair'])

    def test_removed_plateau_control_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'unsupported solver option'):
            ADAPTER.validated_solver_options(['--no-sls-plateau'])

    def test_each_ablation_is_forwarded_only_to_optimization(self):
        for option in filter(None, VARIANTS.values()):
            with self.subTest(option=option):
                self.assertEqual(ADAPTER.validated_solver_options([option]), (option,))
                optimized = ADAPTER.translate_command(
                    ['old', 'case.nia', '--optimize'], 'embedded', '/solver', '/converter', [option])
                self.assertEqual(optimized[-1], option)
                imported = ADAPTER.translate_command(
                    ['python', '/converter', 'case.smt2', '-o', 'case.nia'],
                    'embedded', '/solver', '/converter', [option])
                self.assertNotIn(option, imported)

    def test_search_ablations_can_also_be_used_with_plain_proof(self):
        for option in filter(None, VARIANTS.values()):
            with self.subTest(option=option):
                options = ('--plain-z3-proof', option)
                self.assertEqual(ADAPTER.validated_solver_options(options), options)

    def test_valued_and_duplicate_ablation_controls_are_rejected(self):
        for option in filter(None, VARIANTS.values()):
            for options in ([option + '=true'], [option, option]):
                with self.subTest(options=options), self.assertRaises(ValueError):
                    ADAPTER.validated_solver_options(options)


class SlsAblationScriptTests(unittest.TestCase):
    def invoke(self, *arguments):
        script = ROOT / 'script/run_sls_ablation.sh'
        self.assertTrue(script.is_file(), 'the SLS ablation launcher must exist')
        with tempfile.TemporaryDirectory(prefix='sls-ablation-script-') as folder:
            scripts = Path(folder) / 'script'
            scripts.mkdir()
            for source in (script, ROOT / 'script/run_tactic_chain.sh'):
                shutil.copyfile(source, scripts / source.name)
            # Capture argv at the submission boundary, without invoking Slurm.
            (scripts / 'run_z3_nia_slurm.sh').write_text(
                '#!/usr/bin/env bash\n'
                'exec python3 -c \'import json,sys; print(json.dumps(sys.argv[1:]))\' "$@"\n')
            return subprocess.run(['bash', str(scripts / script.name), *arguments],
                                  capture_output=True, text=True, timeout=10)

    def test_variants_disable_exactly_one_component_and_keep_baseline(self):
        names = set()
        for variant, option in VARIANTS.items():
            with self.subTest(variant=variant):
                result = self.invoke(variant)
                self.assertEqual(result.returncode, 0, result.stderr)
                arguments = json.loads(result.stdout)
                disabled = [arg for arg in arguments if arg.startswith('--solver-option=--no-sls-')]
                self.assertEqual(disabled, [] if option is None else ['--solver-option=' + option])
                self.assertIn('--solver-option=--guarded-product', arguments)
                self.assertIn('--solver-option=--smt-exchange=backend', arguments)
                self.assertIn('--solver-option=--optimality-bv-seconds=120', arguments)
                self.assertNotIn('--no-optimality-cutset', arguments)
                for flag, value in (('--nodes', 'node3,node4,node5'), ('--jobs', '180'),
                                    ('--timeout', '1200'), ('--memory-mb', '96000')):
                    self.assertEqual(arguments[arguments.index(flag) + 1], value)
                self.assertIn('--queue-workers', arguments)
                name = arguments[arguments.index('--job-name') + 1]
                self.assertNotIn(name, names)
                names.add(name)

    def test_extra_launcher_arguments_are_forwarded_verbatim(self):
        extra = ['--nodes', 'node1,node2', '--jobs', '12', '--dry-run',
                 '--output', '/tmp/results with spaces']
        result = self.invoke('no-objective-neighborhoods', *extra)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)[-len(extra):], extra)

    def test_missing_or_unknown_variant_fails_before_submission(self):
        for arguments in ((), ('unknown',), ('no-block-repair',), ('no-plateau',)):
            with self.subTest(arguments=arguments):
                result = self.invoke(*arguments)
                self.assertEqual(result.returncode, 2)
                self.assertIn('full', result.stderr)
                self.assertEqual(result.stdout, '')


if __name__ == '__main__':
    unittest.main()
