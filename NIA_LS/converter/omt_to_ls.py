#!/usr/bin/env python3
"""Parse one integer OMT query, preprocess it, and export NIA_LS_OMT v1.

Only parsing and tactics are used: this module never calls Solver.check or
Optimize.check.  The private Boolean marker follows Z3's own Optimize
preprocessing scheme, so solve-eqs can eliminate a source objective variable
without losing the expression that has to be optimized.
"""

from __future__ import annotations

import argparse
import builtins
import importlib.resources
import importlib.util
import os
from pathlib import Path
import sys
import tempfile


FORK = Path(__file__).resolve().parent / "z3_fork"
MIN_I128 = -(1 << 127)
MAX_I128 = (1 << 127) - 1
TERM_BUDGET = 1024


class ConversionError(ValueError):
    """An input cannot be translated exactly into the supported format."""


def load_z3():
    """Load matching Python bindings and the library of the existing fork."""
    if "z3" in sys.modules:
        z3 = sys.modules["z3"]
        # Generated z3core.py deletes its temporary CDLL global after binding
        # all functions. Record ownership ourselves instead of inspecting it.
        if getattr(z3, "_nia_ls_omt_sdk_root", None) != str(FORK):
            raise ConversionError(
                "another Z3 library is already loaded; run omt_to_ls.py in a "
                "separate Python process to use the converter's matching fork"
            )
        return z3
    sdk = FORK / "build/python"
    library = FORK / "build/libz3.so"
    if not (sdk / "z3/__init__.py").is_file() or not library.is_file():
        raise ConversionError(
            f"Z3 fork SDK is missing; build {FORK} with Python bindings first"
        )
    # Older generated bindings import the backport although Python 3.12 has
    # the same interface in its standard library.
    if importlib.util.find_spec("importlib_resources") is None:
        sys.modules["importlib_resources"] = importlib.resources
    builtins.Z3_LIB_DIRS = [str(library.parent)]
    sys.path.insert(0, str(sdk))
    import z3
    z3._nia_ls_omt_sdk_root = str(FORK)
    return z3


def _tokens(source):
    i = 0
    while i < len(source):
        ch = source[i]
        if ch.isspace():
            i += 1
        elif ch == ";":
            end = source.find("\n", i)
            i = len(source) if end < 0 else end + 1
        elif ch in "()":
            yield ch
            i += 1
        elif ch == "|":
            end = source.find("|", i + 1)
            if end < 0:
                raise ConversionError("unterminated quoted SMT-LIB symbol")
            yield source[i:end + 1]
            i = end + 1
        elif ch == '"':
            start = i
            i += 1
            while i < len(source):
                if source[i] == '"':
                    if i + 1 < len(source) and source[i + 1] == '"':
                        i += 2
                    else:
                        i += 1
                        yield source[start:i]
                        break
                else:
                    i += 1
            else:
                raise ConversionError("unterminated SMT-LIB string")
        else:
            start = i
            while i < len(source) and not source[i].isspace() and source[i] not in "();":
                i += 1
            yield source[start:i]


def _commands(source):
    top, stack = [], []
    for token in _tokens(source):
        if token == "(":
            form = []
            (stack[-1] if stack else top).append(form)
            stack.append(form)
        elif token == ")":
            if not stack:
                raise ConversionError("unmatched closing SMT-LIB parenthesis")
            stack.pop()
        else:
            (stack[-1] if stack else top).append(token)
    if stack:
        raise ConversionError("unclosed SMT-LIB parenthesis")
    return top


def _render(form):
    if isinstance(form, str):
        return form
    return "(" + " ".join(_render(x) for x in form) + ")"


