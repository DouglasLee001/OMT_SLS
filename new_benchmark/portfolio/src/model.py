"""Exact MINLPLib portfolio conversion and planted positive definite risk data.

No downloaded program is executed. GAMS support is intentionally narrow and all
unrecognized statements fail closed. Array order follows the variable list.
"""
from __future__ import annotations

from fractions import Fraction
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import ProxyHandler, build_opener
import hashlib
import json
import math
import random
import re

from nia_bench.core import Model, num, summation, source_record

ROOT = Path(__file__).resolve().parents[2]
IDS = ('smallinvDAXr1b010-011', 'smallinvDAXr1b150-165',
       'smallinvDAXr3b100-110', 'smallinvDAXr5b200-220')
# Pinned texts: first six match the handoff inventory; the additional AMPL/p1
# hashes are from actual 2026-10-19 downloads, not inferred source data.
HASHES = {
    'smallinvDAXr1b010-011.gms': 'b1e0dc55c38bb26eae20b25d6edf43af59bc0f25a47f6a46ece28e80c7e6817c',
    'smallinvDAXr1b150-165.gms': '249c0faa70ee0c735c1d35df07a2983ffe113f2353940d83bd311580b3e6ae88',
    'smallinvDAXr3b100-110.gms': '041006ac9b2f1ade9ff3abea9aaf2553b7da5818ad0c8800c2f9659cc405d0d5',
    'smallinvDAXr5b200-220.gms': 'c33243568ecc596c9da42e58df79f6a84b3c19521154250f38de0e714bd76af7',
    'smallinvDAXr1b150-165.mod': 'cf7d4c4ec29948e560ffe2cf9d618242f3f48bf6be5a1bc3f92a6151ac928be7',
    'smallinvDAXr1b010-011.mod': '678b7f70938653e5d701928a6ebd461c7a4fc979c795020bf3ce222274b3f9b9',
    'smallinvDAXr3b100-110.mod': '18052304808aa99cd855f45c8247c82a9bac46b6edf551ca6846184fc094f110',
    'smallinvDAXr5b200-220.mod': '7b04f2c89f7ebc790c1888b94fb783a0630f8dbda560c90487537a45a863901c',
    'smallinvDAXr1b150-165.p2.sol': 'dfb6b6f3f178bb2675a428afad5763dc7cbd2be96ad46ab9b1abab8fed1b2f31',
    'smallinvDAXr1b010-011.p1.sol': 'a43c4b90c8bf2de09b26bfbc3a678c477afb78291e13a66ba0519b9645256a3a',
    'smallinvDAXr3b100-110.p1.sol': '7a86114e66414d7022018966323ac4e3fda85cc2667b7bbccb8be7a01fca3d60',
    'smallinvDAXr5b200-220.p1.sol': 'f667c5cc9a3502ce95c6a033196088c9d2e7e3c11581a94f333747240e95b873',
}
NUMBER = r'(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?'
TOKEN = re.compile(r'\s*('+NUMBER+r'|[A-Za-z_][A-Za-z_0-9]*|[()+*\-])')


def rational(value):
    """Read exact JSON rationals; never accept binary floating point."""
    if isinstance(value, dict) and set(value) == {'num', 'den'}:
        n, d = value['num'], value['den']
        if not re.fullmatch(r'-?\d+', str(n)) or not re.fullmatch(r'\d+', str(d)) or int(d) <= 0:
            raise ValueError('Invalid rational numerator/denominator')
        return Fraction(int(n), int(d))
    if isinstance(value, bool) or not isinstance(value, (str, int, Fraction)):
        raise ValueError('Expected exact rational, decimal text or integer')
    try:
        return Fraction(value)
    except (ValueError, ZeroDivisionError) as exc:
        raise ValueError('Invalid exact rational') from exc


def fraction_json(value):
    value = rational(value)
    return {'num': str(value.numerator), 'den': str(value.denominator)}


def _add(left, right):
    result = dict(left)
    for key, coefficient in right.items():
        result[key] = result.get(key, Fraction(0)) + coefficient
    return {k: v for k, v in result.items() if v}


def _neg(poly):
    return {k: -v for k, v in poly.items()}


def _mul(left, right):
    result = {}
    for a, ca in left.items():
        for b, cb in right.items():
            key = tuple(sorted(a+b))
            result[key] = result.get(key, Fraction(0)) + ca*cb
    return {k: v for k, v in result.items() if v}


