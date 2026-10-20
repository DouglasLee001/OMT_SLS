import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(SCRIPT))
import run_new


def source(sense='minimize',logic='QF_NIA'):
    return (f'(set-logic {logic})\n(declare-fun x () Int)\n'
            '(declare-fun cost () Int)\n(assert (and (>= x 0) (<= x 4)))\n'
            f'(assert (= cost (* x x)))\n({sense} cost)\n(check-sat)\n')


class NewSuiteTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='omt-new-run-test-')
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        for family in run_new.FAMILIES:
            p=self.root/family/'instance/a.smt2'
            p.parent.mkdir(parents=True)
            p.write_text(source('maximize' if family=='design' else 'minimize'))
            (self.root/family/'src').mkdir()

    def test_all_flat_inputs_have_correct_family_theory_and_sense(self):
        inputs,tags=run_new.collect_inputs(self.root,run_new.FAMILIES)
        self.assertEqual(len(inputs),4)
        self.assertEqual(tags['design/instance/a.smt2']['objective_sense'],'maximize')
        self.assertEqual(tags['packing/instance/a.smt2']['objective_sense'],'minimize')
        self.assertTrue(all(t['theory']=='NIA' for t in tags.values()))
        self.assertEqual(tags['cyclic/instance/a.smt2']['family'],'cyclic')

    def test_new_only_selection_and_scale_tier_mapping(self):
        listing=self.root/'packing/src/scaled_instances.txt'
        listing.write_text('packing/instance/a.smt2\n')
        (listing.parent/'scaled_tiers.json').write_text(json.dumps({'tiers':[{
            'tier':'medium','instances':['packing/instance/a.smt2']}]}))
        (self.root/'packing/instance/b.smt2').write_text(source())
        inputs,tags=run_new.collect_inputs(self.root,('packing',),new_only=True)
        self.assertEqual(inputs,['packing/instance/a.smt2'])
        self.assertEqual(tags[inputs[0]]['scale_tier'],'medium')

    def test_preflight_rejects_wrong_theory_multiple_objectives_and_escape(self):
        p=self.root/'packing/instance/a.smt2'
        for invalid in (source(logic='QF_NRA'),source()+'(maximize x)\n'):
            p.write_text(invalid)
            with self.assertRaises(ValueError):
                run_new.collect_inputs(self.root,('packing',))
        p.unlink()
        outside=self.root.parent/(self.root.name+'-outside.smt2')
        outside.write_text(source())
        self.addCleanup(lambda:outside.unlink(missing_ok=True))
        p.symlink_to(outside)
        with self.assertRaises(ValueError):
            run_new.collect_inputs(self.root,('packing',))

    def test_default_budget_and_dry_run_cannot_launch_workers_or_write_results(self):
        with patch.dict(os.environ,{'JOBS':'','TIMEOUT':'','RESULT_ROOT':''}):
            config=run_new.make_config('z3',self.root,{})
        self.assertEqual(config.jobs,180)
        self.assertEqual(config.timeout,1200)
        with patch.object(run_new,'BENCHMARK_ROOT',self.root), \
             patch.object(run_new.runner,'run_batch',side_effect=AssertionError('must not run')), \
             patch.object(run_new.runner,'create_run_directory',side_effect=AssertionError('must not write')), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run_new.main(['z3','--dry-run']),0)

    def test_new_only_missing_manifest_is_an_error(self):
        with self.assertRaises(ValueError):
            run_new.collect_inputs(self.root,('cyclic',),new_only=True)

if __name__=='__main__':
    unittest.main()
