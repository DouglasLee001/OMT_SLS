#!/usr/bin/env python3
"""File-based cleanup-recovery tests; never run a solver or submit a job."""
import csv
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / 'experiment/recover_completed_worker.py'
HELPER = ROOT / 'experiment/embedding_experiment.py'


def load(path, name):
    assert path.is_file(), f'missing implementation: {path.name}'
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.m = load(MODULE, 'recover_worker_under_test')
        self.h = load(HELPER, 'recovery_fixture_helper')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name) / 'run'; self.output.mkdir()
        self.task_id = 'full-baseline-02'
        self.where = self.output / 'tasks' / self.task_id
        self.run = self.where / 'run'; self.run.mkdir(parents=True)
        script = self.output / 'source/reference/script'; script.mkdir(parents=True)
        for name in ('run_optimize.py', 'run_optimize_slurm.py', 'smtlib.py', 'slurm_snapshot.py'):
            shutil.copy2(ROOT / 'reference/script' / name, script / name)
        frozen_helper = self.output / 'source/harness/embedding_experiment.py'
        frozen_helper.parent.mkdir(parents=True); shutil.copy2(HELPER, frozen_helper)
        executable = self.output / 'source/baseline/solver'; executable.parent.mkdir()
        executable.write_bytes(b'not executed by recovery')
        converter = executable.with_name('converter.py'); converter.write_text('# not executed\n')
        self.cases = []
        for i in range(2):
            source = Path(self.temp.name) / f'case{i}.smt2'
            source.write_text('(set-logic QF_NIA)\n(assert true)\n')
            self.cases.append(dict(source=str(source), filename=source.name, suite='new', category='test',
                                   direction='min', z3=None, optimathsat=None,
                                   known_optimum_source='', known_optimum_value=None))
        self.task = dict(id=self.task_id, phase='full', variant='baseline', indices=[0, 1])
        self.plan = dict(id='fixture', output=str(self.output), baseline_commit=self.h.COMMIT,
                         workers=80, timeout=1200, nodes=['node4'], tasks=[self.task],
                         config=dict(self.h.FULL_CONFIG), cases=self.cases,
                         seed_defaults=dict(explicit_seed=None, mt=0, lia_seed=0),
                         variants=dict(baseline=dict(executable=str(executable),
                                                     executable_sha256=self.h.digest(executable))),
                         converter=str(converter), converter_sha256=self.h.digest(converter),
                         python=str(Path(sys.executable).resolve()), python_sha256=self.h.digest(sys.executable),
                         launcher=str(frozen_helper), reference_script=str(script),
                         benchmark_hashes={c['source']: self.h.digest(c['source']) for c in self.cases},
                         files={str(p.relative_to(self.output)): self.h.digest(p)
                                for p in (self.output / 'source').rglob('*') if p.is_file()})
        self.write_plan()
        common = dict(job_id='87371', task_id=self.task_id)
        self.write_json(self.where / 'intent.json', dict(plan_id=self.plan['id'], plan_sha256=self.plan_sha,
                        task_id=self.task_id, node='node4', workers=80, comment='z3nia:fixture:'+self.task_id))
        self.write_json(self.where / 'receipt.json', dict(job_id='87371'))
        self.write_json(self.where / 'worker_started.json', dict(**common, node='node4', plan_sha256=self.plan_sha))
        self.local = dict(self.h.config_manifest_values(self.plan['config']), cases=self.cases,
                          solver=str(executable), converter=str(converter),
                          solver_sha256=self.h.digest(executable), converter_sha256=self.h.digest(converter),
                          started_utc='2026-10-29T01:00:00+00:00')
        self.write_json(self.run / 'manifest.json', self.local)
        self.runner, _ = self.h._reference_modules(script)
        self.rows = []
        for case in self.cases:
            row = {key: '' for key in self.runner.FIELDS}
            row.update(filename=case['filename'], suite='new', category='test', direction='min',
                       status='sat', termination='wall_timeout', best_value='1', model_validated='true',
                       is_optimal='false', is_unbounded='false', strictly_better_than_best='false',
                       vs_z3='no_reference', vs_optimathsat='no_reference', vs_best='no_reference')
            self.rows.append(row)
        self.write_rows()
        (self.where / 'slurm-87371.err').write_text(
            "embedding experiment: [Errno 39] Directory not empty: '/home/lbh/.tmp/nia-opt-run-abc_123'\n")
        self.queue = patch.object(self.m, 'queue_snapshot', return_value=[])
        self.queue.start(); self.addCleanup(self.queue.stop)

    def write_json(self, path, value):
        path.write_text(json.dumps(value, indent=2)+'\n')

    def write_plan(self):
        self.write_json(self.output / 'manifest.json', self.plan)
        self.plan_sha = self.h.digest(self.output / 'manifest.json')
        (self.output / 'manifest.sha256').write_text(self.plan_sha+'\n')

    def write_rows(self):
        with (self.run / 'results.csv').open('w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=self.runner.FIELDS); writer.writeheader(); writer.writerows(self.rows)
        lines = []
        for i, r in enumerate(self.rows, 1):
            detail = (f"unbounded_proved {r['best_value']}" if r['is_unbounded'] == 'true' else
                      f"{r['status']} {r['best_value']} {r['vs_best']}")
            lines.append(f"[{i}/{len(self.cases)}] {detail}: {r['filename']}")
        (self.where / 'slurm-87371.out').write_text('\n'.join(lines)+'\n')

    def check(self):
        return self.m.check(self.output, self.task_id)

    def test_check_is_read_only_and_derives_only_post_cleanup_returncode(self):
        before = {str(p): p.read_bytes() for p in self.output.rglob('*') if p.is_file()}
        result = self.check()
        self.assertEqual(result['original_worker_outcome'], 'post-run-cleanup-error')
        self.assertEqual(result['post_cleanup_returncode'], 0)
        self.assertEqual(result['completed_cases'], 2)
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.output.rglob('*') if p.is_file()})

    def test_check_disables_bytecode_writes_even_without_python_B(self):
        before = {str(p): p.read_bytes() for p in self.output.rglob('*') if p.is_file()}
        with patch.object(sys, 'dont_write_bytecode', False):
            self.check()
            self.assertTrue(sys.dont_write_bytecode)
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.output.rglob('*') if p.is_file()})

    def test_apply_retains_original_error_and_never_changes_csv_or_frozen_files(self):
        old_csv = (self.run / 'results.csv').read_bytes()
        old_manifest = (self.run / 'manifest.json').read_bytes()
        frozen = {p: p.read_bytes() for p in (self.output / 'source').rglob('*') if p.is_file()}
        self.m.recover(self.output, self.task_id)
        done = json.loads((self.where / 'worker_done.json').read_text())
        self.assertEqual(done['kind'], 'recovered')
        self.assertEqual(done['original_worker_outcome'], 'post-run-cleanup-error')
        self.assertEqual(done['returncode'], 0)
        self.assertEqual((self.run / 'manifest.before-recovery.json').read_bytes(), old_manifest)
        self.assertEqual((self.run / 'results.csv').read_bytes(), old_csv)
        self.assertEqual(frozen, {p: p.read_bytes() for p in frozen})
        self.assertTrue((self.run / 'summary.json').exists())
        self.h.validate_local_manifest(self.plan, self.task, json.loads((self.run / 'manifest.json').read_text()))
        record = json.loads((self.where / 'cleanup_recovery.json').read_text())
        self.assertEqual(record['original_worker_outcome'], 'post-run-cleanup-error')
        with self.assertRaises(ValueError): self.m.recover(self.output, self.task_id)

    def test_rejects_incomplete_csv_even_with_fake_last_stdout(self):
        self.rows.pop(); self.write_rows()
        with self.assertRaises(ValueError): self.check()

    def test_rejects_unknown_or_additional_stderr(self):
        for text in ['out of memory\n', (self.where/'slurm-87371.err').read_text()+'other error\n']:
            (self.where/'slurm-87371.err').write_text(text)
            with self.subTest(text=text), self.assertRaises(ValueError): self.check()

    def test_rejects_job_id_or_comment_still_present_in_queue(self):
        for job in [dict(job_id='87371', comment='different'),
                    dict(job_id='99999', comment='z3nia:fixture:'+self.task_id)]:
            with self.subTest(job=job), patch.object(self.m, 'queue_snapshot', return_value=[job]), self.assertRaises(ValueError):
                self.check()

    def test_rejects_wrong_config_and_does_not_write(self):
        self.local['timeout'] = 10
        self.write_json(self.run/'manifest.json', self.local)
        with self.assertRaises(ValueError): self.check()
        self.assertFalse((self.where/'cleanup_recovery.json').exists())

    def test_rejects_changed_executable_and_input_hash(self):
        for target in [Path(self.plan['variants']['baseline']['executable']), Path(self.cases[0]['source'])]:
            before = target.read_bytes(); target.write_bytes(before+b'changed')
            with self.subTest(target=target), self.assertRaises(ValueError): self.check()
            target.write_bytes(before)

    def test_rejects_stdout_without_strict_per_row_alignment(self):
        out = self.where/'slurm-87371.out'; original = out.read_text()
        for text in [original.splitlines()[-1]+'\n', original.replace('sat 1', 'sat 2', 1)]:
            out.write_text(text)
            with self.subTest(text=text), self.assertRaises(ValueError): self.check()

    def test_rejects_interrupted_duplicate_foreign_or_direction_mismatch(self):
        original = list(self.rows)
        for change in [dict(status='interrupted'), dict(filename='foreign.smt2'), dict(direction='max')]:
            self.rows = [dict(original[0], **change), dict(original[1])]; self.write_rows()
            with self.subTest(change=change), self.assertRaises(ValueError): self.check()
        self.rows = [dict(original[0]), dict(original[0])]; self.write_rows()
        with self.assertRaises(ValueError): self.check()

    def test_rejects_mismatched_start_or_receipt(self):
        self.write_json(self.where/'receipt.json', dict(job_id='87372'))
        with self.assertRaises(ValueError): self.check()

    def test_rejects_existing_completion(self):
        self.write_json(self.where/'worker_done.json', dict(returncode=0))
        with self.assertRaises(ValueError): self.check()

    def test_checks_frozen_runner_return_contract(self):
        path = Path(self.plan['reference_script'])/'run_optimize.py'
        path.write_text(path.read_text().replace("return 130 if len(rows) < len(cases)", "return 131 if len(rows) < len(cases)"))
        self.plan['files'][str(path.relative_to(self.output))] = self.h.digest(path)
        self.write_plan()
        for name in ['intent.json', 'worker_started.json']:
            p=self.where/name; value=json.loads(p.read_text()); value['plan_sha256']=self.plan_sha; self.write_json(p,value)
        with self.assertRaises(ValueError): self.check()


if __name__ == '__main__':
    unittest.main()