def _query(source):
    kept, objective, direction = [], None, None
    checks = 0
    accepted = {
        "declare-const", "declare-fun", "define-fun", "define-fun-rec",
        "define-funs-rec", "declare-sort", "define-sort", "assert", "set-logic",
    }
    for form in _commands(source):
        if not isinstance(form, list) or not form or not isinstance(form[0], str):
            raise ConversionError("expected top-level SMT-LIB commands")
        command = form[0]
        if command == "exit":
            break
        if checks and command not in {"echo", "set-info"} and not command.startswith("get-"):
            raise ConversionError(
                f"command after check-sat would change the query state: {command}"
            )
        if command in {"minimize", "maximize"}:
            if objective is not None or len(form) != 2:
                raise ConversionError("expected exactly one scalar minimize or maximize objective")
            objective = form[1]
            direction = "min" if command == "minimize" else "max"
            # Parse both senses as minimization, so Optimize.objectives() does
            # not negate a maximize expression. Direction is retained above.
            kept.append(["minimize", objective])
        elif command == "check-sat":
            checks += 1
            if len(form) != 1 or checks > 1:
                raise ConversionError("only a single nonincremental OMT query is supported")
        elif command in {"set-option", "set-info", "echo"} or command.startswith("get-"):
            # Do not execute queries, solver settings, output commands, or
            # benchmark-specific optimization settings in the parser.
            continue
        elif command in accepted:
            kept.append(form)
        else:
            raise ConversionError(f"unsupported SMT-LIB command: {command}")
    if objective is None:
        raise ConversionError("expected exactly one scalar minimize or maximize objective")
    return "\n".join(_render(x) for x in kept), direction, _render(objective)


def _hex(value):
    return value.encode("utf-8").hex() or "-"


class _Variables:
    def __init__(self, z3):
        self.z3 = z3
        self.items = []
        self.by_decl = {}
        self.used_names = set()
        self.reserved_names = set()

    def reserve(self, expressions):
        seen, todo = set(), list(expressions)
        while todo:
            expression = todo.pop()
            if expression.get_id() in seen:
                continue
            seen.add(expression.get_id())
            if self.z3.is_quantifier(expression):
                raise ConversionError("quantifiers are not supported: expected QF_NIA")
            if not self.z3.is_app(expression):
                raise ConversionError("bound or non-application expressions are not supported")
            if expression.decl().kind() == self.z3.Z3_OP_UNINTERPRETED:
                if expression.num_args():
                    raise ConversionError(f"uninterpreted function is not supported: {expression.decl().name()}")
                if expression.sort().kind() not in {self.z3.Z3_INT_SORT, self.z3.Z3_BOOL_SORT}:
                    raise ConversionError(f"unsupported variable sort: {expression.sort()}")
                self.reserved_names.add(str(expression.decl().name()))
            todo.extend(expression.children())

    def add(self, expression):
        declaration = expression.decl()
        key = declaration.get_id()
        if key not in self.by_decl:
            if expression.num_args() or declaration.kind() != self.z3.Z3_OP_UNINTERPRETED:
                raise ConversionError("expected a scalar Int or Bool variable")
            kind = expression.sort().kind()
            if kind not in {self.z3.Z3_INT_SORT, self.z3.Z3_BOOL_SORT}:
                raise ConversionError(f"unsupported variable sort: {expression.sort()}")
            name = str(declaration.name())
            # Distinct fresh declarations occasionally have the same printed
            # name as a source symbol. IDs remain distinct; make exported names
            # distinct too, instead of accidentally merging declarations.
            if name in self.used_names:
                stem, suffix = name, 0
                while name in self.used_names or name in self.reserved_names:
                    name = f"{stem}!nia_omt_{suffix}"
                    suffix += 1
            index = len(self.items)
            self.items.append(("I" if kind == self.z3.Z3_INT_SORT else "B", name))
            self.used_names.add(name)
            self.by_decl[key] = index
        return self.by_decl[key]

    def auxiliary(self):
        index = len(self.items)
        suffix = index
        name = f"__nia_omt_aux_{suffix}"
        while name in self.used_names or name in self.reserved_names:
            suffix += 1
            name = f"__nia_omt_aux_{suffix}"
        self.items.append(("I", name))
        self.used_names.add(name)
        return index


