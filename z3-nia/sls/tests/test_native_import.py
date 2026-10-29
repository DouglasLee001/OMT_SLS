"""HEAD converter parity for the embedded C++ importer; never runs a search.

Root builds tests/native_import_driver.cpp with frontend/nia_import.cpp and sets
NIA_NATIVE_IMPORT_DRIVER (default: z3-nia/sls/build/importer_driver). Python is an
independent test oracle only, loaded from BASELINE_COMMIT via git show.
"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

NIA = Path(__file__).resolve().parents[2]
REPO = NIA.parent
DRIVER = Path(os.environ.get('NIA_NATIVE_IMPORT_DRIVER', NIA / 'sls/build/importer_driver'))
MODES = ((True, False), (True, True), (False, False), (False, True))


ORIGINAL_FAMILIES = ('calypto', 'AProVE', 'leipzig', 'mcm', '20170427-VeryMax',
                     'LassoRanker', 'UltimateLassoRanker')
NEW_FAMILIES = ('portfolio', 'cyclic', 'packing', 'design')


def dataset_representatives(original_root=None, new_root=None):
    """Discover the smallest nonempty SMT2 case in every required family."""
    original_root = Path(original_root or '/data/shared/lbh/OMT/benchmarks/QF_NIA')
    new_root = Path(new_root or REPO / 'new_benchmark')

    def smallest(folder):
        candidates = []
        for path in folder.rglob('*.smt2'):
            if path.is_file():
                size = path.stat().st_size
                if size:
                    candidates.append((size, path.as_posix(), path))
        if not candidates:
            raise AssertionError('no nonempty SMT2 input in required family: ' + str(folder))
        return min(candidates)[2]

    selected = [('original:' + family, smallest(original_root / family)) for family in ORIGINAL_FAMILIES]
    discovered = {p.name for p in new_root.iterdir() if p.is_dir() and (p / 'instance').is_dir()}
    missing = set(NEW_FAMILIES) - discovered
    if missing:
        raise AssertionError('missing new source families: ' + ', '.join(sorted(missing)))
    selected += [('new:' + family, smallest(new_root / family / 'instance')) for family in sorted(discovered)]
    return selected


def smt(declarations, assertions, objective, sense='minimize'):
    return '(set-logic QF_NIA)\n' + declarations + '\n' + assertions + '\n(' + sense + ' ' + objective + ')\n(check-sat)\n'


class NativeImportSourceTests(unittest.TestCase):
    def test_cpp_importer_sources_exist(self):
        for name in ('nia_import.h', 'nia_import.cpp'):
            self.assertTrue((NIA / 'sls/frontend' / name).is_file(), 'C++ importer not implemented: ' + name)


    def test_dataset_discovery_is_nonempty_deterministic_and_covers_leipzig_verymax(self):
        with tempfile.TemporaryDirectory(prefix='nia-import-discovery-') as temp:
            folder = Path(temp)
            for family in ORIGINAL_FAMILIES:
                directory = folder / 'original' / family
                directory.mkdir(parents=True)
                (directory / 'empty.smt2').write_text('')
                (directory / 'b.smt2').write_text('larger')
                (directory / 'a.smt2').write_text('x')
            for family in NEW_FAMILIES:
                directory = folder / 'new' / family / 'instance'
                directory.mkdir(parents=True)
                (directory / 'small.smt2').write_text('x')
            selected = dataset_representatives(folder / 'original', folder / 'new')
            self.assertEqual(len(selected), 11)
            choices = dict(selected)
            self.assertEqual(choices['original:leipzig'].name, 'a.smt2')
            self.assertEqual(choices['original:20170427-VeryMax'].name, 'a.smt2')
            self.assertTrue(all(path.stat().st_size for _, path in selected))


class NativeImportParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not DRIVER.is_file():
            raise unittest.SkipTest('root must compile importer_driver, or set NIA_NATIVE_IMPORT_DRIVER')
        cls.temporary = tempfile.TemporaryDirectory(prefix='nia-native-import-')
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.folder = Path(cls.temporary.name)
        baseline = (NIA / 'BASELINE_COMMIT').read_text().strip()
        if not baseline.startswith('6614f3c1e'):
            raise AssertionError('expected authorized HEAD 6614f3c1e baseline')
        text = subprocess.run(['git', 'show', baseline + ':NIA_LS/converter/omt_to_ls.py'],
                              cwd=REPO, text=True, capture_output=True, check=True, timeout=20).stdout
        cls.converter = cls.folder / 'baseline_converter.py'
        cls.converter.write_text(text)
        cls.sdk = cls.folder / 'sdk'
        cls.sdk.mkdir()
        (cls.sdk / 'build').symlink_to(NIA / 'build-competitor', target_is_directory=True)
        cls.serial = 0

    def pair(self, source, preprocess=True, keep=False):
        type(self).serial += 1
        path = self.folder / ('case-' + str(self.serial) + '.smt2')
        path.write_text(source)
        flags = ([] if preprocess else ['--no-preprocess']) + (['--keep-definitions'] if keep else [])
        native = subprocess.run([str(DRIVER), str(path), *flags], text=True, capture_output=True, timeout=45)
        code = '''
import importlib.util, pathlib, sys
spec=importlib.util.spec_from_file_location('baseline_converter',sys.argv[1])
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
m.Z3_ROOT=pathlib.Path(sys.argv[2])
z3=m.load_z3()
def forbidden(*args,**kwargs):raise AssertionError('importer oracle must not search or set global parameters')
z3.Solver.check=forbidden;z3.Optimize.check=forbidden;z3.set_param=forbidden;z3.Context.set=forbidden
try:sys.stdout.write(m.convert_text(pathlib.Path(sys.argv[3]).read_text(),sys.argv[3],sys.argv[4]=='1',keep_definitions=sys.argv[5]=='1'))
except (m.ConversionError,RecursionError) as error:
 print(str(error),file=sys.stderr);sys.exit(2)
'''
        reference = subprocess.run([sys.executable, '-B', '-c', code, str(self.converter), str(self.sdk), str(path),
                                    str(int(preprocess)), str(int(keep))], text=True, capture_output=True, timeout=45)
        return native, reference

    def equivalent(self, source, modes=MODES):
        for preprocess, keep in modes:
            with self.subTest(preprocess=preprocess, keep_definitions=keep):
                native, reference = self.pair(source, preprocess, keep)
                self.assertEqual(reference.returncode, 0, reference.stderr)
                self.assertEqual(native.returncode, 0, native.stderr)
                self.assertEqual(native.stdout, reference.stdout)

    def test_objective_marker_alias_and_polynomial_order(self):
        self.equivalent(smt('(declare-const x Int)(declare-const y Int)(declare-const cost Int)',
            '(assert (<= (- 2) x))(assert (<= x 2))(assert (= y (* x x)))(assert (= cost (+ (* y y) x 3)))', 'cost'))

    def test_low_degree_hard_clauses_precede_objective_traversal(self):
        decl = '(declare-const x Int)(declare-const y Int)(declare-const p Int)'
        hard = '(assert (<= 0 x))(assert (<= x 2))(assert (<= (- 2) y))(assert (<= y 2))(assert (= p (* x y)))'
        for target in ('x', 'y', 'p', '(* p p)'):
            self.equivalent(smt(decl, hard, target))

    def test_boolean_ite_distinct_comparisons_and_maximize(self):
        self.equivalent(smt('(declare-const b Bool)(declare-const c Bool)(declare-const x Int)(declare-const y Int)',
            '(assert (or (and b c) (not (= (* x y) 0))))(assert (distinct x y 7))'
            '(assert (or (> x y) (<= (* x y) 3)))(assert (=> b (< x 4)))',
            '(ite b (+ (* x x) (- y)) (+ y 5))', 'maximize'))

    def test_quoted_unicode_names_and_readonly_metadata(self):
        source = smt('(declare-const |费用 α| Int)(declare-const |x with spaces| Int)',
            '(assert (= |费用 α| (+ (* |x with spaces| |x with spaces|) 2)))', '|费用 α|')
        source = '(set-info :source "escaped ""text""; not a comment")\n' + source
        source += '(get-model)(get-objectives)(echo "ignored")(set-info :status unknown)(exit)(push 1)'
        self.equivalent(source)

    def test_unsat_fixed_and_objective_only(self):
        for source in [smt('(declare-const cost Int)', '(assert false)', 'cost', 'maximize'),
                       smt('(declare-const x Int)', '(assert (= x 7))', '(* x x)'),
                       smt('(declare-const target Int)', '', 'target'),
                       '(declare-const x Int)(minimize (+ x 3))']:
            self.equivalent(source)

    def test_exact_128bit_boundaries_and_cancellation(self):
        for value in (-(1 << 127), (1 << 127) - 1, (1 << 80) + 7):
            numeral = str(value) if value >= 0 else '(- ' + str(-value) + ')'
            self.equivalent(smt('(declare-const x Int)', '', '(+ (* ' + numeral + ' x) 11)'), modes=((False, False),))
        huge = str(1 << 130)
        self.equivalent(smt('(declare-const x Int)', '', '(+ (* ' + huge + ' x) (* (- ' + huge + ') x) 9)'))

    def test_sparse_alias_budget_and_reserved_names(self):
        names = ['x' + str(i) for i in range(66)]
        decl = ''.join('(declare-const ' + n + ' Int)' for n in names + ['__nia_omt_aux_0'])
        target = '(* (+ ' + ' '.join(names[:33]) + ') (+ ' + ' '.join(names[33:]) + '))'
        self.equivalent(smt(decl, '(assert (>= __nia_omt_aux_0 1))', target), modes=((False, False), (True, True)))

    def test_fresh_boolean_names_do_not_merge_source_declarations(self):
        self.equivalent(smt('(declare-const |k!0| Bool)(declare-const b Bool)(declare-const c Bool)(declare-const x Int)',
            '(assert (or (and b c) (and |k!0| (< x 0))))', '(ite b x (- x))'))

    def test_invalid_and_unsupported_sources_match_rejection(self):
        cases = [
            '(declare-const x Int)(minimize x)(maximize x)(check-sat)',
            '(declare-const x Int)(minimize x)(check-sat)(push 1)',
            '(declare-const x Int)(minimize x)(check-sat)(set-option :timeout 1)',
            '(declare-const x Int)(minimize x)(check-sat)(check-sat)',
            '(declare-const x Int)(assert (forall ((y Int)) (> y x)))(minimize x)',
            '(declare-const x Real)(minimize x)',
            '(declare-fun f (Int) Int)(declare-const x Int)(minimize (f x))',
            '(declare-const x Int)(minimize (div x 2))',
            '(declare-const x (_ BitVec 8))(minimize (bv2int x))',
            '(declare-const x Int)(minimize (* ' + str(1 << 127) + ' x))',
            '(declare-const x Int)(minimize (- ' + str((1 << 127) + 1) + '))',
            '(declare-const |unterminated Int)(minimize 0)',
            '(set-info :source "unterminated)(minimize 0)',
            '(declare-const x Int)(assert (>= x 0))',
        ]
        for source in cases:
            with self.subTest(source=source):
                native, reference = self.pair(source, False, False)
                self.assertNotEqual(reference.returncode, 0, reference.stdout)
                self.assertNotEqual(native.returncode, 0, native.stdout)
                self.assertEqual(native.stdout, '')

    @unittest.skipUnless(os.environ.get('NIA_NATIVE_IMPORT_DATASET_SMOKE') == '1', 'optional read-only dataset parity')
    def test_original_and_new_family_representatives(self):
        for family, path in dataset_representatives():
            with self.subTest(family=family, source=str(path)):
                self.equivalent(path.read_text(), modes=((True, False), (True, True)))


if __name__ == '__main__':
    unittest.main(verbosity=2)