def parse_polynomial(text, allowed_variables=None):
    """Recursive descent for +, -, *, (), sqr and exact numeric tokens."""
    tokens = []
    position = 0
    while position < len(text):
        match = TOKEN.match(text, position)
        if not match:
            if not text[position:].strip():
                break
            raise ValueError('Unsupported polynomial token near '+repr(text[position:position+35]))
        tokens.append(match.group(1).lower())
        position = match.end()
    cursor = 0
    allowed = None if allowed_variables is None else {v.lower() for v in allowed_variables}

    def atom():
        nonlocal cursor
        if cursor >= len(tokens):
            raise ValueError('Truncated polynomial')
        token = tokens[cursor]
        cursor += 1
        if token in ('+', '-'):
            result = atom()
            return _neg(result) if token == '-' else result
        if token == '(':
            result = addition()
            if cursor >= len(tokens) or tokens[cursor] != ')':
                raise ValueError('Unclosed polynomial parentheses')
            cursor += 1
            return result
        if re.fullmatch(NUMBER, token, re.I):
            return {(): Fraction(token)} if Fraction(token) else {}
        if token == 'sqr':
            if cursor >= len(tokens) or tokens[cursor] != '(':
                raise ValueError('sqr requires parentheses')
            cursor += 1
            result = addition()
            if cursor >= len(tokens) or tokens[cursor] != ')':
                raise ValueError('sqr takes exactly one expression')
            cursor += 1
            return _mul(result, result)
        if ((allowed is not None and token in allowed) or
                (allowed is None and re.fullmatch(r'(?:i|x)\d+|objvar', token))):
            return {(token,): Fraction(1)}
        raise ValueError('Unknown polynomial variable/function: '+token)

    def multiplication():
        nonlocal cursor
        result = atom()
        while cursor < len(tokens) and tokens[cursor] == '*':
            cursor += 1
            result = _mul(result, atom())
        return result

    def addition():
        nonlocal cursor
        result = multiplication()
        while cursor < len(tokens) and tokens[cursor] in ('+', '-'):
            token = tokens[cursor]
            cursor += 1
            term = multiplication()
            result = _add(result, _neg(term) if token == '-' else term)
        return result

    result = addition()
    if cursor != len(tokens):
        raise ValueError('Unconsumed polynomial syntax: '+str(tokens[cursor:]))
    return result


def pack_polynomial(poly):
    return [{'variables': list(key), 'coefficient': fraction_json(value)}
            for key, value in sorted(poly.items()) if value]


def unpack_polynomial(terms):
    result = {}
    for term in terms:
        key = tuple(sorted(term['variables']))
        result[key] = result.get(key, Fraction(0)) + rational(term['coefficient'])
    return {k: v for k, v in result.items() if v}


def polynomial_smt(poly):
    scale = math.lcm(*(value.denominator for value in poly.values()))
    terms = []
    for variables, coefficient in sorted(poly.items()):
        scaled = coefficient*scale
        if scaled.denominator != 1:
            raise ValueError('Internal nonintegral scaled coefficient')
        factors = [num(scaled.numerator), *variables]
        terms.append(factors[0] if len(factors) == 1 else '(* '+' '.join(factors)+')')
    return summation(terms), scale


def _names(text):
    result = [s.strip().lower() for s in text.split(',')]
    if not result or any(not re.fullmatch(r'[a-z_][a-z_0-9]*', s) for s in result):
        raise ValueError('Unsupported declaration list: '+text)
    if len(set(result)) != len(result):
        raise ValueError('Repeated declaration')
    return result


def _constant(text):
    parsed = parse_polynomial(text, set())
    if any(parsed_key for parsed_key in parsed):
        raise ValueError('Expected constant')
    return parsed.get((), Fraction(0))


def _budget_and_bounds(data):
    names = [v['name'] for v in data['variables']]
    upper_candidates = []
    lower_candidates = []
    for eq in data['equations']:
        if eq['name'] == data['risk_equation']:
            continue
        poly = unpack_polynomial(eq['polynomial'])
        coefficients = {key[0]: value for key, value in poly.items() if len(key) == 1}
        if any(len(key) > 1 for key in poly) or set(coefficients) != set(names):
            continue
        if not all(c > 0 for c in coefficients.values()):
            continue
        entry = (coefficients, -poly.get((), Fraction(0)), eq['name'])
        if eq['relation'] == 'L': upper_candidates.append(entry)
        if eq['relation'] == 'G': lower_candidates.append(entry)
    matching = [(up, lo) for up in upper_candidates for lo in lower_candidates if up[0] == lo[0]]
    if not matching:
        data['bound_derivation'] = {'applied': False, 'reason': 'No recognized positive-price budget pair'}
        return
    upper, lower = matching[0]
    prices = [upper[0][name] for name in names]
    data['prices'] = [fraction_json(v) for v in prices]
    data['budget_min'] = fraction_json(lower[1])
    data['budget_max'] = fraction_json(upper[1])
    data['budget_equations'] = [lower[2], upper[2]]
    if any(v['lower'] is None or int(v['lower']) < 0 for v in data['variables']):
        data['bound_derivation'] = {'applied': False, 'reason': 'Requires all holdings nonnegative'}
        return
    for variable, price in zip(data['variables'], prices):
        limit = math.floor(upper[1]/price)
        previous = variable['upper']
        variable['upper'] = str(min(limit, int(previous))) if previous is not None else str(limit)
        variable['domain_sources'].append('redundant upper bound floor(B_max / positive price)')
    data['bound_derivation'] = {'applied': True, 'reason': 'All prices positive and holdings nonnegative',
                                'upper_equation': upper[2], 'rule': 'floor(B_max / p_i)'}


