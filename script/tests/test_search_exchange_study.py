"""Exchange-mode transport and frozen-baseline study contracts."""
import contextlib
import importlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from script import run_optimize as runner
from script import run_optimize_slurm as slurm


class ExchangeRunnerTests(unittest.TestCase):
    def test_exchange_modes_validate_and_survive_frozen_config(self):
        self.assertIn('search_exchange', runner.Config.__dataclass_fields__)
        self.assertEqual(runner.Config().search_exchange, 'off')
        for mode in ('off', 'observe', 'sls-to-z3', 'z3-to-sls', 'both'):
            with self.subTest(mode=mode):
                config = runner.Config(prove_optimal=True, search_exchange=mode)
                restored = slurm.config_from_record(json.loads(json.dumps(slurm.config_record(config))))
                self.assertEqual(restored.search_exchange, mode)
                self.assertEqual(slurm.make_manifest([], restored)['search_exchange'], mode)
        for options in ({'search_exchange': 'both'}, {'search_exchange': 'typo', 'prove_optimal': True}):
            with self.assertRaisesRegex(ValueError, 'search.exchange'):
                runner.Config(**options)

    def test_both_clis_reject_exchange_without_proof_before_reading_inputs(self):
        self.assertIn('search_exchange', vars(runner.make_parser({}).parse_args([])))
        for module in (runner, slurm):
            with self.subTest(module=module.__name__), \
                 patch.object(runner, 'collect_inputs', side_effect=AssertionError('read inputs')), \
                 contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(module.main(['--search-exchange', 'both', '--dry-run']), 2)

    def test_slurm_cli_forwards_mode_to_config(self):
        self.assertIn('search_exchange', vars(runner.make_parser({}).parse_args([])))
        case = runner.InputCase(Path('/tmp/exchange.smt2'), 'exchange.smt2', 'original', 'original', 'min')
        real_config = runner.Config
        with patch.object(runner, 'collect_inputs', return_value=[case]), \
             patch.object(runner, 'Config', wraps=real_config) as configured, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(slurm.main(['--prove-optimal', '--search-exchange', 'both', '--dry-run']), 0)
        self.assertEqual(configured.call_args.kwargs['search_exchange'], 'both')

    def test_native_flag_and_batch_manifests_keep_default_off_compatible(self):
        self.assertIn('search_exchange', runner.Config.__dataclass_fields__)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable = root / 'solver'
            executable.write_text('#!/bin/sh\nexit 0\n')
            converter = root / 'converter.py'
            converter.write_text('# fixture\n')
            case = runner.InputCase(root / 'case.smt2', 'case.smt2', 'original', 'original', 'min')
            for mode in ('off', 'observe', 'sls-to-z3', 'z3-to-sls', 'both'):
                commands = []
                def stage(command, deadline, stop, stdout, stderr, *args, **kwargs):
                    if command[0] == sys.executable:
                        Path(command[command.index('-o') + 1]).write_text('native fixture')
                        return 0
                    commands.append(command)
                    raise TimeoutError()
                config = runner.Config(jobs=1, timeout=1, solver=executable, converter=converter,
                                       prove_optimal=True, search_exchange=mode)
                output = root / mode
                with patch.object(runner, '_stage', side_effect=stage):
                    self.assertEqual(runner.run_batch([case], config, output), 0)
                flags = [x for x in commands[0] if x.startswith('--search-exchange')]
                self.assertEqual(flags, [] if mode == 'off' else [f'--search-exchange={mode}'])
                for filename in ('manifest.json', 'summary.json'):
                    self.assertEqual(json.loads((output / filename).read_text())['search_exchange'], mode)


class StudyHelperTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue((Path(__file__).parents[1] / 'search_exchange_study.py').is_file(),
                        'search exchange study helper is missing')
        self.study = importlib.import_module('script.search_exchange_study')

    def fake_run(self, count=4):
        names = [f'QF_NIA/test/{i}.smt2' for i in range(count)]
        cases = {n: dict(source=f'/bench/{n}', filename=n, suite='original', category='original', direction='min') for n in names}
        nia = {n: {'状态': 'A2', '目标值': '0'} for n in names}
        competitors = {n: {'z3目标值': '0', 'optimathsat目标值': '0'} for n in names}
        return dict(path=Path('/tmp/run'), nia=nia, cases=cases,
                    directions=dict.fromkeys(names, 'min'), competitors=competitors,
                    manifest=dict(timeout=1200, selection_complete=True, prove_optimal=True,
                                  scalar_target_enabled=False, known_optimum_stop_enabled=False,
                                  scheduler='slurm', nodes=['server', 'node1', 'node2'],
                                  jobs_per_node=160),
                    summary=dict(selected=count, completed=count, complete=True),
                    interrupted=0)

    def test_cohorts_union_all_unsolved_with_protected_groups(self):
        run = self.fake_run()
        names = list(run['nia'])
        run['nia'][names[0]] = {'状态': 'B', '目标值': '1'}
        run['nia'][names[1]] = {'状态': 'C', '目标值': 'NA'}
        evidence = {'groups': {'final_focus369': {'names': names[2:]},
                              'current_candidate_A2_proof_ge300_21': {'names': [names[2]]},
                              'historical_controls128': {'names': [names[3]]},
                              'all_observed_success_regressions': {'names': [names[1]]}},
                    'study_rows': []}
        groups = self.study.build_cohorts(run, evidence)
        self.assertEqual(groups['unsolved'], set(names[:2]))
        self.assertEqual(groups['focus'], set(names))
        self.assertEqual(groups['long_proofs'], {names[2]})
        self.assertEqual(groups['losses'], set(names[:2]))
        self.assertEqual(groups['small_original_negatives'], {names[0]})

    def test_idle_guard_rejects_unallowed_and_nonidle_nodes(self):
        with patch.object(self.study.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '123|RUNNING|server|other\n', '')) as conflict:
            with self.assertRaisesRegex(ValueError, 'idle'):
                self.study.check_idle_nodes(['server'])
            self.assertEqual(conflict.call_count, 1)
        idle = 'NodeName=server State=IDLE CPUAlloc=0 CPUTot=192\n'
        done = subprocess.CompletedProcess([], 0, idle, '')
        with patch.object(self.study.subprocess, 'run', return_value=done) as command:
            with self.assertRaisesRegex(ValueError, 'allowed'):
                self.study.check_idle_nodes(['node5'])
            command.assert_not_called()
        for bad in ('State=MIXED CPUAlloc=1', 'State=IDLE+DRAIN CPUAlloc=0'):
            with patch.object(self.study.subprocess, 'run', side_effect=[
                subprocess.CompletedProcess([], 0, '', ''),
                subprocess.CompletedProcess([], 0, f'NodeName=server {bad} CPUTot=192\n', '')]):
                with self.assertRaisesRegex(ValueError, 'idle'):
                    self.study.check_idle_nodes(['server'])
        with patch.object(self.study.subprocess, 'run', side_effect=[
            subprocess.CompletedProcess([], 0, '', ''), done]):
            self.assertIn('squeue', self.study.check_idle_nodes(['server']))

    def test_launch_uses_frozen_control_and_checks_idle_immediately_before_sbatch(self):
        run = self.fake_run(1)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            baseline = root / 'baseline'
            baseline.mkdir()
            (baseline / 'slurm_state.json').write_text(json.dumps({'config': dict(
                jobs=180, timeout=1200, solver='/frozen/old', converter='/frozen/converter',
                subset='all', prove_optimal=True, optimality_stall=10,
                optimality_resume=True, optimality_burst=True, optimality_cutset=True,
                optimality_balanced=True, initial_z3_after=300), 'snapshot': {}}))
            (baseline / 'source/result').mkdir(parents=True)
            (baseline / 'source/result/competitor.csv').write_bytes((runner.ROOT / 'result/competitor.csv').read_bytes())
            listing = root / 'selected.txt'
            listing.write_text(run['cases'][next(iter(run['cases']))]['source'] + '\n')
            events = []
            def prepared(*args, **kwargs):
                Path(args[2]).mkdir()
                events.append('prepare')
                return {}
            def idle(nodes):
                events.append('idle')
                return {'squeue': '', 'nodes': list(nodes)}
            def submit(*args, **kwargs):
                events.append('sbatch')
                return subprocess.CompletedProcess([], 0, '12345\n', '')
            case = runner.InputCase(Path(next(iter(run['cases'].values()))['source']), next(iter(run['cases'])), 'original', 'original', 'min')
            with patch.object(self.study.slurm, 'check_snapshot'), \
                 patch.object(self.study.runner, 'collect_inputs', return_value=[case]), \
                 patch.object(self.study.slurm, 'prepare_run', side_effect=prepared) as prepare, \
                 patch.object(self.study.slurm, 'make_sbatch_command', return_value=['sbatch']), \
                 patch.object(self.study, 'check_idle_nodes', side_effect=idle), \
                 patch.object(self.study.subprocess, 'run', side_effect=submit):
                result = self.study.launch_run(baseline, listing, ['server'], 'off',
                                               output=root / 'NIA_control')
            self.assertEqual(events, ['prepare', 'idle', 'sbatch'])
            config = prepare.call_args.args[1]
            self.assertEqual((config.jobs, config.timeout, config.search_exchange), (160, 1200, 'off'))
            self.assertEqual(config.solver, Path('/frozen/old'))
            self.assertEqual(result['job_id'], '12345')

    def test_launch_rejects_changed_competitor_before_preparing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'slurm_state.json').write_text(json.dumps({'config': dict(
                solver='/old', converter='/converter', prove_optimal=True), 'snapshot': {}}))
            (root / 'source/result').mkdir(parents=True)
            (root / 'source/result/competitor.csv').write_text('different baseline')
            with patch.object(self.study.slurm, 'check_snapshot'), \
                 patch.object(self.study.runner, 'collect_inputs', return_value=[]), \
                 patch.object(self.study.slurm, 'prepare_run', side_effect=AssertionError('prepared')):
                with self.assertRaisesRegex(ValueError, 'competitor'):
                    self.study.launch_run(root, None, ['server'], 'off')

    def test_launch_rejects_a_candidate_with_a_recorded_correctness_issue(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);candidate=root/'nia_ls';candidate.write_text('placeholder')
            (root/'correctness_status.json').write_text('{"status":"quarantined"}\n')
            with patch.object(self.study.slurm,'prepare_run',side_effect=AssertionError('prepared')):
                with self.assertRaisesRegex(ValueError,'correctness'):
                    self.study.launch_run(root/'unused-baseline',None,['server'],'both',candidate=candidate)

    def test_partial_or_unverified_runs_never_meet_full_goal(self):
        baseline = self.fake_run()
        current = self.fake_run()
        current['nia'][next(iter(current['nia']))]['状态'] = 'B'
        result = self.study.compare_runs(baseline, current, audit_ok=True)
        self.assertFalse(result['full_goal']['eligible'])
        self.assertIsNone(result['full_goal']['met'])
        self.assertEqual(result['full_goal']['required_solved'], 5924)
        self.assertEqual(result['baseline_loss_count'], 0)

    def test_full_goal_rejects_wrong_baseline_solved_count(self):
        baseline = self.fake_run(6310)
        current = self.fake_run(6310)
        result = self.study.compare_runs(baseline, current, audit_ok=True)
        self.assertFalse(result['full_goal']['eligible'])

    def test_full_goal_requires_fifty_net_solved_and1200_budget(self):
        baseline = self.fake_run(6310)
        current = self.fake_run(6310)
        for name in list(baseline['nia'])[:31]:
            baseline['competitors'][name]['z3目标值'] = '-1'
            current['competitors'][name]['z3目标值'] = '-1'
        for name in list(baseline['nia'])[5874:]:
            baseline['nia'][name]['状态'] = 'B'
        for name in list(current['nia'])[5924:]:
            current['nia'][name]['状态'] = 'B'
        good = self.study.compare_runs(baseline, current, audit_ok=True)
        self.assertEqual(good['solved_delta'], 50)
        self.assertTrue(good['full_goal']['met'])
        with tempfile.TemporaryDirectory() as temporary:
            current['path']=Path(temporary)
            (current['path']/'correctness_status.json').write_text('{"status":"quarantined"}\n')
            flagged=self.study.compare_runs(baseline,current,audit_ok=True)
            self.assertFalse(flagged['full_goal']['eligible'])
            self.assertIsNone(flagged['full_goal']['met'])
            self.assertTrue(any('correctness' in reason for reason in flagged['full_goal']['ineligible_reasons']))
        current['path']=Path('/tmp/run')
        name=list(current['nia'])[31]
        current['nia'][name]['目标值']='1'
        self.assertFalse(self.study.compare_runs(baseline,current,audit_ok=True)['full_goal']['met'],
                         'a solved-count gain does not override additional objective losses')
        current['nia'][name]['目标值']='0'
        self.assertIsNone(self.study.compare_runs(baseline, current)['full_goal']['met'])
        current['manifest']['timeout'] = 1199
        self.assertIsNone(self.study.compare_runs(baseline, current, audit_ok=True)['full_goal']['met'])


if __name__ == '__main__':
    unittest.main()
