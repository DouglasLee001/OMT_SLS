"""The frozen runner accepts only well-scoped second-round controls."""
import importlib.util
from pathlib import Path
import unittest

SCRIPT = Path(__file__).resolve().parents[2] / 'experiment/runtime_adapter.py'


class Round2AdapterTests(unittest.TestCase):
    def module(self):
        spec = importlib.util.spec_from_file_location('round2_adapter', SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_new_controls_are_frozen_and_forwarded_to_optimization_only(self):
        adapter = self.module()
        options = ('--smt-exchange=hints',
                   '--isolate-exchange-context', '--slack-repair')
        try:
            validated = adapter.validated_solver_options(options)
        except ValueError as error:
            self.fail('supported experiment options must be accepted: ' + str(error))
        self.assertEqual(validated, options)
        command = adapter.translate_command(['/native', '/input.nia', '--optimize'],
                                            'embedded', '/solver', '/converter.py', options)
        self.assertEqual(command[-len(options):], list(options))
        import_command = adapter.translate_command(['python3', '/converter.py', '/input.smt2', '-o', '/input.nia'],
                                                   'embedded', '/solver', '/converter.py', options)
        self.assertFalse(set(options).intersection(import_command))

    def test_missing_backends_and_valued_boolean_controls_are_rejected(self):
        adapter = self.module()
        for options in [('--isolate-exchange-context',), ('--slack-repair=yes',),
                        ('--smt-exchange=hints', '--isolate-exchange-context=yes')]:
            with self.subTest(options=options), self.assertRaises(ValueError):
                adapter.validated_solver_options(options)


if __name__ == '__main__':
    unittest.main()