def parse_gams(text, *, source_name='inline.gms', external_parameters=None, ampl_text=None):
    """Read this scalar GAMS family; reject all unsupported program behavior."""
    if external_parameters:
        raise ValueError('External GAMS parameter/include injection is unsupported')
    lines = []
    disabled_include = False
    for line in text.splitlines():
        if line.startswith('*'):
            continue
        if line.lstrip().startswith('$'):
            normalized = re.sub(r'\s+', ' ', line.strip()).lower()
            if normalized == '$offlisting': continue
            if normalized == "$if not '%gams.u1%' == '' $include '%gams.u1%'":
                disabled_include = True
                continue
            if normalized == '$if not set minlp $set minlp minlp': continue
            raise ValueError('Unsupported/active GAMS directive: '+line.strip())
        lines.append(line)
    joined = '\n'.join(lines)
    pieces = joined.split(';')
    if pieces[-1].strip():
        raise ValueError('Unterminated GAMS statement')
    declared = []
    integers = []
    equation_names = []
    equations = []
    domain_updates = []
    initial_values = {}
    model_name = None
    solved = False
    for piece in pieces[:-1]:
        statement = piece.strip()
        if not statement: continue
        decl = re.fullmatch(r'(Integer\s+)?Variables?\s+(.+)', statement, re.I | re.S)
        if decl:
            if solved: raise ValueError('Declarations after Solve are unsupported')
            names = _names(decl[2])
            if decl[1]:
                if integers: raise ValueError('Multiple integer declaration blocks unsupported')
                integers = names
            else:
                if declared: raise ValueError('Multiple variable declaration blocks unsupported')
                declared = names
            continue
        decl = re.fullmatch(r'Equations?\s+(.+)', statement, re.I | re.S)
        if decl:
            if equation_names: raise ValueError('Multiple equation blocks unsupported')
            equation_names = _names(decl[1]); continue
        eq = re.fullmatch(r'([A-Za-z_]\w*)\s*\.\.(.*?)=([LGE])=(.*)', statement, re.I | re.S)
        if eq:
            if solved: raise ValueError('Equations after Solve are unsupported')
            left = parse_polynomial(eq[2], set(declared))
            right = parse_polynomial(eq[4], set(declared))
            equations.append({'name': eq[1].lower(), 'relation': eq[3].upper(),
                              'polynomial': pack_polynomial(_add(left, _neg(right)))})
            continue
        modification = re.fullmatch(r'([A-Za-z_]\w*)\.(lo|up|fx|l)\s*=\s*(.*)', statement, re.I | re.S)
        if modification:
            if solved: raise ValueError('Domain updates after Solve are unsupported')
            name, kind = modification[1].lower(), modification[2].lower()
            if name not in declared: raise ValueError('Unknown domain variable: '+name)
            value = _constant(modification[3])
            if kind == 'l': initial_values[name] = fraction_json(value)
            else: domain_updates.append((name, kind, value))
            continue
        model = re.fullmatch(r'Model\s+([A-Za-z_]\w*)\s*/\s*all\s*/', statement, re.I)
        if model:
            if model_name: raise ValueError('Multiple Model statements unsupported')
            model_name = model[1].lower(); continue
        solve = re.fullmatch(r'Solve\s+([A-Za-z_]\w*)\s+using\s+(MINLP|%MINLP%)\s+minimizing\s+objvar', statement, re.I)
        if solve:
            if solved or model_name != solve[1].lower(): raise ValueError('Unexpected Solve target')
            solved = True; continue
        option = re.fullmatch(r'([A-Za-z_]\w*)\.(limrow|limcol|tolproj|reslim|optcr|optca|iterlim)\s*=\s*(.*)', statement, re.I | re.S)
        if option and option[1].lower() == model_name:
            _constant(option[3]); continue
        if re.fullmatch(r'option\s+intvarup\s*=\s*0', statement, re.I): continue
        raise ValueError('Unsupported GAMS statement: '+statement[:120])
    if not solved or not integers or 'objvar' in integers or not declared or set(declared) != set(integers) | {'objvar'}:
        raise ValueError('Expected nonempty integer holdings and a continuous objvar with minimizing Solve')
    if len(equations) != len(equation_names) or {e['name'] for e in equations} != set(equation_names):
        raise ValueError('Missing, repeated or undeclared GAMS equation')
    risk_equations = []
    for eq in equations:
        poly = unpack_polynomial(eq['polynomial'])
        if any('objvar' in mon for mon in poly): risk_equations.append(eq)
    if len(risk_equations) != 1:
        raise ValueError('objvar must occur in exactly one risk equation')
    risk_equation = risk_equations[0]
    risk = unpack_polynomial(risk_equation['polynomial'])
    if (risk_equation['relation'] != 'L' or risk.pop(('objvar',), None) != -1 or
            any('objvar' in mon for mon in risk)):
        raise ValueError('Expected Q(holdings) - objvar <= 0')
    if any(name == 'objvar' for name, _, _ in domain_updates):
        raise ValueError('Cannot eliminate domain-restricted objvar')
    if max(map(len, risk), default=0) != 2:
        raise ValueError('Portfolio risk must be a quadratic polynomial')
    if any(len(mon) > 1 for eq in equations if eq != risk_equation
           for mon in unpack_polynomial(eq['polynomial'])):
        raise ValueError('Only linear side constraints supported in this portfolio profile')
    variables = []
    for name in integers:
        lower, upper = Fraction(0), None
        sources = ['GAMS integer nonnegative domain; unbounded upper confirmed by AMPL for replays']
        for variable, kind, value in domain_updates:
            if variable != name: continue
            if kind in ('lo', 'fx'): lower = value
            if kind in ('up', 'fx'): upper = value
            sources.append('GAMS '+kind+'='+str(value))
        variables.append({'name': name, 'lower': str(math.ceil(lower)),
                          'upper': str(math.floor(upper)) if upper is not None else None,
                          'domain_sources': sources,
                          'source_lower': fraction_json(lower),
                          'source_upper': fraction_json(upper) if upper is not None else None})
    if ampl_text is not None:
        # This is a domain cross-check, not an AMPL compiler. Restrict every
        # variable declaration and reject a different integer/nonnegative domain.
        clean = re.sub(r'#[^\n]*', '', ampl_text)
        declarations = re.findall(r'\bvar\s+(\w+)\s*([^;]*);', clean, re.I)
        objective = re.findall(r'\bminimize\s+\w+\s*:\s*(\w+)\s*;', clean, re.I)
        if len(objective) != 1:
            raise ValueError('Expected one scalar AMPL minimization objective')
        objective_name = objective[0].lower()
        expected = {v['name'] for v in variables} | {objective_name}
        if len(declarations) != len(expected) or {name.lower() for name, _ in declarations} != expected:
            raise ValueError('AMPL variable declarations disagree with GAMS')
        for name, domain in declarations:
            name = name.lower()
            if name == objective_name:
                if domain.strip(): raise ValueError('AMPL objective variable is restricted')
            elif not re.fullmatch(r'\s*integer\s*>=\s*0\s*', domain, re.I):
                raise ValueError('Unsupported/mismatching AMPL holding domain')
        if domain_updates:
            raise ValueError('Modified GAMS domains need a matching supported AMPL cross-check')
    data = {'schema_version': '1.0', 'indexing': 'holdings positions are zero-based; names preserve source order',
            'variables': variables, 'equations': equations,
            'risk_terms': pack_polynomial(risk), 'risk_equation': risk_equation['name'],
            'risk_scale': str(polynomial_smt(risk)[1]), 'source_name': source_name,
            'source_sha256': hashlib.sha256(text.encode()).hexdigest(),
            'initial_values': initial_values,
            'conversion': {'objvar_eliminated': True, 'external_parameters': {},
                           'conditional_include_disabled': disabled_include,
                           'precondition': 'continuous objvar unrestricted and only Q-objvar<=0 plus minimizing objective'}}
    _budget_and_bounds(data)
    return data


