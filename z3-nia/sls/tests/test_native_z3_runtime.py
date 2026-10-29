"""The executable must report and enforce the actual loaded Z3 version."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


NATIVE = Path(__file__).resolve().parents[1] / 'src/nia_ls'


class NativeZ3RuntimeTests(unittest.TestCase):
    def test_reports_actual_z3_runtime_version_without_an_input(self):
        result = subprocess.run([str(NATIVE), '--z3-version'], capture_output=True,
                                text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), '5.1.0.0')

    def test_refuses_wrong_runtime_even_during_help_preflight(self):
        compiler = shutil.which('cc')
        if compiler is None:
            self.skipTest('C compiler is required for the runtime substitution fixture')
        code = '''
void Z3_get_version(unsigned *a, unsigned *b, unsigned *c, unsigned *d) {
    *a=4; *b=8; *c=12; *d=0;
}
const char *Z3_get_full_version(void) { return "4.8.12.0 (test substitute)"; }
'''
        with tempfile.TemporaryDirectory(prefix='nia-z3-version-') as directory:
            library = Path(directory) / 'version.so'
            subprocess.run([compiler, '-shared', '-fPIC', '-x', 'c', '-', '-o', str(library)],
                           input=code, text=True, check=True, capture_output=True, timeout=30)
            environment = dict(os.environ, LD_PRELOAD=str(library))
            result = subprocess.run([str(NATIVE), '--help'], capture_output=True,
                                    text=True, timeout=10, env=environment)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn('requires Z3 5.1.0', result.stderr)
        self.assertIn('4.8.12.0', result.stderr)


if __name__ == '__main__':
    unittest.main()