class _Polynomials:
    """Exact sparse integer polynomials, with bounded definitional expansion."""
    def __init__(self, z3, variables, budget=TERM_BUDGET):
        self.z3, self.variables, self.budget = z3, variables, budget
        self.cache = {}
        self.definitions = []
        self.aliases = {}

    def alias(self, polynomial):
        if len(polynomial) <= 1:
            # Constants and monomials never create distributive expansion.
            return polynomial
        key = tuple(sorted(polynomial.items()))
        if key not in self.aliases:
            variable = self.variables.auxiliary()
            replacement = {((variable, 1),): 1}
            definition = {m: -c for m, c in polynomial.items()}
            definition[((variable, 1),)] = 1
            self.definitions.append(definition)
            self.aliases[key] = replacement
        return self.aliases[key]

    def add(self, left, right, factor=1):
        result = left.copy()
        for monomial, coefficient in right.items():
            value = result.get(monomial, 0) + factor * coefficient
            if value:
                result[monomial] = value
            else:
                result.pop(monomial, None)
        if len(result) > self.budget:
            result = self.alias(result)
        return result

    def multiply(self, left, right):
        if not left or not right:
            return {}
        if len(left) * len(right) > self.budget:
            left, right = self.alias(left), self.alias(right)
        result = {}
        for a, ac in left.items():
            for b, bc in right.items():
                powers = dict(a)
                for variable, exponent in b:
                    powers[variable] = powers.get(variable, 0) + exponent
                    if powers[variable] > 2147483647:
                        raise ConversionError("a polynomial exponent exceeds signed 32-bit range")
                monomial = tuple(sorted(powers.items()))
                coefficient = result.get(monomial, 0) + ac * bc
                if coefficient:
                    result[monomial] = coefficient
                else:
                    result.pop(monomial, None)
        return result

    def of(self, expression):
        key = expression.get_id()
        if key in self.cache:
            return self.cache[key]
        z3 = self.z3
        if z3.is_int_value(expression):
            value = expression.as_long()
            result = {(): value} if value else {}
        elif not z3.is_app(expression):
            raise ConversionError("non-polynomial or quantified arithmetic is not supported")
        elif expression.decl().kind() == z3.Z3_OP_UNINTERPRETED:
            if expression.sort().kind() != z3.Z3_INT_SORT:
                raise ConversionError(f"expected an integer arithmetic variable: {expression}")
            result = {((self.variables.add(expression), 1),): 1}
        elif expression.decl().kind() in {z3.Z3_OP_ADD, z3.Z3_OP_SUB, z3.Z3_OP_UMINUS}:
            args = expression.children()
            kind = expression.decl().kind()
            if kind == z3.Z3_OP_ADD:
                result = {}
                for arg in args:
                    result = self.add(result, self.of(arg))
            elif kind == z3.Z3_OP_UMINUS:
                result = {m: -c for m, c in self.of(args[0]).items()}
            else:
                result = self.of(args[0])
                for arg in args[1:]:
                    result = self.add(result, self.of(arg), -1)
        elif expression.decl().kind() == z3.Z3_OP_MUL:
            result = {(): 1}
            for arg in expression.children():
                result = self.multiply(result, self.of(arg))
        elif expression.decl().kind() == z3.Z3_OP_POWER:
            base, exponent = expression.children()
            if not z3.is_int_value(exponent) or not 0 <= exponent.as_long() <= 2147483647:
                raise ConversionError("only nonnegative integer polynomial exponents are supported")
            power = exponent.as_long()
            result, factor = {(): 1}, self.of(base)
            while power:
                if power & 1:
                    result = self.multiply(result, factor)
                power >>= 1
                if power:
                    factor = self.multiply(factor, factor)
        else:
            operator = str(expression.decl().name())
            raise ConversionError(f"unsupported non-polynomial arithmetic operator: {operator}")
        self.cache[key] = result
        return result


