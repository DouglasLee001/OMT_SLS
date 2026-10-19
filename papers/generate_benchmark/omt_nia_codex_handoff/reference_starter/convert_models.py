#!/usr/bin/env python3
"""Small, auditable conversions of application models to QF_NIA optimization.

Uses the original MiniZinc square_pack/rcmsp and MINLPLib smallinv objectives.
The D-optimal cases are NEW integer-coefficient examples of the published model,
not reproductions of the Boscia paper's floating-point datasets.
Only the Python standard library is required to generate the files.
"""
import argparse
import ast
from fractions import Fraction
import hashlib
import itertools
import json
import math
from pathlib import Path
import random
import re
import urllib.request

PORTFOLIOS = ["smallinvDAXr1b010-011", "smallinvDAXr1b150-165",
              "smallinvDAXr3b100-110", "smallinvDAXr5b200-220"]
CYCLIC = ["easy_1", "easy_2", "medium_1", "hard_1"]
MZN_BASE = "https://raw.githubusercontent.com/MiniZinc/minizinc-benchmarks/master/"


def add(a, b):
    out = dict(a)
    for k, v in b.items():
        out[k] = out.get(k, Fraction(0)) + v
    return {k: v for k, v in out.items() if v}


def mul(a, b):
    out = {}
    for ka, va in a.items():
        for kb, vb in b.items():
            key = tuple(sorted(ka + kb))
            out[key] = out.get(key, Fraction(0)) + va * vb
    return {k: v for k, v in out.items() if v}


def parse_polynomial(text):
    """Parse a tiny GAMS polynomial grammar, retaining exact decimal tokens."""
    text = re.sub(r"\s+", "", text)

    def visit(node):
        if isinstance(node, ast.Constant):
            return {(): Fraction(ast.get_source_segment(text, node))}
        if isinstance(node, ast.Name) and re.fullmatch(r"i\d+|objvar", node.id):
            return {(node.id,): Fraction(1)}
        if isinstance(node, ast.UnaryOp):
            val = visit(node.operand)
            if isinstance(node.op, ast.USub):
                return {k: -v for k, v in val.items()}
            if isinstance(node.op, ast.UAdd):
                return val
        if isinstance(node, ast.BinOp):
            a, b = visit(node.left), visit(node.right)
            if isinstance(node.op, ast.Add):
                return add(a, b)
            if isinstance(node.op, ast.Sub):
                return add(a, {k: -v for k, v in b.items()})
            if isinstance(node.op, ast.Mult):
                return mul(a, b)
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "sqr" and len(node.args) == 1):
            val = visit(node.args[0])
            return mul(val, val)
        raise ValueError("Unsupported polynomial syntax: " + ast.dump(node))

    return visit(ast.parse(text, mode="eval").body)


def number(n):
    n = int(n)
    return str(n) if n >= 0 else f"(- {-n})"


def summation(terms):
    terms = list(terms)
    return "0" if not terms else terms[0] if len(terms) == 1 else "(+ " + " ".join(terms) + ")"


def disjunction(terms):
    terms = list(terms)
    return "false" if not terms else terms[0] if len(terms) == 1 else "(or " + " ".join(terms) + ")"


def polynomial_smt(poly):
    scale = math.lcm(*(v.denominator for v in poly.values()))
    terms = []
    for variables, value in sorted(poly.items()):
        value = int(value * scale)
        factors = [number(value)] + list(variables)
        terms.append(factors[0] if len(factors) == 1 else "(* " + " ".join(factors) + ")")
    return summation(terms), scale


class Model:
    def __init__(self, description, source):
        self.lines = ["; " + description, "; Source: " + source, "(set-logic QF_NIA)"]

    def integer(self, name, lo=None, hi=None):
        self.lines.append(f"(declare-fun {name} () Int)")
        if lo is not None:
            self.assertion(f"(>= {name} {number(lo)})")
        if hi is not None:
            self.assertion(f"(<= {name} {number(hi)})")

    def assertion(self, expr):
        self.lines.append(f"(assert {expr})")

    def write(self, target, direction="minimize"):
        self.lines += [f"({direction} cost)", "(check-sat)", "(get-model)", "(get-objectives)"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join(self.lines) + "\n")


