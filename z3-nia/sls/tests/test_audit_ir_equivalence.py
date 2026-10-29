#!/usr/bin/env python3
"""Small independent checks for the IR projection certificate, no SMT solver."""
import csv
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

MODULE = Path(__file__).resolve().parents[2] / 'experiment/audit_ir_equivalence.py'
spec = importlib.util.spec_from_file_location('ir_equivalence_auditor', MODULE)
auditor = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = auditor
spec.loader.exec_module(auditor)


def lit(name, sign=1):
    return sign, ('B', name) if name.startswith('k!') else ('A', name)


def gate(name, children):
    """k! names encode OR(children), with its complete iff clauses."""
    return [frozenset([lit(name, -1), *children]),
            *[frozenset([lit(name), (-s, atom)]) for s, atom in children]]


class ProjectionTests(unittest.TestCase):
    def test_different_distributions_have_equal_existential_projection(self):
        # (a & b) | (c & d & e), distributing opposite branches.
        left = gate('k!1', [lit('a', -1), lit('b', -1)])
        left += [frozenset([lit('k!1', -1), lit(x)]) for x in ['c', 'd', 'e']]
        right = gate('k!9', [lit(x, -1) for x in ['c', 'd', 'e']])
        right += [frozenset([lit('k!9', -1), lit(x)]) for x in ['a', 'b']]
        dag = auditor.BooleanDAG()
        a = auditor.project_boolean_definitions(left, {'k!1'}, dag)
        b = auditor.project_boolean_definitions(right, {'k!9'}, dag)
        self.assertEqual(a['root'], b['root'])
        self.assertEqual(a['factor_steps'], 2)
        self.assertEqual(b['factor_steps'], 1)

    def test_different_formula_is_not_certified_equal(self):
        dag = auditor.BooleanDAG()
        a = auditor.project_boolean_definitions([frozenset([lit('a')])], set(), dag)
        b = auditor.project_boolean_definitions([frozenset([lit('b')])], set(), dag)
        self.assertNotEqual(a['root'], b['root'])

    def test_missing_iff_half_is_rejected(self):
        clauses = gate('k!1', [lit('a'), lit('b')])[:-1]
        with self.assertRaisesRegex(ValueError, 'complete Boolean definitions'):
            auditor.project_boolean_definitions(clauses, {'k!1'}, auditor.BooleanDAG())

    def test_even_unused_definition_cycles_are_rejected(self):
        clauses = gate('k!1', [lit('k!2'), lit('a')])
        clauses += gate('k!2', [lit('k!1'), lit('b')])
        clauses += [frozenset([lit('c')])]
        with self.assertRaisesRegex(ValueError, 'cyclic Boolean definitions'):
            auditor.project_boolean_definitions(clauses, {'k!1', 'k!2'}, auditor.BooleanDAG())


class ByteAuditBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.run = Path(self.temp.name)
        (self.run / 'verification').mkdir()
        self.cases = [dict(source=str(self.run / f'case{i}.smt2'), filename=f'case{i}.smt2')
                      for i in range(6310)]
        self.plan = dict(cases=self.cases, tasks=[
            dict(id='full-' + variant, phase='full', variant=variant, indices=list(range(6310)))
            for variant in ('baseline', 'embedded')])
        manifest = self.run / 'manifest.json'
        manifest.write_text(json.dumps(self.plan))
        (self.run / 'manifest.sha256').write_text(auditor.digest(manifest) + '\n')
        self.rows = [dict(index=i, encoding='identical', baseline_ir_sha256='', embedded_ir_sha256='')
                     for i in range(6310)]
        self.write_rows()

    def write_rows(self):
        with (self.run / 'verification/full-native-ir.csv').open('w', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=list(self.rows[0]))
            writer.writeheader()
            writer.writerows(self.rows)

    def native_path(self, variant, index):
        case = self.cases[index]
        name = f'{index:04d}-' + auditor.hashlib.sha256(case['source'].encode()).hexdigest()[:12]
        return self.run / 'tasks' / ('full-' + variant) / 'run/cases' / name / 'input.nia'

    def test_identical_labels_with_empty_hashes_and_missing_ir_cannot_certify(self):
        result = auditor.audit(self.run)
        self.assertFalse(result['all_equivalent'])
        self.assertEqual(result['counts']['byte_identical'], 0)

    def test_identical_label_with_current_different_files_cannot_certify(self):
        for variant, constant in [('baseline', 0), ('embedded', 1)]:
            path = self.native_path(variant, 0)
            path.parent.mkdir(parents=True)
            path.write_text('NIA_LS_OMT 1\nSTATUS ok\nSOURCE 2f746d70\n'
                            'ORIGINAL_OBJECTIVE 30\nDIRECTION min\nVARIABLES 0\n'
                            f'LITERALS 0\nCLAUSES 0\nOBJECTIVE {constant} 0\nEND\n')
            self.rows[0][variant + '_ir_sha256'] = auditor.digest(path)
        self.write_rows()
        result = auditor.audit(self.run)
        self.assertFalse(result['all_equivalent'])
        first = next(c for c in result['certificates'] if c['index'] == 0)
        self.assertFalse(first['equivalent'])
        self.assertIn('identical byte-audit label disagrees with current bytes', first['error'])


if __name__ == '__main__':
    unittest.main()
