import contextlib
import io
import json
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from nia_bench import cli, core


class CliTests(unittest.TestCase):
    def test_one_command_generates_requested_number_of_flat_smt_files(self):
        with tempfile.TemporaryDirectory() as folder:
            entry=core.ROOT/'packing/src/generate.py'
            command=[sys.executable,str(entry),'--profile','rectangles','--items','4',
                     '--max-side','8','--seed','100','--count','3','--out',folder]
            first=subprocess.run(command,capture_output=True,text=True,cwd=folder)
            self.assertEqual(first.returncode,0,first.stdout+first.stderr)
            files=sorted((Path(folder)/'packing/instance').iterdir())
            self.assertEqual(len(files),3)
            self.assertTrue(all(p.is_file() and p.suffix=='.smt2' for p in files))
            contents={p.name:p.read_bytes() for p in files}
            second=subprocess.run(command,capture_output=True,text=True,cwd=folder)
            self.assertEqual(second.returncode,0,second.stdout+second.stderr)
            self.assertEqual(contents,{p.name:p.read_bytes() for p in files})
            self.assertFalse(list(Path(folder).rglob('z3')))

    def test_count_rejects_zero_and_nonrandom_profile_without_creating_instances(self):
        for profile,count in (('rectangles','0'),('squares','2')):
            with self.subTest(profile=profile), tempfile.TemporaryDirectory() as folder:
                result=subprocess.run([sys.executable,str(core.ROOT/'packing/src/generate.py'),
                    '--profile',profile,'--count',count,'--out',folder],capture_output=True,text=True)
                self.assertNotEqual(result.returncode,0)
                self.assertEqual(list(Path(folder).rglob('*.smt2')),[])

    def test_encoding_primary_is_stable_across_incremental_generation_orders(self):
        cb = {'family':'design','profile':'tiny','encoding':'cauchy-binet'}
        det = {'family':'design','profile':'tiny','encoding':'determinant'}
        for initial in ([cb,det],[det]):
            with self.subTest(initial=initial), tempfile.TemporaryDirectory() as folder, contextlib.redirect_stdout(io.StringIO()):
                cli.generate_specs(initial,Path(folder))
                cli.generate_specs([det,cb],Path(folder))
                final = cli.generate_specs([cb,det],Path(folder))
                self.assertEqual(final['total'],2)
                self.assertEqual(final['new'],1)
                self.assertEqual(final['encoding_variants'],1)

    def test_alternate_encoding_is_retained_without_counting_new_application(self):
        with tempfile.TemporaryDirectory() as folder, contextlib.redirect_stdout(io.StringIO()):
            report = cli.generate_specs([
                {'family':'design','profile':'tiny','encoding':'cauchy-binet'},
                {'family':'design','profile':'tiny','encoding':'determinant'}],Path(folder))
            self.assertEqual(report['total'],2)
            self.assertEqual(report['new'],1)
            self.assertEqual(len({case['application_instance_id'] for case in report['cases']}),1)

    def test_float_claimed_objective_is_rejected(self):
        module = core.load_family('packing')
        case = module.make('squares', n=3)[0]
        with tempfile.TemporaryDirectory() as folder:
            record = core.write_case('packing',case,module,Path(folder))
            path = Path(record['path'])
            candidate = case['witness']
            candidate['cost'] = 15.75
            solution = Path(folder)/'candidate.json'
            solution.write_text(json.dumps(candidate))
            with self.assertRaises(ValueError):
                cli.check_solution(path,solution)

    def test_optimized_python_cannot_disable_witness_cost_validation(self):
        module = core.load_family('packing')
        case = module.make('squares', n=3)[0]
        with tempfile.TemporaryDirectory() as folder:
            record = core.write_case('packing',case,module,Path(folder))
            path = Path(record['path'])
            wrong = json.loads((path/'witness.json').read_text())
            wrong['cost'] = '999'
            (path/'witness.json').write_text(json.dumps(wrong))
            result = subprocess.run([sys.executable,'-O',str(core.ROOT/'packing/src/manage.py'),'validate',str(path)],capture_output=True,text=True)
            self.assertEqual(result.returncode,1,result.stdout+result.stderr)

    def test_normalized_application_is_not_duplicated_under_an_alias(self):
        module = core.load_family('packing')
        case = module.make('squares', n=3)[0]
        case['label'] = 'already-existing-alias'
        with tempfile.TemporaryDirectory() as folder:
            core.write_case('packing',case,module,Path(folder))
            with contextlib.redirect_stdout(io.StringIO()):
                report = cli.generate_specs([{'family':'packing','profile':'squares','n':3}],Path(folder))
            self.assertEqual(report['total'],1)
            self.assertEqual(len(report['duplicate_parameters_skipped']),1)

    def test_external_application_solution_rejects_bad_geometry(self):
        module = core.load_family('packing')
        case = module.make('squares', n=3)[0]
        with tempfile.TemporaryDirectory() as folder:
            record = core.write_case('packing',case,module,Path(folder))
            solution = Path(folder)/'decision.json'
            solution.write_text(json.dumps(case['witness']))
            self.assertEqual(cli.check_solution(Path(record['path']),solution)['cost'],'15')
            bad = case['witness']
            bad['decision']['positions'][0] = [0,0]
            solution.write_text(json.dumps(bad))
            with self.assertRaises(ValueError):
                cli.check_solution(Path(record['path']),solution)


if __name__ == '__main__':
    unittest.main()