def _data_from_parameters(names, risk, prices, budget_min, budget_max, returns, return_min):
    data = {'schema_version': '1.0', 'indexing': 'holdings array is zero-based; symbols are one-based',
            'variables': [{'name': name, 'lower': '0', 'upper': None,
                           'source_lower': fraction_json(0), 'source_upper': None,
                           'domain_sources': ['application nonnegative integer holdings']} for name in names],
            'equations': [], 'risk_terms': pack_polynomial(risk), 'risk_equation': None,
            'risk_scale': str(polynomial_smt(risk)[1])}
    for name, values, threshold, relation in (
            ('budget_lower', prices, budget_min, 'G'), ('budget_upper', prices, budget_max, 'L'),
            ('return_requirement', returns, return_min, 'G')):
        poly = {(symbol,): rational(v) for symbol, v in zip(names, values) if rational(v)}
        poly[()] = -rational(threshold)
        data['equations'].append({'name': name, 'relation': relation, 'polynomial': pack_polynomial(poly)})
    data['return_coefficients'] = [fraction_json(v) for v in returns]
    data['return_min'] = fraction_json(return_min)
    _budget_and_bounds(data)
    return data


def build(data):
    model = Model('Integer minimum-trading-unit holdings; cost is exact scaled quadratic portfolio risk')
    names = [v['name'] for v in data['variables']]
    if len(set(names)) != len(names): raise ValueError('Repeated portfolio variable')
    for variable in data['variables']:
        lower, upper = variable['lower'], variable['upper']
        model.integer(variable['name'], None if lower is None else int(lower),
                      None if upper is None else int(upper), unit='minimum trading units')
        model.variables[variable['name']]['domain']['source'] = variable['domain_sources']
    for equation in data['equations']:
        if equation['name'] == data['risk_equation']: continue
        poly = unpack_polynomial(equation['polynomial'])
        if any(v not in names for mon in poly for v in mon): raise ValueError('Unknown constraint variable')
        expression, _ = polynomial_smt(poly)
        relation = {'L': '<=', 'G': '>=', 'E': '='}[equation['relation']]
        model.assertion('('+relation+' '+expression+' 0)')
    risk = unpack_polynomial(data['risk_terms'])
    if any(v not in names for mon in risk for v in mon): raise ValueError('Unknown risk variable')
    expression, scale = polynomial_smt(risk)
    if str(scale) != data['risk_scale']: raise ValueError('Risk scaling mismatch')
    model.integer('cost', role='auxiliary', unit='risk multiplied by '+str(scale))
    model.assertion('(= cost '+expression+')')
    return model


