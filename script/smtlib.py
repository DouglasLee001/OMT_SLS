"""Small SMT-LIB reader preserving quoted symbols and exact objective values."""
from fractions import Fraction


def _tokens(text):
    i, length = 0, len(text)
    while i < length:
        ch = text[i]
        if ch.isspace():
            i += 1
        elif ch == ';':
            end = text.find('\n', i)
            i = length if end < 0 else end + 1
        elif ch in '()':
            yield ch
            i += 1
        elif ch == '|':
            end = text.find('|', i+1)
            if end < 0:
                raise ValueError('Unterminated quoted SMT-LIB symbol')
            yield text[i:end+1]
            i = end + 1
        elif ch == '"':
            start = i
            i += 1
            while i < length:
                if text[i] == '"':
                    if i+1 < length and text[i+1] == '"':
                        i += 2
                    else:
                        i += 1
                        yield text[start:i]
                        break
                else:
                    i += 1
            else:
                raise ValueError('Unterminated SMT-LIB string')
        else:
            start = i
            while i < length and not text[i].isspace() and text[i] not in '();':
                i += 1
            yield text[start:i]


def parse(text):
    """Parse top-level atoms/lists; do not interpret symbol contents."""
    result, stack = [], []
    for token in _tokens(text):
        if token == '(':
            form = []
            (stack[-1] if stack else result).append(form)
            stack.append(form)
        elif token == ')':
            if not stack:
                raise ValueError('Unmatched closing SMT-LIB parenthesis')
            stack.pop()
        else:
            (stack[-1] if stack else result).append(token)
    if stack:
        raise ValueError('Unclosed SMT-LIB parenthesis')
    return result


def render(form):
    if isinstance(form, str):
        return form
    return '(' + ' '.join(render(x) for x in form) + ')'


def _optimization_commands(text):
    forms = parse(text)
    effective = []
    for form in forms:
        if not isinstance(form, list) or not form:
            raise ValueError('Expected top-level SMT-LIB commands')
        if form[0] == 'exit':
            break
        effective.append(form)
    objectives = [x for x in effective if x[0] in ('minimize', 'maximize')]
    if len(objectives) != 1 or len(objectives[0]) != 2:
        raise ValueError('Expected exactly one minimize or maximize objective')
    if sum(x[0] == 'check-sat' for x in effective) != 1:
        raise ValueError('Expected exactly one check-sat command')
    return effective, objectives[0]


def objective_sense(text):
    """Return the direction of the benchmark's single effective objective."""
    _, objective = _optimization_commands(text)
    return objective[0]


def optimization_input(text, normalize_maximize=False):
    """Add result queries, optionally rewriting max f as min (- f)."""
    effective, objective_command = _optimization_commands(text)
    if normalize_maximize and objective_command[0] == 'maximize':
        objective_command[:] = ['minimize', ['-', objective_command[1]]]
    query_names = {'check-sat', 'get-objectives', 'get-model', 'get-value',
                   'get-info', 'get-option', 'get-assertions', 'get-unsat-core'}
    kept = [x for x in effective if x[0] not in query_names]
    objective = objective_command[1]
    kept.extend([['check-sat'], ['get-objectives'], ['get-value', [objective]], ['exit']])
    return '\n'.join(render(x) for x in kept)+'\n', render(objective)


def negate_value(value):
    """Negate an exact solver expression without approximating its value."""
    if value is None:
        return None
    forms = parse(value)
    if len(forms) != 1:
        raise ValueError('Expected one solver value')
    form = forms[0]
    if isinstance(form, list) and len(form) == 2 and form[0] == '-':
        return render(form[1])
    return render(['-', form])


def numeric_value(value):
    """Return exact (infinity, constant, epsilon) coefficients, or None."""
    def evaluate(form):
        if isinstance(form, str):
            if form == 'oo':
                return (Fraction(1), Fraction(0), Fraction(0))
            if form in ('epsilon', 'eps'):
                return (Fraction(0), Fraction(0), Fraction(1))
            return (Fraction(0), Fraction(form), Fraction(0))
        if not form:
            raise ValueError('Empty value')
        op, args = form[0], [evaluate(x) for x in form[1:]]
        if op == '+':
            return tuple(sum(x[i] for x in args) for i in range(3))
        if op == '-' and args:
            if len(args) == 1:
                return tuple(-x for x in args[0])
            return tuple(args[0][i]-sum(x[i] for x in args[1:]) for i in range(3))
        if op == '*' and args:
            out = (Fraction(0), Fraction(1), Fraction(0))
            for arg in args:
                if out[0] == out[2] == 0:
                    out = tuple(out[1]*x for x in arg)
                elif arg[0] == arg[2] == 0:
                    out = tuple(arg[1]*x for x in out)
                else:
                    raise ValueError('Nonlinear extended value')
            return out
        if op == '/' and len(args) == 2 and args[1][0] == args[1][2] == 0:
            return tuple(x/args[1][1] for x in args[0])
        if op == 'to_real' and len(args) == 1:
            return args[0]
        raise ValueError('Non-numeric expression')
    try:
        forms = parse(value)
        if len(forms) != 1:
            return None
        return evaluate(forms[0])
    except (ValueError, TypeError, ZeroDivisionError):
        return None


def finite_value(value):
    parts = numeric_value(value)
    if parts is not None and parts[0] == parts[2] == 0:
        return parts[1]
    return None


def strictly_better(new, old):
    new_value, old_value = finite_value(new), finite_value(old)
    return new_value is not None and (old is None or (old_value is not None and new_value < old_value))
