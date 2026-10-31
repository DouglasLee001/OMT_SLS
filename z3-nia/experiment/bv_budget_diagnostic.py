"""Two budgets for the existing qfnia bounded-BV branch, via tactic2solver.

No source bounds or encodings are added. The built-in goal precision and model
converters remain attached to the complete tactic, including UNDER results.
"""
import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).with_name('closed_bound_diagnostic.py')
_spec = importlib.util.spec_from_file_location('bv_closed_runner', _path)
runner = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = runner
_spec.loader.exec_module(runner)
Config = runner.Config
ARMS = ['bv_control', 'bv_extended']
UINT_MAX = 4294967295
PIPELINE = [
    'simplify', 'propagate-values', 'solve-eqs',
    'ctx-simplify(max_depth=30,max_steps=5000000)',
    'simplify(pull_cheap_ite=true,local_ctx=true,local_ctx_limit=10000000)',
    'elim-uncnstr', 'lia2card(max_range=1,max_ite_nesting=1)', 'card2bv',
    'skip-if-failed(cofactor-term-ite(max_memory=20))',
    'simplify(hoist_mul=true)', 'nla2bv(max_bv_size=64)',
    'skip-if-failed(bv-cleanup/bit-blast/sat)', 'fail-if-undecided',
]

def arm_order(index):
    return runner.arm_order(index, ARMS)

def policy(arm):
    runner._require(arm in ARMS, 'unknown BV budget arm')
    return dict(tactic='qfnia-bv', pipeline=list(PIPELINE), nla2bv_max_bv_size=64,
                heuristic_bv_bits=4, sat_max_conflicts=500 if arm == 'bv_control' else UINT_MAX,
                engine_max_memory_mb=100 if arm == 'bv_control' else None,
                preamble_cofactor_max_memory_mb=20, precision='builtin tactic2solver',
                cofactor_parameter_api='With(Cond(Probe(0),unused simplify,cofactor-term-ite),max_memory=20)',
                lia2card_module_parameters={'tactic.lia2card.max_range': 1,
                                           'tactic.lia2card.max_ite_nesting': 1},
                added_source_bounds=False)


def _bv_cleanup(z3, arm):
    limits = policy(arm)
    common = dict(flat=False, hi_div0=True, elim_and=True, blast_distinct=True)
    local = dict(common, local_ctx=True, local_ctx_limit=10000000)
    engine = dict(max_conflicts=limits['sat_max_conflicts'])
    if limits['engine_max_memory_mb'] is not None:
        engine['max_memory'] = limits['engine_max_memory_mb']
    return z3.With(z3.Then(z3.Tactic('simplify'), z3.Tactic('propagate-values'),
                           z3.With(z3.Tactic('simplify'), **local), z3.Tactic('max-bv-sharing'),
                           # The public With API validates descriptors. Group
                           # these stages so SAT's max_conflicts descriptor is
                           # available while both receive the same memory cap,
                           # exactly as the C++ branch forwards mem_p to both.
                           z3.With(z3.Then(z3.Tactic('bit-blast'), z3.Tactic('sat')), **engine)), **common)


def solver_factory(z3, arm):
    metadata = policy(arm)
    # lia2card reads these through tactic_params/gparams but does not advertise
    # them in collect_param_descrs, so public With rejects them. This factory
    # runs only inside its one-arm child; the official module parameter API
    # supplies the same C++ preamble values without changing the parent or
    # dropping its 0/1-variable throttle.
    for name, value in metadata['lia2card_module_parameters'].items():
        z3.set_param(name, value)
        runner._require(z3.get_param(name) == str(value), 'lia2card module parameter mismatch')
    preamble = z3.Then(
        z3.Tactic('simplify'), z3.Tactic('propagate-values'), z3.Tactic('solve-eqs'),
        z3.With(z3.Tactic('ctx-simplify'), max_depth=30, max_steps=5000000),
        z3.With(z3.Tactic('simplify'), pull_cheap_ite=True, local_ctx=True, local_ctx_limit=10000000),
        z3.Tactic('elim-uncnstr'),
        z3.Tactic('lia2card'),
        z3.Tactic('card2bv'),
        # cofactor consumes max_memory but omits its public descriptor. Cond
        # unions descriptors and forwards parameters to both children; its
        # constant-false probe executes only cofactor. The unused simplify is
        # solely a public descriptor carrier, never an extra transformation.
        z3.OrElse(z3.With(z3.Cond(z3.Probe(0), z3.Tactic('simplify'),
                                 z3.Tactic('cofactor-term-ite')), max_memory=20),
                  z3.Tactic('skip')))
    branch = z3.Then(
        z3.With(z3.Tactic('simplify'), hoist_mul=True),
        z3.With(z3.Tactic('nla2bv'), nla2bv_max_bv_size=64),
        z3.OrElse(_bv_cleanup(z3, arm), z3.Tactic('skip')),
        z3.Tactic('fail-if-undecided'))
    # This is the precision-preserving public wrapper. Never flatten an
    # ApplyResult into a fresh SMT solver, which would lose UNDER and its MC.
    return z3.Then(preamble, branch).solver(), metadata

def run_case(record, config, case_dir, stop):
    return runner.run_case(record, config, case_dir, stop, arms=ARMS, worker_path=Path(__file__),
                           run_metadata=dict(version='bv-budget-diagnostic-v1', tactic='qfnia-bv',
                                             policies={arm: policy(arm) for arm in ARMS}))


if __name__ == '__main__':
    if len(sys.argv) != 4 or sys.argv[1] != '--arm':
        raise SystemExit('only private --arm PAYLOAD RECEIPT is supported')
    runner._run_arm(sys.argv[2], sys.argv[3], solver_factory=solver_factory)
