#!/usr/bin/env python3
"""No solver, Slurm submission or watcher is executed by this test suite."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / 'experiment/embedding_experiment.py'
ADAPTER = ROOT / 'experiment/runtime_adapter.py'


def load(path, name):
    assert path.is_file(), f'missing implementation: {path.name}'
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class ExperimentTests(unittest.TestCase):
    def setUp(self):
        self.m = load(MODULE, 'embedding_experiment_under_test')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def node(self, **changes):
        values = dict(NodeName='node1', State='MIXED', CPUTot='192',
                      CPUEfctv='180', CPUAlloc='100', RealMemory='360000', AllocMem='180000')
        values.update(changes)
        return ' '.join(f'{key}={value}' for key, value in values.items())

    def test_mixed_effective_capacity_and_proportional_memory(self):
        node = self.m.admit_node(self.node(), 'node1')
        self.assertEqual(node['free_cpus'], 80)
        self.assertEqual(node['memory_mb'], 160000)
        self.assertEqual(node['workers'], 80)

    def test_resource_and_health_fail_closed(self):
        for change in [dict(CPUAlloc='101'), dict(State='IDLE+DRAIN'),
                       dict(State='DOWN'), dict(CPUEfctv='N/A'),
                       dict(AllocMem='200001'), dict(NodeName='node2'),
                       dict(RealMemory='0')]:
            with self.subTest(change=change):
                self.assertIsNone(self.m.admit_node(self.node(**change), 'node1'))
        self.assertIsNone(self.m.admit_node(self.node(), 'node6'))

    def test_sbatch_is_shared_with_explicit_memory(self):
        command = self.m.sbatch_command(self.root, 'regression-baseline-00',
                                       self.m.admit_node(self.node(), 'node1'), 5, 'abc')
        self.assertIn('--cpus-per-task=80', command)
        self.assertIn('--mem=160000M', command)
        self.assertIn('--nodelist=node1', command)
        self.assertNotIn('--exclusive', command)
        self.assertNotIn('--mem=0', command)

    def test_stratified_regression_is_deterministic_and_keeps_full_order(self):
        cases = [dict(suite='new', category='a' if i < 4 else 'b', direction='min',
                      known_optimum_value=None, z3='1', optimathsat=None,
                      source=f'/source/{i}.smt2', filename=f'{i}.smt2') for i in range(8)]
        chosen = self.m.regression_indices(cases, 2)
        self.assertEqual(chosen, self.m.regression_indices(cases, 2))
        self.assertEqual(len(chosen), 4)
        self.assertEqual(chosen, sorted(chosen))
        self.assertEqual({cases[i]['category'] for i in chosen}, {'a', 'b'})

    def test_original_regression_strata_use_first_qf_nia_family(self):
        cases = [dict(suite='original', category='original', direction='min', known_optimum_value=None,
                      z3=None, optimathsat=None, source=f'/source/{family}/{i}.smt2',
                      filename=f'QF_NIA/{family}/{i}.smt2')
                 for family in ('leipzig', 'VeryMax', 'Arithmetic') for i in range(4)]
        selected = self.m.regression_indices(cases, 1)
        self.assertEqual(len(selected), 3)
        self.assertEqual({cases[i]['filename'].split('/')[1] for i in selected},
                         {'leipzig', 'VeryMax', 'Arithmetic'})

    def test_forced_regression_keeps_1058_and_leipzig_or_errors(self):
        self.assertTrue(hasattr(self.m, 'forced_regression_indices'), 'missing mandatory regression cases')
        cases = [dict(suite='new', category='portfolio', source='/portfolio/1058.smt2',
                      filename='gram-m12-q6-s1058--a34080af91dd.smt2'),
                 dict(suite='original', category='original', source='/original/example.smt2',
                      filename='QF_NIA/leipzig/example.smt2')]
        self.assertEqual(self.m.forced_regression_indices(cases), [0, 1])
        with self.assertRaisesRegex(ValueError, '1058'):
            self.m.forced_regression_indices(cases[1:])

    def test_exact_paired_states_and_objectives(self):
        def row(state='B', value='5', direction='min'):
            return dict(status='sat' if state != 'C' else 'timeout', model_validated='true',
                        best_value=value if state != 'C' else '', direction=direction,
                        is_optimal=str(state == 'A2').lower(), is_unbounded=str(state == 'A1').lower(),
                        termination='timeout', error='')
        self.assertEqual(self.m.result_state(row('A1')), 'A1')
        self.assertEqual(self.m.result_state(row('A2')), 'A2')
        self.assertEqual(self.m.result_state(row('B')), 'B')
        self.assertEqual(self.m.result_state(row('C')), 'C')
        huge = 10**150
        self.assertEqual(self.m.compare_pair(row(value=str(huge)), row(value=str(huge - 1))), 'win')
        self.assertEqual(self.m.compare_pair(row('A1'), row(value='-999999')), 'loss')
        self.assertEqual(self.m.compare_pair(row('C'), row()), 'win')

    def test_freeze_detects_changed_runtime_and_symlink(self):
        source = self.root / 'original'; source.write_text('one')
        frozen = self.root / 'run'; frozen.mkdir()
        files = {}
        self.m.freeze_file(source, frozen, 'source/data', files)
        self.m.verify_files(frozen, files)
        (frozen / 'source/data').write_text('two')
        with self.assertRaises(ValueError): self.m.verify_files(frozen, files)
        (frozen / 'source/data').unlink()
        (frozen / 'source/data').symlink_to(source)
        with self.assertRaises(ValueError): self.m.verify_files(frozen, files)

    def task_fixture(self):
        task = dict(id='regression-baseline-00', phase='regression', variant='baseline', indices=[0])
        plan = dict(id='abc', workers=80, timeout=1200, partition='all', tasks=[task],
                    nodes=['node1'], files={}, benchmark_hashes={}, config={}, variants={},
                    launcher=str(MODULE))
        (self.root / 'tasks' / task['id']).mkdir(parents=True)
        return plan, task

    def test_busy_never_submits(self):
        plan, task = self.task_fixture()
        with patch.object(self.m, 'query_node', return_value=None), \
             patch.object(self.m, 'verify_plan'), \
             patch.object(self.m.subprocess, 'run', side_effect=AssertionError('no sbatch')):
            self.assertFalse(self.m.attempt_submit(self.root, plan, task, 'node1'))
        self.assertFalse((self.root / 'tasks' / task['id'] / 'intent.json').exists())

    def test_final_idle_check_precedes_durable_intent_and_sbatch(self):
        plan, task = self.task_fixture()
        node = self.m.admit_node(self.node(), 'node1')
        events = []
        def query(_node): events.append('node'); return node
        def submit(command, **kwargs):
            events.append('submit')
            intent = self.root / 'tasks' / task['id'] / 'intent.json'
            self.assertTrue(intent.exists())
            self.assertEqual(json.loads(intent.read_text())['node'], 'node1')
            self.assertEqual(events[-2], 'node')
            return subprocess.CompletedProcess(command, 0, '123;cluster\n', '')
        with patch.object(self.m, 'verify_plan', side_effect=lambda *x: events.append('verify')), \
             patch.object(self.m, 'query_node', side_effect=query), \
             patch.object(self.m.subprocess, 'run', side_effect=submit):
            self.assertTrue(self.m.attempt_submit(self.root, plan, task, 'node1'))
        receipt = json.loads((self.root / 'tasks' / task['id'] / 'receipt.json').read_text())
        self.assertEqual(receipt['job_id'], '123')

    def test_timeout_leaves_intent_and_never_resubmits(self):
        plan, task = self.task_fixture()
        node = self.m.admit_node(self.node(), 'node1')
        with patch.object(self.m, 'verify_plan'), patch.object(self.m, 'query_node', return_value=node), \
             patch.object(self.m.subprocess, 'run', side_effect=subprocess.TimeoutExpired('sbatch', 30)) as call:
            self.assertFalse(self.m.attempt_submit(self.root, plan, task, 'node1'))
            self.assertFalse(self.m.attempt_submit(self.root, plan, task, 'node1'))
            self.assertEqual(call.call_count, 1)
        self.assertTrue((self.root / 'tasks' / task['id'] / 'intent.json').exists())

    def test_worker_placement_validation_does_not_require_receipt(self):
        intent = dict(node='node1', workers=80, memory_mb=160000, plan_id='abc')
        env = dict(SLURMD_NODENAME='node1', SLURM_CPUS_PER_TASK='80', SLURM_NTASKS='1',
                   SLURM_LOCALID='0', SLURM_JOB_ID='123', SLURM_MEM_PER_NODE='160000')
        self.m.validate_worker(intent, env, 'node1')
        with self.assertRaises(ValueError): self.m.validate_worker(intent, dict(env, SLURM_CPUS_PER_TASK='79'), 'node1')
        with self.assertRaises(ValueError): self.m.validate_worker(intent, env, 'node2')

    def test_receipt_recovery_uses_exact_comment_and_never_retries(self):
        plan, task = self.task_fixture()
        where = self.root / 'tasks' / task['id']
        (where / 'intent.json').write_text(json.dumps(dict(node='node1', comment='z3nia:abc:regression-baseline-00')))
        self.m.recover_receipt(self.root, plan, task, [dict(job_id='42', comment='z3nia:abc:regression-baseline-00')])
        self.assertEqual(json.loads((where / 'receipt.json').read_text())['job_id'], '42')

    def test_full_gate_waits_then_rejects_regression_loss(self):
        self.assertEqual(self.m.gate_from_comparison(None)['state'], 'waiting')
        self.assertEqual(self.m.gate_from_comparison(dict(complete=True, losses=0, errors=0,
                                                        solved_delta=0))['state'], 'passed')
        self.assertEqual(self.m.gate_from_comparison(dict(complete=True, losses=1, errors=0,
                                                        solved_delta=1))['state'], 'failed')

    def test_missing_effective_cpu_or_memory_is_not_admitted(self):
        for key in ('CPUEfctv', 'AllocMem'):
            text = ' '.join(x for x in self.node().split() if not x.startswith(key + '='))
            self.assertIsNone(self.m.admit_node(text, 'node1'))

    def test_node_query_errors_mean_busy(self):
        for error in [subprocess.TimeoutExpired('scontrol', 15), subprocess.CalledProcessError(1, 'scontrol')]:
            with self.subTest(error=error), patch.object(self.m.subprocess, 'run', side_effect=error):
                self.assertIsNone(self.m.query_node('node1'))

    def test_final_node_became_busy_no_intent(self):
        plan, task = self.task_fixture()
        node = self.m.admit_node(self.node(), 'node1')
        with patch.object(self.m, 'verify_plan'), patch.object(self.m, 'query_node', side_effect=[node, None]), \
             patch.object(self.m.subprocess, 'run', side_effect=AssertionError('must not submit')):
            self.assertFalse(self.m.attempt_submit(self.root, plan, task, 'node1'))
        self.assertFalse((self.root / 'tasks' / task['id'] / 'intent.json').exists())

    def test_uncertain_intent_reserves_node_without_receipt(self):
        plan, task = self.task_fixture()
        where = self.root / 'tasks' / task['id']
        (where / 'intent.json').write_text(json.dumps(dict(node='node1')))
        result = self.m.status(self.root, plan)
        self.assertEqual(result['tasks'][0]['state'], 'submission_uncertain')
        self.assertEqual(result['tasks'][0]['node'], 'node1')

    def test_worker_started_recovers_lost_receipt(self):
        plan, task = self.task_fixture()
        where = self.root / 'tasks' / task['id']
        (where / 'intent.json').write_text(json.dumps(dict(node='node1', comment='exact')))
        (where / 'worker_started.json').write_text(json.dumps(dict(job_id='87')))
        self.m.recover_receipt(self.root, plan, task, [])
        self.assertEqual(json.loads((where / 'receipt.json').read_text())['job_id'], '87')

    def test_nonmatching_queue_comment_does_not_recover(self):
        plan, task = self.task_fixture()
        where = self.root / 'tasks' / task['id']
        (where / 'intent.json').write_text(json.dumps(dict(node='node1', comment='exact')))
        self.m.recover_receipt(self.root, plan, task, [dict(job_id='42', comment='exact-other')])
        self.assertFalse((where / 'receipt.json').exists())

    def test_watcher_automatically_continues_after_regression_gate(self):
        plan, task = self.task_fixture()
        full = dict(task, id='full-embedded-00', phase='full', variant='embedded')
        plan['tasks'] = [full]
        (self.root / 'tasks' / full['id']).mkdir()
        comparisons = dict(regression=dict(complete=True), full=dict(complete=False))
        passed = dict(state='passed')
        with patch.object(self.m, 'load_plan', return_value=plan), \
             patch.object(self.m, 'verify_plan'), patch.object(self.m, 'queue_jobs', return_value=[]), \
             patch.object(self.m, 'report', return_value=(comparisons, passed)), \
             patch.object(self.m, 'attempt_submit', return_value=True) as submit:
            self.assertEqual(self.m.watch(self.root, once=True), 0)
            self.assertEqual(submit.call_args.args[2]['phase'], 'full')

    def test_failed_regression_gate_submits_nothing(self):
        plan, _ = self.task_fixture()
        with patch.object(self.m, 'load_plan', return_value=plan), \
             patch.object(self.m, 'verify_plan'), patch.object(self.m, 'queue_jobs', return_value=[]), \
             patch.object(self.m, 'report', return_value=({}, dict(state='failed'))), \
             patch.object(self.m, 'attempt_submit', side_effect=AssertionError('gate failed')):
            self.assertEqual(self.m.watch(self.root, once=True), 2)

    def test_explicit_full_evidence_continues_after_failed_gate_and_records_it(self):
        plan, task = self.task_fixture()
        full = dict(task, id='full-embedded-00', phase='full', variant='embedded')
        plan['tasks'] = [full]
        (self.root / 'tasks' / full['id']).mkdir()
        comparisons = dict(regression=dict(complete=True), full=dict(complete=False))
        failed = dict(state='failed', reason='one objective loss')
        with patch.object(self.m, 'load_plan', return_value=plan), \
             patch.object(self.m, 'verify_plan'), patch.object(self.m, 'queue_jobs', return_value=[]), \
             patch.object(self.m, 'report', return_value=(comparisons, failed)), \
             patch.object(self.m, 'attempt_submit', return_value=True) as submit:
            self.assertEqual(self.m.watch(self.root, once=True, collect_full_evidence=True), 0)
        self.assertEqual(submit.call_args.args[2]['phase'], 'full')
        state = json.loads((self.root / 'state.json').read_text())
        self.assertEqual(state['regression_gate']['state'], 'failed')
        self.assertTrue(state['collect_full_evidence'])

    def test_explicit_evidence_completion_is_not_a_noninferiority_claim(self):
        plan, _ = self.task_fixture()
        comparisons = dict(regression=dict(complete=True), full=dict(complete=True))
        failed = dict(state='failed', reason='one objective loss')
        with patch.object(self.m, 'load_plan', return_value=plan), \
             patch.object(self.m, 'verify_plan'), patch.object(self.m, 'queue_jobs', return_value=[]), \
             patch.object(self.m, 'report', return_value=(comparisons, failed)), \
             patch.object(self.m, 'attempt_submit', side_effect=AssertionError('already complete')):
            self.assertEqual(self.m.watch(self.root, once=True, collect_full_evidence=True), 0)
        state = json.loads((self.root / 'state.json').read_text())
        self.assertEqual(state['state'], 'evaluation_complete')
        self.assertEqual(state['regression_gate']['state'], 'failed')

    def test_full_evaluation_incomplete_cannot_meet_aggregate_criteria(self):
        self.assertTrue(hasattr(self.m, 'full_evaluation'), 'missing separate full evaluation')
        result = self.m.full_evaluation(dict(complete=False))
        self.assertEqual(result['state'], 'incomplete')
        self.assertIsNone(result['meets_aggregate_criteria'])

    def test_full_evaluation_rejects_complete_subset_as_full_evidence(self):
        comparison = dict(complete=True, cases=52, wins=0, losses=0,
                          new_error_count=0, model_delta=0, solved_delta=0)
        result = self.m.full_evaluation(comparison)
        self.assertEqual(result['state'], 'incomplete')
        self.assertIsNone(result['meets_aggregate_criteria'])

    def test_aggregate_criteria_do_not_hide_new_error_or_metric_losses(self):
        self.assertTrue(hasattr(self.m, 'full_evaluation'), 'missing separate full evaluation')
        good = dict(complete=True, cases=6310, wins=2, ties=6306, losses=2,
                    errors=0, errors_by_variant=dict(baseline=0, embedded=0),
                    new_error_count=0, model_delta=0, solved_delta=0)
        result = self.m.full_evaluation(good)
        self.assertTrue(result['meets_aggregate_criteria'])
        self.assertFalse(result['strict_zero_objective_losses'])
        for change in [dict(new_error_count=1), dict(model_delta=-1),
                       dict(solved_delta=-1), dict(wins=1)]:
            with self.subTest(change=change):
                self.assertFalse(self.m.full_evaluation(dict(good, **change))['meets_aggregate_criteria'])

    def test_paired_summary_preserves_per_case_new_errors(self):
        def row(status):
            return dict(status=status, model_validated='false', best_value='', direction='min',
                        is_optimal='false', is_unbounded='false')
        summary = self.m.paired_summary({0: row('error'), 1: row('timeout')},
                                       {0: row('timeout'), 1: row('error')})
        self.assertEqual(summary['errors_by_variant'], dict(baseline=1, embedded=1))
        self.assertEqual(summary['new_error_count'], 1)

    def test_plan_detects_changed_benchmark(self):
        source = self.root / 'one.smt2'; source.write_text('first')
        plan = dict(files={}, python=sys.executable, python_sha256=self.m.digest(sys.executable),
                    cases=[dict(source=str(source))], benchmark_hashes={str(source): self.m.digest(source)})
        self.m.verify_plan(self.root, plan)
        source.write_text('second')
        with self.assertRaises(ValueError): self.m.verify_plan(self.root, plan)

    def test_worker_memory_and_job_allocation_are_verified(self):
        intent = dict(node='node1', workers=80, memory_mb=160000)
        env = dict(SLURMD_NODENAME='node1', SLURM_CPUS_PER_TASK='80', SLURM_NTASKS='1',
                   SLURM_JOB_ID='123', SLURM_MEM_PER_NODE='159999')
        with self.assertRaises(ValueError): self.m.validate_worker(intent, env, 'node1')

    def test_prepare_freezes_full_manifest_without_launching_anything(self):
        tree = self.root / 'tree'; workspace = self.root / 'workspace'
        reference = tree / 'reference'
        for path in [reference / 'script', reference / 'result', reference / 'NIA_LS/converter',
                     workspace / 'result', self.root / 'sdk/build/python/z3']:
            path.mkdir(parents=True)
        (reference / 'NIA_LS/converter/omt_to_ls.py').write_text('# converter\n')
        (workspace / 'result/competitor.csv').write_text('header\n')
        (self.root / 'sdk/build/python/z3/__init__.py').write_text('# SDK\n')
        library = self.root / 'sdk/build/libz3.so'; library.write_bytes(b'fake library')
        baseline = self.root / 'baseline'; baseline.write_bytes(b'fake baseline'); baseline.chmod(0o755)
        embedded = self.root / 'embedded'; embedded.write_bytes(b'fake embedded'); embedded.chmod(0o755)
        receipt = self.root / 'build.json'
        receipt.write_text(json.dumps(dict(git_commit=self.m.COMMIT, executable_sha256=self.m.digest(baseline))))
        (tree / 'embedding-build.json').write_text(json.dumps(dict(baseline_commit=self.m.COMMIT,
                                                                  executable_sha256=self.m.digest(embedded))))
        (tree / 'build.sh').write_text('# fake build, never executed\n')
        (tree / 'toolchain/gmp').mkdir(parents=True)
        (tree / 'toolchain/gmp/provenance.json').write_text('{}\n')
        cases = []
        for i in range(6310):
            source = self.root / 'cases' / f'{i}.smt2'; source.parent.mkdir(exist_ok=True)
            source.write_text('(assert true)\n')
            cases.append(dict(source=str(source), filename=source.name, suite='original' if i < 5744 else 'new',
                              category='original' if i < 5744 else 'toy', direction='min', z3=None,
                              optimathsat=None, known_optimum_value=None, known_optimum_source=''))
        cases[0]['filename'] = 'QF_NIA/leipzig/example.smt2'
        cases[-1].update(filename='gram-m12-q6-s1058--a34080af91dd.smt2', category='portfolio')
        manifest = self.root / 'cases.json'; manifest.write_text(json.dumps(dict(cases=cases)))
        args = SimpleNamespace(tree=tree, workspace=workspace, output=self.root / 'output',
                               baseline=baseline, embedded=embedded, baseline_build_record=receipt,
                               sdk=self.root / 'sdk', case_manifest=manifest, shards=6,
                               partition='all', regression_per_stratum=2)
        snapshot = SimpleNamespace(native_libraries=lambda _: {'libz3.so.5.1': library})
        fake_runner = SimpleNamespace(Config=lambda **kwargs: None)
        def baseline_archive(workspace, reference, target): Path(target).write_bytes(b'baseline source')
        def embedded_archive(tree, target): Path(target).write_bytes(b'embedded source')
        with patch.object(self.m, '_baseline_source_archive', side_effect=baseline_archive), \
             patch.object(self.m, '_source_archive', side_effect=embedded_archive), \
             patch.object(self.m, 'regression_indices', return_value=[*range(51), 6309]), \
             patch.object(self.m, '_reference_modules', return_value=(fake_runner, None)), \
             patch.object(self.m.importlib, 'import_module', return_value=snapshot), \
             patch.object(self.m.subprocess, 'run', side_effect=AssertionError('no external program')):
            output = self.m.prepare(args)
        plan = self.m.load_plan(output)
        self.assertEqual(len(plan['cases']), 6310)
        self.assertFalse(plan['baseline_reuse'])
        self.assertEqual(plan['seed_defaults'], dict(explicit_seed=None, mt=0, lia_seed=0))
        self.assertTrue(plan['config']['optimality_balanced'])
        self.assertIn('source/reference/result/competitor.csv', plan['files'])
        self.assertIn('source/embedded-source.tar.gz', plan['files'])
        self.assertIn('source/embedding-build.json', plan['files'])
        self.assertIn('source/build.sh', plan['files'])
        self.assertIn('source/toolchain/gmp/provenance.json', plan['files'])
        self.assertEqual(plan['forced_case_count'], 2)
        self.assertTrue({0, 6309}.issubset(plan['regression_indices']))
        regression_tasks = [t for t in plan['tasks'] if t['phase'] == 'regression']
        full_tasks = [t for t in plan['tasks'] if t['phase'] == 'full']
        self.assertEqual(len(plan['regression_indices']), 52)
        self.assertEqual(len(regression_tasks), 2)
        self.assertTrue(all(t['indices'] == plan['regression_indices'] for t in regression_tasks))
        self.assertEqual(len(full_tasks), 12)
        self.assertEqual({t['variant'] for t in plan['tasks']}, {'baseline', 'embedded'})
        self.assertFalse(any((output / 'tasks' / t['id'] / 'intent.json').exists() for t in plan['tasks']))

    def test_prepare_rejects_embedded_receipt_for_different_executable(self):
        tree = self.root / 'tree'; tree.mkdir()
        baseline = self.root / 'baseline'; baseline.write_bytes(b'baseline')
        embedded = self.root / 'embedded'; embedded.write_bytes(b'embedded')
        receipt = self.root / 'baseline-build.json'
        receipt.write_text(json.dumps(dict(git_commit=self.m.COMMIT, executable_sha256=self.m.digest(baseline))))
        (tree / 'embedding-build.json').write_text(json.dumps(dict(baseline_commit=self.m.COMMIT,
                                                                  executable_sha256='different')))
        args = SimpleNamespace(tree=tree, workspace=self.root, output=self.root / 'output',
                               baseline=baseline, embedded=embedded, baseline_build_record=receipt)
        with self.assertRaisesRegex(ValueError, 'embedded.*receipt'):
            self.m.prepare(args)

    def test_worker_manifest_config_and_actual_runtime_must_match(self):
        self.assertTrue(hasattr(self.m, 'config_manifest_values'), 'missing manifest validation')
        plan = dict(config=dict(self.m.FULL_CONFIG), seed_defaults=dict(explicit_seed=None, mt=0, lia_seed=0),
                    converter_sha256='converter', variants=dict(embedded=dict(executable_sha256='binary')))
        task = dict(variant='embedded')
        local = dict(self.m.config_manifest_values(plan['config']), solver_sha256='binary',
                     converter_sha256='converter', actual_converter_sha256='binary',
                     seed_defaults=plan['seed_defaults'])
        self.m.validate_local_manifest(plan, task, local)
        for change in [dict(optimality_balanced=False), dict(solver_sha256='different'),
                       dict(actual_converter_sha256='different'), dict(known_optimum_stop_enabled=True)]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.m.validate_local_manifest(plan, task, dict(local, **change))

    def test_sacct_terminal_failure_is_recorded_without_resubmit(self):
        self.assertTrue(hasattr(self.m, 'record_terminal_jobs'), 'missing terminal job recovery')
        plan, task = self.task_fixture()
        where = self.root / 'tasks' / task['id']
        (where / 'intent.json').write_text(json.dumps(dict(node='node1')))
        (where / 'receipt.json').write_text(json.dumps(dict(job_id='42')))
        answer = subprocess.CompletedProcess([], 0, '42|FAILED|1:0\n', '')
        with patch.object(self.m.subprocess, 'run', return_value=answer):
            self.m.record_terminal_jobs(self.root, plan, [])
        failure = json.loads((where / 'worker_done.json').read_text())
        self.assertNotEqual(failure['returncode'], 0)
        self.assertEqual(failure['scheduler_state'], 'FAILED')

    def test_reference_report_reuses_exact_official_comparison(self):
        self.assertTrue(hasattr(self.m, 'write_reference_report'), 'missing reference report bridge')
        nia = {'x': {'文件名': 'x', '状态': 'A2', '目标值': 7, '时间（秒）': '1'}}
        baseline = {'x': {'文件名': 'x', 'z3状态': 'B', 'z3目标值': '8', 'z3时间（秒）': '2',
                          'optimathsat状态': 'C', 'optimathsat目标值': 'NA', 'optimathsat时间（秒）': '3'}}
        reporter = SimpleNamespace(NIA_FIELDS=['文件名', '状态', '目标值', '时间（秒）'],
                                   COMBINED_FIELDS=list(baseline['x']) + ['NIA_LS状态', 'NIA_LS目标值', 'NIA_LS时间（秒）'],
                                   load_nia_run=lambda _: (nia, {'x': 'min'}), load_competitors=lambda _: baseline,
                                   compute_statistics=lambda rows, directions: dict(solved=1, count=len(rows)),
                                   csv_text=lambda fields, rows: 'csv\n', render_report=lambda stats, run: 'report\n',
                                   atomic_write=lambda path, text, *args: Path(path).write_text(text))
        with patch.object(self.m.importlib, 'import_module', return_value=reporter):
            stats = self.m.write_reference_report(self.root, self.root / 'competitor.csv')
        self.assertEqual(stats, dict(solved=1, count=1))
        self.assertTrue((self.root / 'summary.md').exists())

    def test_both_unsolved_pairs_and_top_summary(self):
        self.assertTrue(hasattr(self.m, 'paired_summary'), 'missing both-unsolved paired report')
        def row(state, value):
            return dict(status='sat' if state != 'C' else 'timeout', model_validated='true',
                        best_value=str(value) if state != 'C' else '', direction='min',
                        is_optimal=str(state == 'A2').lower(), is_unbounded=str(state == 'A1').lower())
        a = {0: row('B', 10), 1: row('B', 5), 2: row('C', 0), 3: row('A2', 1)}
        b = {0: row('B', 9), 1: row('B', 6), 2: row('C', 0), 3: row('B', 1)}
        summary = self.m.paired_summary(a, b)
        self.assertEqual(summary['both_unsolved_paired'], dict(cases=3, wins=1, ties=1, losses=1))
        self.assertEqual(summary['solved_delta'], -1)
        text = self.m.render_top_summary(dict(regression=summary, full=dict(complete=False)))
        self.assertIn('| baseline | 0 | 1 | 2 | 1 | 1 |', text)
        self.assertIn('| embedded | 0 | 0 | 3 | 1 | 0 |', text)
        self.assertIn('| Both B/C | 3 | 1 | 1 | 1 |', text)


class AdapterTests(unittest.TestCase):
    def setUp(self): self.a = load(ADAPTER, 'embedding_adapter_under_test')

    def test_converter_translation_keeps_definition_option(self):
        original = ['python', '/converter.py', 'input.smt2', '-o', 'input.nia', '--keep-definitions']
        self.assertEqual(self.a.translate_command(original, 'embedded', '/z3', '/converter.py'),
                         ['/z3', '--nia-ls', 'input.smt2', '--import-only', '--export-native=input.nia', '--keep-definitions'])

    def test_solver_translation_preserves_defaults_and_all_flags(self):
        original = ['/temporary/nia_ls', 'input.nia', '--optimize', '--timeout=99', '--optimality-balanced']
        self.assertEqual(self.a.translate_command(original, 'baseline', '/baseline', '/converter'), original)
        actual = self.a.translate_command(original, 'embedded', '/z3', '/converter')
        self.assertEqual(actual, ['/z3', '--nia-ls', '--native-input', 'input.nia', *original[2:]])
        self.assertFalse(any(x.startswith('--seed') or x.startswith('--mode') for x in actual))


if __name__ == '__main__': unittest.main()
