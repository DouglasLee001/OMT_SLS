"""Exact ray certificates and retained unbounded-proof runner contracts."""
import copy
import json
import threading
import unittest
from unittest import mock

from script.tests import test_run_optimize as runner_tests
from script.tests import test_verify_optimize as verifier_tests


def native_text(direction='min'):
    return ('NIA_LS_OMT 1\nSTATUS ready\nSOURCE -\nORIGINAL_OBJECTIVE 78\n'
            f'DIRECTION {direction}\nVARIABLES 1\n0 I 78\n'
            'LITERALS 1\n1 L 0 1\n1 1 0 1\nCLAUSES 1\n1 1\n'
            'OBJECTIVE 0 1\n1 1 0 1\nEND\n')


def certificate():
    return dict(kind='lia_ray', version=1, base=['-10'], direction=['-1'],
                fixed=[], literals=[1], objective_slope='-1')


def proof_final(**changed):
    result = dict(event='final', status='sat', direction='min', objective_value='-10',
                  elapsed_seconds=.004, time_to_best_seconds=.001, model_validated=True,
                  is_optimal=False, termination='unbounded_proved', is_unbounded=True,
                  unbounded_threshold='10', unbounded_attempts=1, unbounded_queries=1,
                  unbounded_seconds=.002, unbounded_status='proved',
                  time_to_unbounded_seconds=.003, unbounded_certificate=certificate())
    result.update(changed)
    return result


class RayCertificateTests(unittest.TestCase):
    def setUp(self):
        self.v = verifier_tests.load_verifier()
        self.native = self.v.parse_native(native_text())

    def test_valid_ray_preserves_exact_arbitrary_precision_directions(self):
        proof = certificate()
        proof['direction'] = [str(-10**200)]
        proof['objective_slope'] = str(-10**200)
        self.assertEqual(self.v.verify_unbounded_certificate(self.native, proof), -10)

    def test_forged_certificate_rejected(self):
        for changed in [dict(base=['1']), dict(direction=['1']), dict(literals=[]),
                        dict(fixed=[0]), dict(fixed=[True]), dict(objective_slope='-2'),
                        dict(direction=[-1]), dict(version=True), dict(literals=[1, 1])]:
            with self.subTest(changed=changed), self.assertRaises(self.v.VerificationError):
                self.v.verify_unbounded_certificate(self.native, dict(certificate(), **changed))

    def test_boolean_directions_and_fixed_variables_must_stay_zero(self):
        self.native['variables'][1] = ('B', 'b')
        self.native['literals'][2] = ('B', 1)
        self.native['clauses'].append([2])
        proof = dict(certificate(), base=['-10', '1'], direction=['-1', '0'], literals=[1, 2])
        self.assertEqual(self.v.verify_unbounded_certificate(self.native, proof), -10)
        for changed in [dict(direction=['-1', '1']), dict(base=['-10', '0']), dict(fixed=[1])]:
            with self.subTest(changed=changed), self.assertRaises(self.v.VerificationError):
                self.v.verify_unbounded_certificate(self.native, dict(proof, **changed))

    def test_fixed_substitution_merges_monomials_and_zero_factors(self):
        self.native['variables'][1] = ('I', 'y')
        self.native['objective'] = (0, [(1, [(0, 1)]), (7, [(0, 2), (1, 1)])])
        proof = dict(certificate(), base=['-10', '0'], direction=['-1', '0'], fixed=[1])
        self.assertEqual(self.v.verify_unbounded_certificate(self.native, proof), -10)
        self.native['objective'] = (0, [(1, [(0, 1)]), (7, [(0, 2)]), (-7, [(0, 1), (0, 1)])])
        self.assertEqual(self.v.verify_unbounded_certificate(self.native, proof), -10)
        self.native['objective'] = (0, [(1, [(0, 2)])])
        with self.assertRaises(self.v.VerificationError):
            self.v.verify_unbounded_certificate(self.native, proof)

    def test_negated_inequality_and_disequality_do_not_cross_zero(self):
        self.native['direction'] = 'max'
        self.native['clauses'] = [[-1], [-2]]
        self.native['literals'][2] = ('E', (-3, [(1, [(0, 1)])]))
        proof = dict(certificate(), base=['10'], direction=['1'], literals=[-1, -2], objective_slope='1')
        self.assertEqual(self.v.verify_unbounded_certificate(self.native, proof), 10)
        self.native['literals'][2] = ('E', (-20, [(1, [(0, 1)])]))
        with self.assertRaises(self.v.VerificationError):
            self.v.verify_unbounded_certificate(self.native, proof)

    def test_retained_model_must_match_every_base_variable_even_with_same_objective(self):
        self.native['variables'][1] = ('I', 'y')
        proof = dict(certificate(), base=['-10', '0'], direction=['-1', '0'])
        with self.assertRaises(self.v.VerificationError):
            self.v.verify_unbounded_certificate(self.native, proof, 'x\t-10\ny\t1\n')

    def test_zero_factors_and_cancelled_monomials_do_not_evaluate_huge_powers(self):
        class UnevaluatedExponent(int):
            def __rpow__(self, base):
                raise AssertionError('a zero or cancelled monomial must not compute its huge power')

            def __radd__(self, value):
                return type(self)(value + int(self))

            def __add__(self, value):
                return type(self)(int(self) + value)

        self.native['variables'] = {0: ('I', 'x'), 1: ('I', 'y'), 2: ('I', 'z')}
        self.native['objective'] = (0, [(1, [(2, 1)])])
        power = UnevaluatedExponent(2147483647)
        proof = dict(certificate(), base=['10', '0', '0'], direction=['0', '0', '-1'], fixed=[0, 1])
        for terms in [[(1, [(0, power), (1, 1)])],
                      [(1, [(0, power)]), (-1, [(0, power)])]]:
            with self.subTest(terms=terms):
                self.native['literals'][1] = ('E', (0, terms))
                self.assertEqual(self.v.verify_unbounded_certificate(self.native, proof), 0)