def _holding_values(data, decision):
    if not isinstance(decision, dict) or not isinstance(decision.get('holdings'), list):
        raise ValueError('Decision requires a holdings array')
    holdings = decision['holdings']
    if len(holdings) != len(data['variables']): raise ValueError('Wrong holdings length')
    if any(type(value) is not int for value in holdings): raise ValueError('Holdings must be integer JSON numbers')
    return dict(zip((v['name'] for v in data['variables']), holdings))


def check(data, decision):
    """Independent Fraction evaluation of original application equations."""
    values = _holding_values(data, decision)
    for variable in data['variables']:
        value = values[variable['name']]
        lo, hi = variable['lower'], variable['upper']
        if lo is not None and value < int(lo) or hi is not None and value > int(hi):
            raise ValueError('Holding outside domain: '+variable['name'])
    def evaluate(terms):
        total = Fraction(0)
        for term in terms:
            coefficient = rational(term['coefficient'])
            for name in term['variables']:
                if name not in values: raise ValueError('Unknown variable in application equation')
                coefficient *= values[name]
            total += coefficient
        return total
    for equation in data['equations']:
        if equation['name'] == data['risk_equation']: continue
        value = evaluate(equation['polynomial'])
        satisfied = {'L': value <= 0, 'G': value >= 0, 'E': value == 0}.get(equation['relation'])
        if not satisfied: raise ValueError('Violated original equation: '+equation['name'])
    risk = evaluate(data['risk_terms'])
    if data['risk_equation'] is not None:
        originals = [eq for eq in data['equations'] if eq['name'] == data['risk_equation']]
        if len(originals) != 1 or originals[0]['relation'] != 'L':
            raise ValueError('Missing original risk inequality')
        original_terms = []
        auxiliary_coefficient = Fraction(0)
        for term in originals[0]['polynomial']:
            if term['variables'] == ['objvar']:
                auxiliary_coefficient += rational(term['coefficient'])
            elif 'objvar' in term['variables']:
                raise ValueError('Unsupported original objective-variable use')
            else:
                original_terms.append(term)
        if auxiliary_coefficient != -1 or evaluate(original_terms) != risk:
            raise ValueError('Converted risk differs from the preserved original risk equation')
    if 'risk_factor_matrix' in data:
        A = [[int(v) for v in row] for row in data['risk_factor_matrix']]
        covariance = [[int(v) for v in row] for row in data['covariance']]
        x = decision['holdings']; n = len(x); lam = int(data['lambda'])
        if lam <= 0 or any(len(row) != n for row in A) or len(covariance) != n or any(len(row) != n for row in covariance):
            raise ValueError('Invalid Gram construction dimensions or lambda')
        for i in range(n):
            for j in range(n):
                if covariance[i][j] != sum(row[i]*row[j] for row in A) + (lam if i == j else 0):
                    raise ValueError('Covariance does not equal A^T A + lambda I')
        factor_risk = sum(sum(a*v for a, v in zip(row, x))**2 for row in A) + lam*sum(v*v for v in x)
        if risk != factor_risk: raise ValueError('Expanded polynomial disagrees with independent Gram risk')
    scale = int(data['risk_scale'])
    if scale <= 0 or (risk*scale).denominator != 1: raise ValueError('Invalid objective scaling')
    cost = int(risk*scale)
    if 'cost' in decision:
        provided = decision['cost']
        if isinstance(provided, bool) or not re.fullmatch(r'-?\d+', str(provided)) or int(provided) != cost:
            raise ValueError('Incorrect supplied portfolio cost')
    if 'risk' in decision and rational(decision['risk']) != risk:
        raise ValueError('Incorrect supplied portfolio risk')
    return {'cost': cost, 'risk': fraction_json(risk), 'method': 'original Fraction equations and independent Gram identity when present'}