def portfolio(path, target):
    name = path.stem
    source = f"https://www.minlplib.org/{name}.html"
    text = path.read_text()
    variables_text = re.search(r"Integer Variables(.*?);", text, re.S).group(1)
    variables = re.findall(r"\bi\d+\b", variables_text)
    equations = re.findall(r"\b(e\d+)\.\.(.*?)=([LGE])=(.*?);", text, re.S)
    parsed = [(n, add(parse_polynomial(a), {k: -v for k, v in parse_polynomial(b).items()}), op)
              for n, a, op, b in equations]
    _, objective, op = parsed[0]
    assert op == "L" and objective.pop(("objvar",)) == -1
    assert all("objvar" not in k for _, p, _ in parsed[1:] for k in p)
    assert max(map(len, objective)) == 2
    # In the upstream AMPL formulation all i variables are integer >= 0.
    # objvar occurs only in Q(i) <= objvar and in the objective: eliminate it.
    m = Model("Exact-decimal integer mean-variance portfolio: " + name, source)
    for v in variables:
        m.integer(v, 0)
    for _, p, relation in parsed[1:]:
        expr, _ = polynomial_smt(p)
        operator = {'L': '<=', 'G': '>=', 'E': '='}[relation]
        m.assertion(f"({operator} {expr} 0)")
    expr, scale = polynomial_smt(objective)
    m.lines.append(f"; Original risk = cost / {scale}; positive exact scaling, no rounding.")
    m.integer("cost")
    m.assertion(f"(= cost {expr})")
    m.write(target)
    return {"name": name, "family": "portfolio", "variables": len(variables),
            "source": source, "risk_scale": scale, "maximum_degree": 2,
            "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def read_dzn(path):
    text = re.sub(r"%[^\n]*", "", path.read_text())
    scalar = lambda n: int(re.search(r"\b" + n + r"\s*=\s*(-?\d+)\s*;", text).group(1))
    def array(name):
        body = re.search(r"\b" + name + r"\s*=\s*\[(.*?)\]\s*;", text, re.S).group(1)
        return [int(x) for x in re.findall(r"-?\d+", body)]
    n, r = scalar("n_tasks"), scalar("n_res")
    req = array("rreq")
    prec = array("prec")
    assert len(req) == n * r and len(prec) == scalar("n_prec") * 4
    return {"n": n, "r": r, "cap": array("rcap"),
            "req": [req[i*r:(i+1)*r] for i in range(n)],
            "prec": [prec[i:i+4] for i in range(0, len(prec), 4)]}


def cyclic(path, target):
    data = read_dzn(path)
    n, r = data["n"], data["r"]
    tmax = sum(max(0, p[2]) for p in data["prec"])
    name = path.stem.removeprefix("cyclic_")
    source = MZN_BASE + "cyclic-rcpsp/rcmsp.mzn"
    m = Model("Integer resource-constrained modulo scheduling: " + name, source)
    duration = lambda i: 0 if i in (1, n) else 1
    for i in range(1, n+1):
        m.integer(f"s{i}", 0, tmax)
        m.integer(f"k{i}", 0, n)
    for i, j, latency, distance in data["prec"]:
        late = f"(ite (<= (+ s{i} {number(latency)}) s{j}) 0 1)"
        m.assertion(f"(<= (+ k{i} {late}) (+ k{j} {number(distance)}))")
    for i in range(1, n+1):
        for j in range(i+1, n+1):
            if any(data["req"][i-1][q] + data["req"][j-1][q] > data["cap"][q] for q in range(r)):
                m.assertion(f"(or (<= (+ s{i} {duration(i)}) s{j}) (<= (+ s{j} {duration(j)}) s{i}))")
    # All non-dummy tasks have integer starts and duration 1. Resource usage
    # can change only at starts/ends; checking every task start is exact.
    for q in range(r):
        tasks = [i for i in range(2, n) if data["req"][i-1][q] > 0]
        if sum(data["req"][i-1][q] for i in tasks) <= data["cap"][q]:
            continue
        for i in tasks:
            load = summation(f"(ite (= s{i} s{j}) {data['req'][j-1][q]} 0)" for j in tasks)
            m.assertion(f"(<= {load} {data['cap'][q]})")
    for i in range(1, n):
        m.assertion(f"(<= (+ s{i} {duration(i)}) s{n})")
        m.assertion(f"(>= k{n} k{i})")
    m.assertion(disjunction(f"(= k{n} k{i})" for i in range(1, n)))
    m.assertion("(= s1 0)")
    m.assertion("(= k1 0)")
    # Retain the essential variable products k_i * T, with T = s_n.
    m.integer("first_event")
    m.integer("last_event")
    offsets = [f"(- s{i} (* k{i} s{n}))" for i in range(1, n)]
    for expr in offsets:
        m.assertion(f"(<= first_event {expr})")
        m.assertion(f"(>= last_event {expr})")
    m.assertion(disjunction(f"(= first_event {expr})" for expr in offsets))
    m.assertion(disjunction(f"(= last_event {expr})" for expr in offsets))
    m.integer("makespan", 0, tmax)
    m.assertion("(= makespan (+ (- last_event first_event) 1))")
    m.integer("cost", 0, tmax * (1+tmax))
    m.assertion(f"(= cost (+ (* {tmax} s{n}) makespan))")
    m.write(target)
    return {"name": name, "family": "cyclic", "tasks": n, "t_max": tmax,
            "source": source, "data_source": MZN_BASE + "cyclic-rcpsp/" + name + ".dzn",
            "maximum_degree": 2, "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def packing(n, target):
    source = "https://github.com/MiniZinc/minizinc-examples/blob/master/square_pack.mzn"
    total = n*(n+1)//2
    m = Model(f"Minimum-area square packing, original square_pack.mzn, n={n}", source)
    m.integer("width", n, total)
    m.integer("height", n, total)
    m.integer("cost", sum(i*i for i in range(1, n+1)), n*total)
    m.assertion("(= cost (* width height))")
    for i in range(1, n+1):
        m.integer(f"x{i}", 0, total)
        m.integer(f"y{i}", 0, total)
        m.assertion(f"(<= (+ x{i} {i}) width)")
        m.assertion(f"(<= (+ y{i} {i}) height)")
    for i in range(1, n+1):
        for j in range(i+1, n+1):
            m.assertion(f"(or (<= (+ x{i} {i}) x{j}) (<= (+ x{j} {j}) x{i}) (<= (+ y{i} {i}) y{j}) (<= (+ y{j} {j}) y{i}))")
    m.write(target)
    return {"name": f"square_{n:02}", "family": "packing", "n": n,
            "source": source, "maximum_degree": 2,
            "note": "Standard synthetic square family, not a measured industrial layout."}


def determinant_smt(matrix):
    n = len(matrix)
    terms = []
    for perm in itertools.permutations(range(n)):
        inversions = sum(perm[i] > perm[j] for i in range(n) for j in range(i+1, n))
        term = "(* " + " ".join(matrix[i][perm[i]] for i in range(n)) + ")"
        terms.append(f"(- {term})" if inversions % 2 else term)
    return summation(terms)


def design(dimension, experiments, budget, seed, target):
    rng = random.Random(seed)
    vectors = [[int(i == j) for j in range(dimension)] for i in range(dimension)]
    vectors += [[rng.randint(-3, 3) for _ in range(dimension)]
                for _ in range(experiments-dimension)]
    source = "https://arxiv.org/abs/2312.11200"
    m = Model("NEW integer-coefficient exact D-optimal design example; not paper data", source)
    for i in range(experiments):
        m.integer(f"n{i}", 0, budget)
    m.assertion(f"(= {summation(f'n{i}' for i in range(experiments))} {budget})")
    matrix = []
    for row in range(dimension):
        matrix.append([])
        for col in range(dimension):
            name = f"M_{row}_{col}"
            m.integer(name)
            value = summation(f"(* {number(v[row]*v[col])} n{i})" for i, v in enumerate(vectors) if v[row]*v[col])
            m.assertion(f"(= {name} {value})")
            matrix[-1].append(name)
    m.integer("cost", 1)
    m.assertion(f"(= cost {determinant_smt(matrix)})")
    m.write(target, "maximize")
    return {"name": target.stem, "family": "design", "maximum_degree": dimension,
            "dimension": dimension, "experiments": experiments, "budget": budget,
            "vectors": vectors, "seed": seed, "source": source,
            "note": "New integer input data. Maximizing det is equivalent to minimizing -log(det) on positive definite information matrices."}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sources", type=Path, default=Path("downloaded_sources"))
    parser.add_argument("--output", type=Path, default=Path("generated"))
    parser.add_argument("--fetch", action="store_true", help="Download the eight original instance inputs")
    args = parser.parse_args()
    args.sources.mkdir(parents=True, exist_ok=True)
    args.output.mkdir(parents=True, exist_ok=True)
    records = []
    for name in PORTFOLIOS:
        path = args.sources / (name + ".gms")
        if args.fetch and not path.exists():
            with urllib.request.urlopen(f"https://www.minlplib.org/gms/{name}.gms", timeout=30) as r:
                path.write_bytes(r.read())
        records.append(portfolio(path, args.output / "portfolio" / (name + ".smt2")))
    for name in CYCLIC:
        path = args.sources / ("cyclic_" + name + ".dzn")
        if args.fetch and not path.exists():
            with urllib.request.urlopen(MZN_BASE + "cyclic-rcpsp/" + name + ".dzn", timeout=30) as r:
                path.write_bytes(r.read())
        records.append(cyclic(path, args.output / "cyclic" / (name + ".smt2")))
    for n in range(3, 11):
        records.append(packing(n, args.output / "packing" / f"square_{n:02}.smt2"))
    for dim, count, runs, seed in [(3, 6, 6, 1), (4, 7, 7, 2), (5, 8, 8, 3)]:
        records.append(design(dim, count, runs, seed, args.output / "design" / f"design_d{dim}_m{count}.smt2"))
    (args.output / "manifest.json").write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"generated": len(records), "output": str(args.output)}))


if __name__ == "__main__":
    main()