class UnboundedRunnerTests(unittest.TestCase):
    def setUp(self):
        self.fixture = runner_tests.OptimizationRunnerTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.runner, self.root = self.fixture.runner, self.fixture.root

    def setup_proof(self, final=None, body=None, timeout=1):
        report = proof_final() if final is None else final
        body = body or ('import json,pathlib,sys\n'
            "assert '--prove-unbounded' in sys.argv\n"
            "assert '--unbounded-threshold=10' in sys.argv\n"
            "assert not any(a.startswith('--target=') for a in sys.argv)\n"
            "pathlib.Path(next(a.split('=',1)[1] for a in sys.argv if a.startswith('--model-output='))).write_text('x\\t-10\\n')\n"
            'print(json.dumps(' + repr(report) + '),flush=True)\n')
        config, case = self.fixture.setup_case(native_body=body, timeout=timeout,
            converter_body='import pathlib,sys\npathlib.Path(sys.argv[sys.argv.index("-o")+1]).write_text(' + repr(native_text()) + ')\n')
        return self.runner.replace(config, prove_unbounded=True, unbounded_threshold=10), case

    def test_opt_in_defaults_validation_and_cli_dependency(self):
        config = self.runner.Config()
        self.assertIs(config.prove_unbounded, False)
        self.assertEqual(config.unbounded_threshold, 1000000)
        args = self.runner.make_parser({}).parse_args(['--prove-unbounded', '--unbounded-threshold', '10'])
        self.assertTrue(args.prove_unbounded)
        with mock.patch.object(self.runner, 'collect_inputs', return_value=[]):
            self.assertEqual(self.runner.main(['--dry-run', '--unbounded-threshold', '10']), 2)
            self.assertEqual(self.runner.main(['--dry-run', '--prove-unbounded', '--unbounded-threshold', '10']), 0)
        for value in [0, -1, True, 2**127]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.runner.Config(prove_unbounded=True, unbounded_threshold=value)

    def test_runner_validates_proof_preserves_finite_value_and_offsets_time(self):
        config, case = self.setup_proof()
        row = self.runner.run_case(case, config, threading.Event(), self.root / 'artifacts')
        self.assertEqual((row['status'], row['termination'], row['best_value'], row['is_optimal']),
                         ('sat', 'unbounded_proved', '-10', 'false'))
        self.assertEqual(row['is_unbounded'], 'true', row)
        self.assertGreater(float(row['time_to_unbounded_seconds']), .003)
        self.assertNotIn('unbounded_certificate', row)

    def test_forged_or_missing_proof_cannot_claim_unbounded(self):
        for changed in [dict(unbounded_certificate=dict(certificate(), direction=['1'])),
                        dict(unbounded_certificate=None), dict(unbounded_attempts=0),
                        dict(time_to_unbounded_seconds=.005), dict(unbounded_threshold='11'),
                        dict(time_to_unbounded_seconds=.0005, unbounded_seconds=.0001)]:
            with self.subTest(changed=changed):
                config, case = self.setup_proof(final=proof_final(**changed))
                row = self.runner.run_case(case, config, threading.Event())
                self.assertEqual((row['status'], row['is_unbounded']), ('error', 'false'), row)

    def test_proof_after_budget_received_during_cleanup_is_not_accepted(self):
        report = proof_final(elapsed_seconds=.19, time_to_unbounded_seconds=.18)
        body = ('import json,pathlib,signal,sys,time\n'
                "pathlib.Path(next(a.split('=',1)[1] for a in sys.argv if a.startswith('--model-output='))).write_text('x\\t-10\\n')\n"
                'def finish(sig,frame):\n print(json.dumps(' + repr(report) + '),flush=True)\n sys.exit(0)\n'
                'signal.signal(signal.SIGTERM,finish)\n'
                "print(json.dumps({'event':'incumbent','status':'sat','direction':'min','objective_value':'-10','elapsed_seconds':.001,'model_validated':True}),flush=True)\n"
                'time.sleep(30)\n')
        config, case = self.setup_proof(body=body, timeout=.2)
        row = self.runner.run_case(case, config, threading.Event())
        self.assertEqual((row['termination'], row['is_unbounded']), ('wall_timeout', 'false'), row)

    def test_manifest_summary_use_actual_target_and_count_proofs(self):
        config, case = self.setup_proof()
        directory = self.root / 'run'
        self.assertEqual(self.runner.run_batch([case], config, directory), 0)
        for name in ['manifest.json', 'summary.json']:
            metadata = json.loads((directory / name).read_text())
            self.assertIs(metadata['prove_unbounded'], True)
            self.assertEqual(metadata['unbounded_threshold'], '10')
            self.assertIs(metadata['scalar_target_enabled'], False)
        self.assertEqual(json.loads((directory / 'summary.json').read_text())['is_unbounded'], 1)

    def test_missing_metadata_and_incumbent_claims_never_grant_proof(self):
        for report in [dict(event='final', status='sat', direction='min', objective_value='-10',
                            elapsed_seconds=.002, model_validated=True, termination='unbounded_proved'),
                       dict(proof_final(), event='incumbent')]:
            with self.subTest(report=report):
                config, case = self.setup_proof(final=report)
                row = self.runner.run_case(case, config, threading.Event())
                self.assertEqual((row['status'], row['is_unbounded']), ('error', 'false'), row)

    def test_known_optimum_stop_remains_enabled_without_native_final(self):
        body = ('import json,time\n'
                "print(json.dumps({'event':'incumbent','status':'sat','direction':'min','objective_value':'-10','elapsed_seconds':.001,'model_validated':True}),flush=True)\n"
                'time.sleep(30)\n')
        config, case = self.setup_proof(body=body)
        case = self.runner.replace(case, known_optimum=-10, known_optimum_source='z3')
        row = self.runner.run_case(case, config, threading.Event())
        self.assertEqual((row['termination'], row['is_unbounded'], row['unbounded_attempts']),
                         ('known_optimum_reached', 'false', ''), row)

    def test_default_mode_rejects_forged_unbounded_termination_without_metadata(self):
        config, case = self.fixture.setup_case(native_body=
            "import json\nprint(json.dumps({'event':'final','status':'sat','direction':'min','objective_value':'8','elapsed_seconds':.001,'model_validated':True,'termination':'unbounded_proved'}))\n")
        row = self.runner.run_case(case, config, threading.Event())
        self.assertEqual((row['status'], row['is_unbounded']), ('error', 'false'), row)

    def test_final_objective_must_equal_certificate_base_and_retained_incumbent(self):
        report = proof_final(objective_value='-9')
        body = ('import json,pathlib,sys\n'
                "pathlib.Path(next(a.split('=',1)[1] for a in sys.argv if a.startswith('--model-output='))).write_text('x\\t-10\\n')\n"
                "print(json.dumps({'event':'incumbent','status':'sat','direction':'min','objective_value':'-10','elapsed_seconds':.001,'model_validated':True}),flush=True)\n"
                'print(json.dumps(' + repr(report) + '),flush=True)\n')
        config, case = self.setup_proof(body=body)
        row = self.runner.run_case(case, config, threading.Event())
        self.assertEqual((row['status'], row['is_unbounded']), ('error', 'false'), row)

    def test_proof_received_during_known_optimum_cleanup_keeps_proof_termination(self):
        report = proof_final()
        body = ('import json,pathlib,signal,sys,time\n'
                "pathlib.Path(next(a.split('=',1)[1] for a in sys.argv if a.startswith('--model-output='))).write_text('x\\t-10\\n')\n"
                'def finish(sig,frame):\n print(json.dumps(' + repr(report) + '),flush=True)\n sys.exit(0)\n'
                'signal.signal(signal.SIGTERM,finish)\n'
                "print(json.dumps({'event':'incumbent','status':'sat','direction':'min','objective_value':'-10','elapsed_seconds':.001,'model_validated':True}),flush=True)\n"
                'time.sleep(30)\n')
        config, case = self.setup_proof(body=body)
        case = self.runner.replace(case, known_optimum=-10, known_optimum_source='z3')
        row = self.runner.run_case(case, config, threading.Event())
        self.assertEqual((row['termination'], row['is_unbounded']), ('unbounded_proved', 'true'), row)


