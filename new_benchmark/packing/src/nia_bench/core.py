"""Shared serialization and SMT output; application checks live in each family."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[3]
REPO = ROOT.parent
VERSION = '1.0.0'
FAMILIES = ('portfolio', 'cyclic', 'packing', 'design')


def num(value):
    if isinstance(value, bool):
        raise ValueError('Boolean is not an integer coefficient')
    try:
        integer = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError('Expected an exact integer') from exc
    if str(value) != str(integer):
        raise ValueError('Expected integer or decimal integer string')
    return str(integer) if integer >= 0 else f'(- {-integer})'


def exact_int(value):
    num(value)
    return int(value)


def summation(terms):
    terms = list(terms)
    return '0' if not terms else terms[0] if len(terms) == 1 else '(+ '+' '.join(terms)+')'


def disjunction(terms):
    terms = list(terms)
    return 'false' if not terms else terms[0] if len(terms) == 1 else '(or '+' '.join(terms)+')'


def json_text(value):
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)+'\n'


def digest(content):
    return hashlib.sha256(content).hexdigest()


def source_record(path, url, role, commit=None):
    path = Path(path)
    try:
        name = str(path.resolve().relative_to(REPO))
    except ValueError:
        name = path.name
    record = {'file': name, 'url': url, 'role': role,
              'bytes': path.stat().st_size, 'sha256': digest(path.read_bytes())}
    if commit:
        record['git_commit'] = commit
    return record


class Model:
    def __init__(self, description=''):
        self.description = description
        self.lines = ['; '+line for line in description.splitlines()]+['(set-logic QF_NIA)']
        self.variables = {}
        self.assertions = []

    def integer(self, name, lo=None, hi=None, role='application', unit=''):
        if not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', name) or name in self.variables:
            raise ValueError('Invalid or repeated integer symbol: '+name)
        if lo is not None and hi is not None and int(lo) > int(hi):
            raise ValueError('Empty variable domain: '+name)
        self.variables[name] = {'type': 'Int', 'role': role, 'unit': unit,
                                'domain': {'lower': None if lo is None else str(lo),
                                           'upper': None if hi is None else str(hi)}}
        self.lines.append(f'(declare-fun {name} () Int)')
        if lo is not None:
            self.assertion(f'(>= {name} {num(lo)})')
        if hi is not None:
            self.assertion(f'(<= {name} {num(hi)})')

    def assertion(self, expression):
        self.assertions.append(expression)
        self.lines.append('(assert '+expression+')')

    def base(self):
        return '\n'.join(self.lines)+'\n'


def forms(text):
    """Parse generated S-expressions and Z3 integer-value replies, without eval."""
    text = re.sub(r';[^\n]*', '', text)
    tokens = re.findall(r'\(|\)|"(?:[^"\\]|\\.)*"|[^\s()]+', text)
    stack, result = [], []
    for token in tokens:
        if token == '(':
            stack.append([])
        elif token == ')':
            if not stack:
                raise ValueError('Unbalanced SMT output')
            item = stack.pop()
            (stack[-1] if stack else result).append(item)
        else:
            (stack[-1] if stack else result).append(token)
    if stack:
        raise ValueError('Unbalanced SMT output')
    return result


def audit_smt(text):
    expressions = forms(text)
    symbols = set()
    allowed = {'+', '-', '*', 'ite', '=', '<', '<=', '>', '>=', 'and', 'or', 'not', '=>', 'distinct'}

    def expression(node):
        if isinstance(node, str):
            if node not in symbols and node not in ('true', 'false') and not re.fullmatch(r'\d+', node):
                raise ValueError('Unknown SMT atom: '+node)
            return 'Bool' if node in ('true','false') else 'Int'
        if not node or node[0] not in allowed:
            raise ValueError('Unsupported SMT operation: '+str(node[:1]))
        sorts = [expression(child) for child in node[1:]]
        operator = node[0]
        if operator in ('+', '-', '*') and sorts and all(s=='Int' for s in sorts):
            return 'Int'
        if operator in ('<','<=','>','>=') and len(sorts)>=2 and all(s=='Int' for s in sorts):
            return 'Bool'
        if operator in ('=','distinct') and len(sorts)>=2 and len(set(sorts))==1:
            return 'Bool'
        if operator in ('and','or') and all(s=='Bool' for s in sorts):
            return 'Bool'
        if operator=='not' and sorts==['Bool']:
            return 'Bool'
        if operator=='=>' and len(sorts)>=2 and all(s=='Bool' for s in sorts):
            return 'Bool'
        if operator=='ite' and len(sorts)==3 and sorts[0]=='Bool' and sorts[1]==sorts[2]:
            return sorts[1]
        raise ValueError('Wrong SMT sort or arity: '+operator)

    logic_count = 0
    for command in expressions:
        if not isinstance(command, list) or not command:
            raise ValueError('Expected SMT command')
        if command[0] == 'set-logic' and command == ['set-logic', 'QF_NIA']:
            logic_count += 1
        elif command[0] == 'declare-fun' and len(command) == 4 and command[2:] == [[], 'Int']:
            if command[1] in symbols:
                raise ValueError('Repeated SMT declaration')
            symbols.add(command[1])
        elif command[0] == 'assert' and len(command) == 2:
            if expression(command[1]) != 'Bool':
                raise ValueError('SMT assertion must have Boolean sort')
        else:
            raise ValueError('Unsupported base SMT command: '+str(command[0]))
    if logic_count != 1:
        raise ValueError('Expected exactly one QF_NIA declaration')
    return {'integer_variables': len(symbols), 'assertions': sum(x[0]=='assert' for x in expressions)}


def parse_values(text):
    def integer(node):
        if isinstance(node, str) and re.fullmatch(r'\d+', node):
            return int(node)
        if isinstance(node, list) and len(node) == 2 and node[0] == '-':
            return -integer(node[1])
        raise ValueError('Expected exact integer model value: '+str(node))
    for node in forms(text):
        if isinstance(node, list) and node and all(isinstance(x, list) and len(x)==2 for x in node):
            if all(isinstance(x[0], str) and re.fullmatch(r'[A-Za-z_]\w*', x[0]) for x in node):
                return {key: integer(value) for key, value in node}
    raise ValueError('No get-value integer response')


def load_family(name):
    if name not in FAMILIES:
        raise ValueError('Unknown application: '+name)
    module_name = 'nia_bench_family_'+name
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, ROOT/name/'src/model.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module


def application_fingerprint(family, case):
    meta = case.get('metadata', {})
    if meta.get('semantic_fingerprint'):
        return str(meta['semantic_fingerprint'])
    data = {k: v for k, v in case['data'].items()
            if k not in ('encoding', 'seed', 'generation', 'source_path')}
    if family == 'packing':
        for key in ('rectangles', 'sizes', 'items'):
            if isinstance(data.get(key), list):
                data[key] = sorted(data[key])
    return digest(json_text(data).encode())


def write_case(family, case, module, out=ROOT):
    model = module.build(case['data'])
    base = model.base()
    theory = audit_smt(base)
    original_meta = case.get('metadata', {})
    sense = original_meta.get('objective', {}).get('sense', 'minimize')
    if sense not in ('minimize', 'maximize') or 'cost' not in model.variables:
        raise ValueError('Expected a single cost objective')
    witness = case.get('witness')
    checked = None
    if witness is not None:
        checked = module.check(case['data'], witness['decision'])
        if exact_int(checked['cost']) != exact_int(witness['cost']):
            raise ValueError('Witness objective disagrees with independent application check')
    data_text = json_text(case['data'])
    app_hash = application_fingerprint(family, case)
    variant_hash = digest((data_text+base).encode())
    label = re.sub(r'[^A-Za-z0-9_.-]+', '-', case['label']).strip('-')
    instance_id = label+'--'+variant_hash[:12]
    target = Path(out)/family/'src'/'case_data'/instance_id
    benchmark = Path(out)/family/'instance'/(instance_id+'.smt2')
    metadata = dict(original_meta)
    metadata.update(schema_version='1.0', family=family, instance_id=instance_id,
                    application_instance_id=family+'-'+app_hash[:20],
                    generator_version=VERSION, data_sha256=digest(data_text.encode()),
                    model_sha256=digest(base.encode()),
                    decision_variables=model.variables,
                    feasibility_status='feasible_only' if checked else 'not_checked',
                    optimality_status='not_checked')
    metadata['new_instance'] = not bool(metadata.get('replay', False) or metadata.get('encoding_variant',False))
    metadata.setdefault('model_changes', [])
    metadata.setdefault('source_records', [])
    metadata.setdefault('parameters', {})
    generator_files = [Path(__file__), Path(__file__).with_name('__init__.py')]
    if getattr(module, '__file__', None):
        generator_files.append(Path(module.__file__))
    metadata['generator_sources_sha256'] = {
        str(p.resolve().relative_to(ROOT)): digest(p.read_bytes()) for p in generator_files}
    metadata['size'] = {**theory,
        'application_variables': sum(v['role']=='application' for v in model.variables.values()),
        'auxiliary_variables': sum(v['role']!='application' for v in model.variables.values()),
        'disjunctions': base.count('(or '), 'base_bytes': len(base.encode())}
    literals = [int(s) for s in re.findall(r'(?<![A-Za-z_0-9])[0-9]+(?![A-Za-z_0-9])',base)]
    metadata['size']['maximum_integer_literal_bits'] = max((n.bit_length() for n in literals),default=0)
    metadata['output_sha256'] = {'data.json': digest(data_text.encode()),
                                 'base.smt2': digest(base.encode())}
    opt = base+f'({sense} cost)\n(check-sat)\n'
    metadata['size']['opt_bytes'] = len(opt.encode())
    metadata['output_sha256']['opt.smt2'] = digest(opt.encode())
    semantic = {'data.json': data_text, 'base.smt2': base, 'opt.smt2': opt,
                'metadata.json': json_text(metadata),
                'witness.json': json_text(witness if witness else {'status': 'not_found'})}
    if benchmark.exists() and benchmark.read_text() != opt:
        raise FileExistsError('Refuse to replace different existing benchmark: '+str(benchmark))
    if target.exists():
        for name, content in semantic.items():
            if not (target/name).is_file() or (target/name).read_text() != content:
                raise FileExistsError('Refuse to replace different existing instance: '+str(target/name))
    else:
        target.mkdir(parents=True)
        for name, content in semantic.items():
            (target/name).write_text(content)
        validation = {'schema_version': '1.0', 'generator': 'passed',
                      'theory_audit': {'status': 'passed', **theory},
                      'application_witness': {'status': 'passed', 'cost': str(checked['cost'])}
                          if checked else {'status': 'not_checked'},
                      'z3': {'status': 'not_checked'}, 'optimality': {'status': 'not_checked'}}
        (target/'validation.json').write_text(json_text(validation))
    if not benchmark.exists():
        benchmark.parent.mkdir(parents=True,exist_ok=True)
        benchmark.write_text(opt)
    return {'family': family, 'instance_id': instance_id, 'path': str(target.resolve()),
            'benchmark_path':str(benchmark.resolve()),
            'application_instance_id': metadata['application_instance_id'],
            'replay': bool(metadata.get('replay', False)),
            'new_instance': metadata['new_instance'], 'encoding_variant':bool(metadata.get('encoding_variant',False)),
            'profile': metadata['profile']}


def find_cases(out=ROOT):
    result = []
    for family in FAMILIES:
        for path in sorted((Path(out)/family/'src'/'case_data').glob('*/metadata.json')):
            meta = json.loads(path.read_text())
            result.append({'family': family, 'instance_id': meta['instance_id'],
                           'path': str(path.parent.resolve()),
                           'benchmark_path':str((Path(out)/family/'instance'/(meta['instance_id']+'.smt2')).resolve()),
                           'application_instance_id': meta['application_instance_id'],
                           'replay': bool(meta.get('replay', False)),
                           'new_instance': bool(meta['new_instance']),
                           'encoding_variant':bool(meta.get('encoding_variant',False)), 'profile': meta['profile']})
    return result


def support_dir(out=ROOT):
    return Path(out)/'packing'/'src'/'_support'


def case_data_path(path):
    path = Path(path)
    if path.suffix=='.smt2' and path.parent.name=='instance':
        return path.parent.parent/'src'/'case_data'/path.stem
    return path


def manifest(out=ROOT, skipped=None):
    out = Path(out)
    cases = find_cases(out)
    per_family = {}
    for family in FAMILIES:
        entries = [c for c in cases if c['family'] == family]
        per_family[family] = {'total': len(entries), 'replay': sum(c['replay'] for c in entries),
                              'new': sum(c['new_instance'] for c in entries),
                              'applications':len({c['application_instance_id'] for c in entries}),
                              'encoding_variants':sum(c['encoding_variant'] for c in entries)}
    for case in cases:
        case['auxiliary_path'] = str(Path(case['path']).relative_to(out.resolve()))
        case['path'] = str(Path(case.pop('benchmark_path')).relative_to(out.resolve()))
    report = {'schema_version': '1.0', 'generator_version': VERSION, 'families': per_family,
              'total': len(cases), 'replay': sum(c['replay'] for c in cases),
              'new': sum(c['new_instance'] for c in cases), 'cases': cases,
              'unique_applications':len({c['application_instance_id'] for c in cases}),
              'encoding_variants':sum(c['encoding_variant'] for c in cases),
              'duplicate_parameters_skipped': skipped or []}
    support_dir(out).mkdir(parents=True, exist_ok=True)
    (support_dir(out)/'manifest.json').write_text(json_text(report))
    return report
