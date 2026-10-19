#!/usr/bin/env python3
"""Parse all examples, check witnesses, and obtain two small exact references.

Requires z3-solver. These checks are not a general equivalence proof or a solver
performance study. The larger cases are not claimed solved to optimality.
"""
import itertools
import json
from pathlib import Path
import sys
import z3
from convert_models import parse_polynomial, read_dzn

ROOT = Path(__file__).resolve().parent


def read_assertions(path):
    lines = [line for line in path.read_text().splitlines()
             if not line.startswith(("(minimize", "(maximize", "(check-sat", "(get-model", "(get-objectives"))]
    return list(z3.parse_smt2_string("\n".join(lines)))


def det(matrix):
    a = [row[:] for row in matrix]
    previous, sign, n = 1, 1, len(a)
    for k in range(n-1):
        if a[k][k] == 0:
            pivot = next((i for i in range(k+1, n) if a[i][k]), None)
            if pivot is None:
                return 0
            a[k], a[pivot] = a[pivot], a[k]
            sign = -sign
        pivot = a[k][k]
        for i in range(k+1, n):
            for j in range(k+1, n):
                numerator = a[i][j]*pivot-a[i][k]*a[k][j]
                assert numerator % previous == 0
                a[i][j] = numerator // previous
            a[i][k] = 0
        previous = pivot
    return sign*a[-1][-1]


def information(vectors, counts):
    d = len(vectors[0])
    return [[sum(counts[k]*v[i]*v[j] for k, v in enumerate(vectors))
             for j in range(d)] for i in range(d)]


def compositions(total, length):
    if length == 1:
        yield (total,)
    else:
        for value in range(total+1):
            for rest in compositions(total-value, length-1):
                yield (value,)+rest


def witness_check(assertions, values, expected=None):
    substitutions = [(z3.Int(k), z3.IntVal(v)) for k, v in values.items()]
    solver = z3.Solver()
    solver.set(timeout=5000)
    solver.add(*[z3.simplify(z3.substitute(a, *substitutions)) for a in assertions])
    result = solver.check()
    assert result == z3.sat, result
    value = solver.model().eval(z3.Int("cost"), model_completion=True).as_long()
    if expected is not None:
        assert value == expected, (value, expected)
    return value


def main():
    source_dir = Path(sys.argv[1]) if len(sys.argv)>1 else ROOT / "downloaded_sources"
    manifest = json.loads((ROOT/"generated/manifest.json").read_text())
    report = {"z3_version": z3.get_version_string(), "parsed": 0, "checks": [],
              "limits": "Pilot translations. No full optimality/performance claim for the larger instances."}
    for item in manifest:
        path = ROOT / "generated" / item["family"] / (item["name"]+".smt2")
        assertions = read_assertions(path)
        report["parsed"] += 1
        if item["family"] == "portfolio":
            # Find a feasible holding vector using only the original linear
            # budget/return constraints, then check the complete NIA formula.
            solver = z3.SolverFor("QF_LIA")
            solver.set(timeout=5000)
            solver.add(*assertions[:-1])
            assert solver.check() == z3.sat
            model = solver.model()
            values = {f"i{i}": model.eval(z3.Int(f"i{i}"),model_completion=True).as_long()
                      for i in range(1,item["variables"]+1)}
            value = witness_check(assertions, values)
            report["checks"].append({"name":item["name"], "feasible_cost":value})
            if item["name"] == "smallinvDAXr1b150-165":
                point = {f"i{i}":0 for i in range(1,31)}
                point.update({"i4":35,"i5":47,"i6":46,"i8":32,"i22":28,"i23":1,"i26":39})
                expected = 8810493476 * item["risk_scale"] // 100000000
                value = witness_check(assertions, point, expected)
                report["checks"].append({"name":item["name"], "published_point_checked":True,
                    "risk":"88.10493476", "exact_scaled_cost":value,
                    "source":"https://www.minlplib.org/sol/smallinvDAXr1b150-165.p2.sol"})
        elif item["family"] == "cyclic":
            data = read_dzn(source_dir / ("cyclic_"+item["name"]+".dzn"))
            n, tmax = data["n"], item["t_max"]
            fixed = [(z3.Int(f"k{i}"), z3.IntVal(0)) for i in range(1,n+1)]
            fixed.append((z3.Int(f"s{n}"),z3.IntVal(tmax)))
            solver = z3.SolverFor("QF_LIA")
            solver.set(timeout=5000)
            solver.add(*[z3.simplify(z3.substitute(a,*fixed)) for a in assertions])
            result = solver.check()
            assert result == z3.sat, (item["name"],result)
            model = solver.model()
            s = {i:(tmax if i==n else model.eval(z3.Int(f"s{i}"),model_completion=True).as_long()) for i in range(1,n+1)}
            k = {i:0 for i in range(1,n+1)}
            values = {f"s{i}":s[i] for i in s}|{f"k{i}":k[i] for i in k}
            for i,j,latency,distance in data["prec"]:
                assert k[i]+int(s[i]+latency>s[j]) <= k[j]+distance
            for q in range(data["r"]):
                for t in set(s.values()):
                    load = sum(data["req"][i-1][q] for i in range(2,n) if s[i]==t)
                    assert load <= data["cap"][q]
            offsets = [s[i]-k[i]*s[n] for i in range(1,n)]
            expected = tmax*s[n]+max(offsets)-min(offsets)+1
            value = witness_check(assertions,values,expected)
            report["checks"].append({"name":item["name"],"independent_schedule_check":True,"feasible_cost":value})
        elif item["family"] == "packing":
            n = item["n"]
            values = {"width":n*(n+1)//2,"height":n}
            for i in range(1,n+1):
                values[f"x{i}"] = i*(i-1)//2
                values[f"y{i}"] = 0
            value = witness_check(assertions,values,n*n*(n+1)//2)
            report["checks"].append({"name":item["name"],"strip_witness_cost":value})
            if n == 3:
                optimal = {"width":5,"height":3,"x1":3,"y1":2,"x2":3,"y2":0,"x3":0,"y3":0}
                witness_check(assertions,optimal,15)
                solver=z3.Solver();solver.set(timeout=5000)
                solver.add(*assertions,z3.Int("cost")<15)
                assert solver.check()==z3.unsat
                report["checks"].append({"name":item["name"],"proved_optimum":15,"method":"feasible witness + UNSAT below 15"})
        elif item["family"] == "design":
            d,m,N=item["dimension"],item["experiments"],item["budget"]
            counts=[N-d+1]+[1]*(d-1)+[0]*(m-d)
            expected=det(information(item["vectors"],counts))
            witness_check(assertions,{f"n{i}":counts[i] for i in range(m)},expected)
            report["checks"].append({"name":item["name"],"exact_determinant_witness":expected})
            if d == 3:
                candidates=list(compositions(N,m))
                best=max(det(information(item["vectors"],counts)) for counts in candidates)
                report["checks"].append({"name":item["name"],"proved_optimum":best,
                    "method":"complete exact enumeration of all integer run allocations",
                    "allocations_enumerated":len(candidates)})
    (ROOT/"verification.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n")
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=="__main__":
    main()