class UnboundedAuditTests(unittest.TestCase):
    def setUp(self):
        self.fixture = verifier_tests.OptimizationVerifierTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.v = self.fixture.verifier

    def prepare(self):
        f = self.fixture
        f.case.update(known_optimum_value=None, known_optimum_source='')
        f.row.update(termination='unbounded_proved', best_value='-10', vs_z3='better',
                     vs_optimathsat='better', vs_best='better', strictly_better_than_best='true',
                     known_optimum_value='', known_optimum_source='', known_optimum_reached='false',
                     is_unbounded='true', unbounded_threshold='10', unbounded_attempts='1',
                     unbounded_queries='1', unbounded_seconds='0.002', unbounded_status='proved',
                     time_to_unbounded_seconds='0.153')
        f.write_artifacts(final='unbounded_proved', value='-10')
        text = native_text().replace('SOURCE -', 'SOURCE ' + str(f.source).encode().hex())
        (f.artifact / 'input.nia').write_text(text)
        events = [json.loads(line) for line in (f.artifact / 'native.stdout').read_text().splitlines()]
        events[-1] = proof_final(elapsed_seconds=.104, time_to_best_seconds=.1, time_to_unbounded_seconds=.103)
        (f.artifact / 'native.stdout').write_text(''.join(json.dumps(e) + '\n' for e in events))
        return events

    def audit(self):
        f = self.fixture
        return self.v.verify_case(f.case, f.row, f.artifact, 1, False,
                                  unbounded_enabled=True, unbounded_threshold=10, unbounded_metadata=True)

    def test_independent_audit_accepts_exact_proof(self):
        self.prepare()
        report = self.audit()
        self.assertTrue(report['ok'], report)
        self.assertTrue(report['verified_is_unbounded'])

    def test_independent_audit_rejects_forged_certificate_and_missing_metadata(self):
        events = self.prepare()
        for changed in [dict(unbounded_certificate=dict(certificate(), base=['-11'])),
                        dict(unbounded_queries=0), dict(unbounded_status='unknown'),
                        dict(time_to_unbounded_seconds=2)]:
            with self.subTest(changed=changed):
                bad = copy.deepcopy(events)
                bad[-1].update(changed)
                (self.fixture.artifact / 'native.stdout').write_text(''.join(json.dumps(e) + '\n' for e in bad))
                self.assertFalse(self.audit()['ok'])

    def full_audit(self):
        f = self.fixture
        f.save_run()
        path = f.run / 'summary.json'
        summary = json.loads(path.read_text())
        summary.update(prove_unbounded=f.manifest['prove_unbounded'],
                       unbounded_threshold=f.manifest['unbounded_threshold'],
                       is_unbounded=int(f.row['is_unbounded'] == 'true'),
                       scalar_target_enabled=f.manifest['scalar_target_enabled'])
        path.write_text(json.dumps(summary))
        return self.v.verify_run(f.run, expected_cases=1)

    def test_manifest_and_summary_audit_enabled_proof(self):
        self.prepare()
        f = self.fixture
        f.manifest.update(prove_unbounded=True, unbounded_threshold='10')
        f.write_tables(z3_proven='false')
        report = self.full_audit()
        self.assertTrue(report['ok'], report)
        self.assertEqual(report['verified_is_unbounded'], 1)
        for field, changed in [('prove_unbounded', False), ('unbounded_threshold', '11'), ('is_unbounded', 0)]:
            with self.subTest(field=field):
                self.full_audit()
                path = f.run / 'summary.json'
                summary = json.loads(path.read_text())
                summary[field] = changed
                path.write_text(json.dumps(summary))
                self.assertFalse(self.v.verify_run(f.run, expected_cases=1)['ok'])

    def test_no_final_keeps_unbounded_attempt_result_unknown(self):
        f = self.fixture
        f.manifest.update(prove_unbounded=True, unbounded_threshold='10')
        f.row.update(is_unbounded='false', unbounded_threshold='10', unbounded_attempts='',
                     unbounded_queries='', unbounded_seconds='', unbounded_status='',
                     time_to_unbounded_seconds='')
        report = self.full_audit()
        self.assertTrue(report['ok'], report)
        self.assertEqual(report['verified_is_unbounded'], 0)

    def test_disabled_metadata_allows_no_activity(self):
        f = self.fixture
        f.ordinary_final()
        f.manifest.update(prove_unbounded=False, unbounded_threshold=None)
        f.row.update(is_unbounded='false', unbounded_threshold='', unbounded_attempts='0',
                     unbounded_queries='0', unbounded_seconds='0', unbounded_status='disabled',
                     time_to_unbounded_seconds='')
        path = f.artifact / 'native.stdout'
        events = [json.loads(line) for line in path.read_text().splitlines()]
        events[-1].update(is_unbounded=False, unbounded_threshold=None, unbounded_attempts=0,
                          unbounded_queries=0, unbounded_seconds=0, unbounded_status='disabled',
                          time_to_unbounded_seconds=None, unbounded_certificate=None)
        path.write_text(''.join(json.dumps(event) + '\n' for event in events))
        report = self.full_audit()
        self.assertTrue(report['ok'], report)
        events[-1]['unbounded_queries'] = 1
        path.write_text(''.join(json.dumps(event) + '\n' for event in events))
        self.assertFalse(self.full_audit()['ok'])

    def test_csv_enable_flag_without_manifest_configuration_is_rejected(self):
        self.fixture.row['prove_unbounded'] = 'true'
        report = self.fixture.verify()
        self.assertFalse(report['ok'], report)

    def test_proof_slice_scheduling_overhead_does_not_invalidate_a_case_within_budget(self):
        events = self.prepare()
        events[-1].update(elapsed_seconds=.27, unbounded_seconds=.15, time_to_unbounded_seconds=.26)
        self.fixture.row.update(unbounded_seconds='0.15', time_to_unbounded_seconds='0.31', wall_seconds='0.4')
        path = self.fixture.artifact / 'native.stdout'
        path.write_text(''.join(json.dumps(event) + '\n' for event in events))
        report = self.audit()
        self.assertTrue(report['ok'], report)
        self.assertTrue(report['verified_is_unbounded'])


if __name__ == '__main__':
    unittest.main()