def _preprocess(z3, hard, objective, enabled, keep_definitions=False):
    # The marker is fresh and has no other occurrences. Its interpretation can
    # always be chosen true, so adding it restricts no integer assignment.
    marker = z3.Function(z3.FreshConst(z3.BoolSort(), "omt_marker_name").decl().name(),
                         z3.IntSort(), z3.BoolSort())
    goal = z3.Goal()
    goal.add(*hard, marker(objective))
    simplify = z3.With(z3.Tactic("simplify"), som=False, mul_to_power=False,
                       arith_lhs=True, blast_distinct=True)
    stages = [simplify]
    if enabled:
        # Opt in for encodings whose auxiliary products must stay low-degree.
        # The default branch preserves the original preprocessing exactly.
        stages.extend([z3.Tactic("propagate-values"),
                       z3.With(z3.Tactic("solve-eqs"), theory_solver=False)
                       if keep_definitions else z3.Tactic("solve-eqs"), simplify])
    stages.extend([z3.Tactic("elim-term-ite"), simplify])
    for stage in stages:
        result = stage(goal)
        if len(result) != 1:
            raise ConversionError("preprocessing unexpectedly split the input into several goals")
        goal = result[0]
        if goal.inconsistent():
            return [], z3.IntVal(0), True
        marked = [atom for atom in goal if z3.is_app(atom) and atom.decl() == marker]
        if len(marked) != 1:
            raise ConversionError("objective marker was lost or duplicated during preprocessing")
    rewritten_objective = marked[0].arg(0)
    plain = z3.Goal()
    plain.add(*[atom for atom in goal if not (z3.is_app(atom) and atom.decl() == marker)])
    result = z3.Tactic("tseitin-cnf")(plain)
    if len(result) != 1:
        raise ConversionError("CNF conversion unexpectedly produced several goals")
    return list(result[0]), rewritten_objective, result[0].inconsistent()


def _check_i128(value):
    if not MIN_I128 <= value <= MAX_I128:
        raise ConversionError(f"integer coefficient or constant exceeds signed 128-bit range: {value}")
    return str(value)


def _polynomial_lines(polynomial):
    constant = polynomial.get((), 0)
    terms = [(m, c) for m, c in sorted(polynomial.items()) if m and c]
    lines = [f"{_check_i128(constant)} {len(terms)}"]
    for monomial, coefficient in terms:
        factors = " ".join(f"{variable} {exponent}" for variable, exponent in monomial)
        lines.append(f"{_check_i128(coefficient)} {len(monomial)} {factors}")
    return lines