def assignment(data, decision):
    return _holding_values(data, decision)


def decode(data, valuesdict):
    names = [v['name'] for v in data['variables']]
    if any(name not in valuesdict for name in names): raise ValueError('Missing solver holdings')
    decision = {'holdings': [valuesdict[name] for name in names]}
    _holding_values(data, decision)
    return decision


def _cache_dir(cache):
    if cache is None: return ROOT/'portfolio/src/data/raw/portfolio'
    path = Path(cache)
    if path.name == 'portfolio' or any((path/(name+'.gms')).exists() for name in IDS): return path
    return path/'portfolio'


def _selected(ids):
    selected = list(IDS) if ids is None else (ids.split(',') if isinstance(ids, str) else list(ids))
    if not selected or any(name not in IDS for name in selected):
        raise ValueError('This pinned minlplib profile supports only the four documented DAX ids')
    if len(selected) != len(set(selected)): raise ValueError('Repeated portfolio id')
    return selected


def _specs(name):
    point = 'p2' if name == 'smallinvDAXr1b150-165' else 'p1'
    return [(name+'.gms', 'gms', 'source_model_instance'),
            (name+'.mod', 'mod', 'domain_cross_check'),
            (name+'.'+point+'.sol', 'sol', 'published_point')]


def _source(path, url, role):
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != HASHES[path.name]:
        raise ValueError('Cached or downloaded source SHA256 differs from pinned version: '+path.name)
    record = source_record(path, url, role)
    # Retrieval dates belong to provenance, not the generated run's clock.
    log_path = path.parent/'download_records.json'
    log = json.loads(log_path.read_text()) if log_path.is_file() else []
    dates = [entry['retrieved_date'] for entry in log if entry.get('name') == path.name
             and entry.get('sha256') == record['sha256'] and entry.get('retrieved_date')]
    record.update(retrieved_date=dates[-1] if dates else None, license='CC BY 4.0',
                  attribution='MINLPLib; original source POLIP')
    return record


def fetch(cache=None, ids=None):
    """Fetch pinned source texts atomically, never overwrite a changed cache."""
    directory = _cache_dir(cache)
    directory.mkdir(parents=True, exist_ok=True)
    records = []
    for name in _selected(ids):
        for filename, folder, role in _specs(name):
            path = directory/filename
            url = 'https://www.minlplib.org/'+folder+'/'+filename
            if not path.exists():
                with build_opener(ProxyHandler({})).open(url, timeout=30) as response:
                    content = response.read()
                if hashlib.sha256(content).hexdigest() != HASHES[filename]:
                    raise ValueError('Upstream changed; refusal to cache unpinned text: '+url)
                temporary = path.with_suffix(path.suffix+'.download')
                temporary.write_bytes(content)
                temporary.replace(path)
                log_path = directory/'download_records.json'
                log = json.loads(log_path.read_text()) if log_path.is_file() else []
                log.append({'name': filename, 'url': url, 'sha256': HASHES[filename],
                            'size_bytes': len(content),
                            'retrieved_date': datetime.now(timezone.utc).date().isoformat()})
                log_path.write_text(json.dumps(log, sort_keys=True, indent=2)+'\n')
            records.append(_source(path, url, role))
    return records


def _published_decision(text, data):
    names = {v['name'] for v in data['variables']}
    holdings = {name: 0 for name in names}
    seen = set()
    for line in text.splitlines():
        pieces = line.split()
        if len(pieces) != 2 or pieces[0] in seen or pieces[0] not in names | {'objvar'}:
            raise ValueError('Unsupported or repeated published solution line')
        seen.add(pieces[0])
        value = rational(pieces[1])
        if pieces[0] == 'objvar': continue  # Printed floating auxiliary value is not exact risk.
        if value.denominator != 1: raise ValueError('Noninteger published holding')
        holdings[pieces[0]] = value.numerator
    return {'holdings': [holdings[v['name']] for v in data['variables']]}


