"""Pure, exact objective comparisons for the three-solver result table."""
from collections.abc import Mapping
from decimal import Decimal
import re


SOLVERS = ('z3', 'optimathsat', 'NIA_LS')
STATES = ('A1', 'A2', 'B', 'C')
TARGETS = ('z3', 'optimathsat', 'best_competitor')
_INTEGER = re.compile(r'[+-]?[0-9]+\Z')


def _score(value, direction, name, solver):
    """Higher tuples are better: infinity, finite objective, then no model."""
    if not isinstance(value, str):
        raise ValueError(f'{name}: invalid {solver} objective value')
    value = value.strip()
    if value == 'NA':
        return (0, 0)
    if value in ('-oo', '+oo', 'oo'):
        if ((direction == 'min' and value != '-oo')
                or (direction == 'max' and value == '-oo')):
            raise ValueError(f'{name}: {solver} infinity conflicts with optimization direction')
        return (2, 0)
    if not _INTEGER.fullmatch(value):
        raise ValueError(f'{name}: invalid {solver} integer objective value')
    # Decimal construction and integer conversion are exact and bypass Python's
    # decimal-string digit limit without changing the process-wide setting.
    number = int(Decimal(value))
    return (1, -number if direction == 'min' else number)


def _empty_group():
    return {
        'instances': 0,
        'all_na': 0,
        'best_including_na': dict.fromkeys(SOLVERS, 0),
        'best_excluding_na': dict.fromkeys(SOLVERS, 0),
        'states': {solver: dict.fromkeys(STATES, 0) for solver in SOLVERS},
        'solved': dict.fromkeys(SOLVERS, 0),
        'nia_wdl': {target: {'win': 0, 'tie': 0, 'loss': 0} for target in TARGETS},
    }


def _accumulate(group, states, scores):
    group['instances'] += 1
    best = max(scores.values())
    all_na = best[0] == 0
    group['all_na'] += int(all_na)
    for solver in SOLVERS:
        group['states'][solver][states[solver]] += 1
        group['solved'][solver] += int(states[solver] in ('A1', 'A2'))
        if scores[solver] == best:
            group['best_including_na'][solver] += 1
            if not all_na:
                group['best_excluding_na'][solver] += 1
    opponents = {'z3': scores['z3'], 'optimathsat': scores['optimathsat'],
                 'best_competitor': max(scores['z3'], scores['optimathsat'])}
    nia = scores['NIA_LS']
    for target, opponent in opponents.items():
        outcome = 'win' if nia > opponent else 'loss' if nia < opponent else 'tie'
        group['nia_wdl'][target][outcome] += 1


def compute_statistics(rows, directions):
    """Compare combined CSV rows without using runtime or proof state as a rank.

    Each row has a unique ``文件名`` and the columns ``{solver}状态`` and
    ``{solver}目标值`` for z3, optimathsat and NIA_LS. Directions map those
    filenames to ``min`` or ``max``. Finite values are exact decimal integers;
    NA ties NA, and a direction-compatible infinity report ranks above every
    finite objective (min: -oo; max: +oo or oo).

    The returned groups cover all rows and the subset where all three states
    are B/C. Solved counts use A1/A2 independently from objective comparisons.
    Invalid names, directions, states and values raise ValueError.
    """
    if not isinstance(directions, Mapping):
        raise ValueError('Directions must map instance names to min or max')
    groups = {'all': _empty_group(), 'all_unsolved': _empty_group()}
    seen = set()
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError('Each result row must be a mapping')
        name = row.get('文件名')
        if not isinstance(name, str) or not name.strip():
            raise ValueError('Result row has a missing or empty instance name')
        if name in seen:
            raise ValueError(f'duplicate instance name: {name}')
        seen.add(name)
        direction = directions.get(name)
        if direction not in ('min', 'max'):
            raise ValueError(f'{name}: missing or invalid optimization direction')
        states, scores = {}, {}
        for solver in SOLVERS:
            state = row.get(solver + '状态')
            if state not in STATES:
                raise ValueError(f'{name}: invalid {solver} state')
            states[solver] = state
            scores[solver] = _score(row.get(solver + '目标值'), direction, name, solver)
        _accumulate(groups['all'], states, scores)
        if all(state in ('B', 'C') for state in states.values()):
            _accumulate(groups['all_unsolved'], states, scores)
    return groups