def convert_text(source: str, source_name: str = "", preprocess: bool = True,
                 *, keep_definitions: bool = False) -> str:
    """Convert an OMT source string without running a solver.

    `preprocess=False` disables optional propagation and equation elimination;
    exact expression normalization, ITE elimination and CNF encoding remain
    necessary format conversions. Unsupported inputs raise ConversionError.
    """
    z3 = load_z3()
    query, direction, original_objective = _query(source)
    opt = z3.Optimize()
    try:
        opt.from_string(query)
        objectives = opt.objectives()
        if len(objectives) != 1:
            raise ConversionError("expected one arithmetic optimization objective")
        objective = objectives[0]
        if objective.sort().kind() != z3.Z3_INT_SORT:
            raise ConversionError(f"objective must have integer sort, found {objective.sort()}")
        hard = list(opt.assertions())
        variables = _Variables(z3)
        variables.reserve([*hard, objective])
        cnf, objective, unsat = _preprocess(z3, hard, objective, preprocess, keep_definitions)
        variables.reserve([*cnf, objective])
        polynomial = _Polynomials(z3, variables)
        objective_polynomial = None if preprocess and keep_definitions else polynomial.of(objective)
        literals, literal_ids, clauses = [], {}, []

        def intern(kind, value):
            key = (kind, value if kind == "B" else tuple(sorted(value.items())))
            if key not in literal_ids:
                literal_ids[key] = len(literals) + 1
                literals.append((kind, value))
            return literal_ids[key]

        def atom_literal(atom):
            sign = 1
            while z3.is_not(atom):
                sign = -sign
                atom = atom.arg(0)
            if z3.is_true(atom):
                return sign > 0
            if z3.is_false(atom):
                return sign < 0
            if z3.is_const(atom) and atom.decl().kind() == z3.Z3_OP_UNINTERPRETED:
                if atom.sort().kind() != z3.Z3_BOOL_SORT:
                    raise ConversionError("expected Boolean CNF atom")
                return sign * intern("B", variables.add(atom))
            if not z3.is_app(atom) or atom.num_args() != 2:
                raise ConversionError(f"unsupported CNF atom: {atom}")
            kind = atom.decl().kind()
            left, right = atom.children()
            comparison = {z3.Z3_OP_LE, z3.Z3_OP_GE, z3.Z3_OP_LT, z3.Z3_OP_GT, z3.Z3_OP_EQ}
            if kind not in comparison or left.sort().kind() != z3.Z3_INT_SORT or right.sort().kind() != z3.Z3_INT_SORT:
                raise ConversionError(f"unsupported CNF atom: {atom}")
            if kind in {z3.Z3_OP_GE, z3.Z3_OP_GT}:
                left, right = right, left
            value = polynomial.add(polynomial.of(left), polynomial.of(right), -1)
            if kind in {z3.Z3_OP_LT, z3.Z3_OP_GT}:
                value = polynomial.add(value, {(): 1})
            literal_kind = "E" if kind == z3.Z3_OP_EQ else "L"
            if not any(m for m in value):
                constant = value.get((), 0)
                truth = constant == 0 if literal_kind == "E" else constant <= 0
                return truth if sign > 0 else not truth
            return sign * intern(literal_kind, value)

        if unsat:
            clauses.append([])
        else:
            for expression in cnf:
                atoms = expression.children() if z3.is_or(expression) else [expression]
                clause, tautology = [], False
                for atom in atoms:
                    literal = atom_literal(atom)
                    if literal is True:
                        tautology = True
                        break
                    if literal is False:
                        continue
                    if -literal in clause:
                        tautology = True
                        break
                    if literal not in clause:
                        clause.append(literal)
                if not tautology:
                    clauses.append(clause)
        if preprocess and keep_definitions:
            objective_polynomial = polynomial.of(objective)
        # Definitional polynomial aliases bound expansion without changing
        # feasible objective values: each fresh integer equals its expression.
        for definition in polynomial.definitions:
            clauses.append([intern("E", definition)])
        status = "unsat" if any(not clause for clause in clauses) else "ready"
        lines = ["NIA_LS_OMT 1", f"STATUS {status}", f"SOURCE {_hex(source_name)}",
                 f"ORIGINAL_OBJECTIVE {_hex(original_objective)}", f"DIRECTION {direction}",
                 f"VARIABLES {len(variables.items)}"]
        lines.extend(f"{index} {kind} {_hex(name)}" for index, (kind, name) in enumerate(variables.items))
        lines.append(f"LITERALS {len(literals)}")
        for index, (kind, value) in enumerate(literals, 1):
            if kind == "B":
                lines.append(f"{index} B {value}")
            else:
                encoded = _polynomial_lines(value)
                lines.append(f"{index} {kind} {encoded[0]}")
                lines.extend(encoded[1:])
        lines.append(f"CLAUSES {len(clauses)}")
        lines.extend(str(len(clause)) + (" " + " ".join(map(str, clause)) if clause else "") for clause in clauses)
        encoded = _polynomial_lines(objective_polynomial)
        lines.append(f"OBJECTIVE {encoded[0]}")
        lines.extend(encoded[1:])
        lines.append("END")
        return "\n".join(lines) + "\n"
    except z3.Z3Exception as exc:
        raise ConversionError(f"Z3 parsing or preprocessing failed: {exc}") from exc


def _atomic_write(path, contents):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                         prefix=f".{path.name}.", suffix=".tmp",
                                         dir=path.parent, delete=False) as out:
            temporary = Path(out.name)
            out.write(contents)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="single-objective integer OMT SMT-LIB file")
    parser.add_argument("-o", "--output", default="-", help="native output path, or - for stdout")
    parser.add_argument("--preprocess", choices=("on", "off"), default="on")
    parser.add_argument("--keep-definitions", action="store_true",
                        help="keep arithmetic definitions to avoid raising polynomial degrees")
    args = parser.parse_args(argv)
    try:
        if args.output != "-" and Path(args.output).resolve() == args.input.resolve():
            raise ConversionError("output must not overwrite the original SMT-LIB input")
        source = args.input.read_text(encoding="utf-8")
        converted = convert_text(source, str(args.input.resolve()), args.preprocess == "on",
                                 keep_definitions=args.keep_definitions)
        if args.output == "-":
            sys.stdout.write(converted)
        else:
            _atomic_write(Path(args.output), converted)
        return 0
    except (ConversionError, RecursionError) as exc:
        print(f"omt_to_ls: {exc}", file=sys.stderr)
        return 2
    except (OSError, UnicodeError) as exc:
        print(f"omt_to_ls: file error: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