def _case(label, data, decision, profile, origin, *, records=None, parameters=None, replay=False):
    risk = unpack_polynomial(data['risk_terms'])
    _, scale = polynomial_smt(risk)
    cross = sum(len(mon) == 2 and mon[0] != mon[1] for mon in risk)
    metadata = {
        'profile': profile, 'origin': origin, 'replay': replay,
        'model_changes': [
            'eliminate unrestricted continuous objvar with Q-objvar<=0 and minimizing objvar' if replay else 'new application data with original integer holding risk objective',
            'positive exact rational denominator clearing per polynomial',
            'redundant finite upper bounds derived from positive prices and nonnegative holdings' if data['bound_derivation']['applied'] else 'no budget-bound inference applied'],
        'objective': {'sense': 'minimize', 'symbol': 'cost', 'scale': str(scale),
                      'name': 'quadratic portfolio risk', 'unit': 'source risk units' if replay else 'synthetic risk score',
                      'recover': 'risk = cost / '+str(scale)},
        'nonlinearity': {'application_degree': 2, 'encoding_local_degree': 2,
                         'multivalued_integer_products': sum(len(mon) == 2 for mon in risk),
                         'boolean_products': 0, 'risk_cross_terms': cross,
                         'maximum_coefficient_digits': max((len(str(abs(int(c*scale)))) for c in risk.values()), default=1)},
        'source_records': records or [], 'parameters': parameters or {},
        'constraint_scaling': {eq['name']: str(polynomial_smt(unpack_polynomial(eq['polynomial']))[1])
                               for eq in data['equations'] if eq['name'] != data['risk_equation']},
        'budget_bound_derivation': data['bound_derivation'],
        'indexing': data['indexing'],
        'attribution': 'MINLPLib Small Investor Mean-Variance Portfolio Optimization; POLIP' if replay else 'new application data constructed for this project',
    }
    witness = None
    if decision is not None:
        result = check(data, decision)
        witness = {'decision': decision, 'cost': str(result['cost']),
                   'risk': result['risk'], 'source': 'MINLPLib published holdings' if replay else 'constructed feasible holdings'}
    return {'label': label, 'data': data, 'witness': witness, 'metadata': metadata}


def _synthetic(assets=8, risk_factors=None, seed=1, price_min=2, price_max=25,
               witness_max=5, budget_slack='0.10', return_tightness='0.90', coefficient_max=5):
    for name, value in [('assets', assets), ('seed', seed), ('price_min', price_min), ('price_max', price_max),
                        ('witness_max', witness_max), ('coefficient_max', coefficient_max)]:
        if type(value) is not int: raise ValueError(name+' must be an integer')
    q = max(2, assets//2) if risk_factors is None else risk_factors
    if type(q) is not int or q < 1 or assets < 2 or price_min < 1 or price_max < price_min or witness_max < 2 or coefficient_max < 1:
        raise ValueError('Invalid synthetic-gram parameter range')
    slack, tightness = rational(budget_slack), rational(return_tightness)
    if not 0 <= slack < 1 or not 0 < tightness <= 1: raise ValueError('Invalid slack/tightness')
    rng = random.Random(seed)
    # Bounded rejection only concerns a completely diagonal Gram draw.
    for attempt in range(32):
        A = [[rng.randint(-coefficient_max, coefficient_max) for _ in range(assets)] for _ in range(q)]
        lam = rng.randint(1, coefficient_max)
        covariance = [[sum(A[k][i]*A[k][j] for k in range(q)) + (lam if i == j else 0)
                       for j in range(assets)] for i in range(assets)]
        if any(covariance[i][j] for i in range(assets) for j in range(i+1, assets)): break
    else: raise ValueError('No nonzero risk cross term in 32 bounded draws')
    prices = [rng.randint(price_min, price_max) for _ in range(assets)]
    # Equal prices for two assets give a second exact-budget allocation without
    # expanding the configured planted-holdings range.
    prices[1] = prices[0]
    # Both positive and negative synthetic returns ensure the return condition is
    # useful; a budget-feasible all-negative-return holding violates it.
    returns = [rng.randint(1, 2*price_max) for _ in range(assets)]
    returns[-1] = -rng.randint(1, price_max)
    x = [rng.randint(0, witness_max) for _ in range(assets)]
    x[0] = max(2, x[0]); x[1] = max(1, x[1]); x[-1] = 0 if assets > 2 else x[-1]
    if assets == 2:
        returns[0] = max(returns[0], abs(returns[-1])*witness_max+1)
    else:
        returns[0] = max(returns[0], returns[1])
    planted_return = sum(a*b for a, b in zip(returns, x))
    if planted_return <= 0: raise ValueError('Internal planted return is not positive')
    alternative = list(x)
    alternative[0] += 1
    alternative[1] -= 1
    spending = sum(p*v for p, v in zip(prices, x))
    lo, hi = math.floor(spending*(1-slack)), math.ceil(spending*(1+slack))
    threshold = math.floor(sum(a*b for a, b in zip(returns, x))*tightness)
    # Widen only the upper synthetic budget to contain a negative-return-only
    # allocation; this certifies the return constraint is not implied by budget.
    negative_units = max(1, math.ceil(Fraction(lo, prices[-1])))
    hi = max(hi, negative_units*prices[-1])
    if not (lo <= negative_units*prices[-1] <= hi and negative_units*returns[-1] < threshold):
        raise ValueError('Failed to construct a budget-feasible strict return counterexample')
    names = ['x'+str(i+1) for i in range(assets)]
    risk = {(names[i], names[i]): Fraction(covariance[i][i]) for i in range(assets)}
    for i in range(assets):
        for j in range(i+1, assets):
            if covariance[i][j]: risk[(names[i], names[j])] = Fraction(2*covariance[i][j])
    data = _data_from_parameters(names, risk, prices, lo, hi, returns, threshold)
    data.update(risk_factor_matrix=[[str(v) for v in row] for row in A],
                covariance=[[str(v) for v in row] for row in covariance], **{'lambda': str(lam)},
                generation={'algorithm': 'integer-gram-v1', 'seed': seed, 'draw_attempts': attempt+1,
                            'price_unit': 'discrete synthetic currency unit',
                            'return_unit': 'synthetic return score',
                            'alternative_feasible_holdings': alternative,
                            'return_nonredundancy_counterexample': [0]*(assets-1)+[negative_units]})
    params = {'assets': assets, 'risk_factors': q, 'seed': seed, 'price_min': price_min, 'price_max': price_max,
              'witness_max': witness_max, 'budget_slack': fraction_json(slack),
              'return_tightness': fraction_json(tightness), 'coefficient_max': coefficient_max}
    check(data, {'holdings': alternative})
    return _case('gram-m'+str(assets)+'-q'+str(q)+'-s'+str(seed), data, {'holdings': x},
                 'synthetic-gram', 'new_application_family', parameters=params)


def make(profile, **params):
    if profile == 'tiny':
        if params: raise ValueError('tiny takes no parameters')
        names = ['x1', 'x2']
        risk = {('x1','x1'): Fraction(1,2), ('x1','x2'): Fraction(1,3), ('x2','x2'): Fraction(2,3)}
        data = _data_from_parameters(names, risk, [2,3], 5, 6, [1,1], 2)
        case = _case('portfolio-tiny', data, {'holdings': [1,1]}, 'tiny', 'new_application_family')
        case['metadata']['expected_optimum'] = '9'
        case['metadata']['fixture'] = 'handoff portfolio_tiny, exact full-enumeration regression'
        return [case]
    if profile == 'synthetic-gram': return [_synthetic(**params)]
    if profile != 'minlplib': raise ValueError('Unknown portfolio profile: '+profile)
    cache = _cache_dir(params.pop('cache', None))
    selected = _selected(params.pop('ids', None))
    if params: raise ValueError('Unknown minlplib parameters: '+', '.join(params))
    result = []
    for name in selected:
        records = []
        for filename, folder, role in _specs(name):
            path = cache/filename
            if not path.is_file(): raise ValueError('Missing pinned source '+str(path)+'; run fetch first')
            records.append(_source(path, 'https://www.minlplib.org/'+folder+'/'+filename, role))
        data = parse_gams((cache/(name+'.gms')).read_text(), source_name=name+'.gms',
                          ampl_text=(cache/(name+'.mod')).read_text())
        point = next(cache/r[0] for r in _specs(name) if r[2] == 'published_point')
        decision = _published_decision(point.read_text(), data)
        case = _case(name, data, decision, 'minlplib', 'upstream_model_instance',
                     records=records, parameters={'id': name, 'external_parameters': {}}, replay=True)
        if name == 'smallinvDAXr1b150-165':
            checked = check(data, decision)
            if checked['cost'] != 220262336900 or rational(checked['risk']) != Fraction('88.10493476'):
                raise ValueError('Public p2 exact-risk regression failed')
        result.append(case)
    return result


def smoke_specs():
    return [{'profile': 'tiny'}, {'profile': 'minlplib', 'ids': ','.join(IDS)}] + [
        {'profile': 'synthetic-gram', 'assets': assets, 'seed': seed}
        for assets in (4,8,16) for seed in (1,2)]
