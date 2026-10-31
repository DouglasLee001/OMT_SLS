"""Source affine-ray candidate synthesis; import does not load Z3.

Call analyze in an externally bounded SDK child. SAT gives a candidate; only
the independent checker certifies its trajectory. Template UNSAT says nothing
about original boundedness. There are no SLS anchors or coefficient bounds.
"""
from __future__ import annotations
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import sys
import time

MAX_NODES=50000
MAX_BITS=8192
MAX_SOURCE_BYTES=16*1024*1024

class Unsupported(ValueError):
    pass

def _multiply(left,right):
    if len(left)+len(right)-2>2:
        raise Unsupported('source syntactic polynomial degree exceeds two')
    return tuple(sum(left[i]*right[k-i] for i in range(len(left)) if 0<=k-i<len(right))
                 for k in range(len(left)+len(right)-1))

def _threshold(coefficients):
    for degree in range(len(coefficients)-1,0,-1):
        lead=abs(coefficients[degree])
        if lead:
            lower=sum(abs(c) for c in coefficients[:degree])
            return 1+(lower+lead-1)//lead
    return 1

def _eventual(coefficients,relation):
    value=next((c for c in reversed(coefficients) if c),0)
    return {'=':value==0,'<':value<0,'<=':value<=0,'>':value>0,'>=':value>=0}[relation]

def _strict(helper):
    if helper is not None and hasattr(helper,'_query') and hasattr(helper,'_commands'):return helper
    path=Path(helper) if helper is not None else Path(__file__).resolve().parents[2]/'script/run_strict_bound.py'
    path=path.resolve(strict=True);name='_affine_ray_strict_'+hashlib.sha256(str(path).encode()).hexdigest()[:16]
    if name not in sys.modules:
        spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec)
        sys.modules[name]=module;spec.loader.exec_module(module)
    return sys.modules[name]

def _raw_validate(forms,remaining):
    """Validate every retained raw body before parser folding can hide it."""
    declarations=set();bodies=[];nodes=0
    for form in forms:
        remaining()
        if not isinstance(form,list) or not form:raise Unsupported('invalid source command')
        head=form[0]
        if head=='declare-const' and len(form)==3 and form[2]=='Int':declarations.add(form[1])
        elif head=='declare-fun' and len(form)==4 and form[2]==[] and form[3]=='Int':declarations.add(form[1])
        elif head=='define-fun' and len(form)==5 and form[2]==[] and form[3]=='Int':
            declarations.add(form[1]);bodies.append((form[4],'Int'))
        elif head=='assert' and len(form)==2:bodies.append((form[1],'Bool'))
        elif head=='minimize' and len(form)==2:bodies.append((form[1],'Int'))
        elif head=='set-logic' and len(form)==2:pass
        else:raise Unsupported('unsupported source declaration/command: '+str(head))
    todo=list(bodies)
    while todo:
        remaining();expression,sort=todo.pop();nodes+=1
        if nodes>MAX_NODES:raise Unsupported('raw source node guard exceeded')
        if isinstance(expression,str):
            if sort=='Bool' and expression in ('true','false'):continue
            if sort=='Int' and expression in declarations:continue
            if sort=='Int' and re.fullmatch(r'-?[0-9]+',expression):
                if len(expression)>2500 or int(expression).bit_length()>MAX_BITS:raise Unsupported('source integer bit guard exceeded')
                continue
            raise Unsupported('unsupported source symbol/sort: '+expression[:120])
        if not expression or not isinstance(expression[0],str):raise Unsupported('unsupported source expression')
        head,children=expression[0],expression[1:]
        if sort=='Int' and head in ('+','-','*') and children:todo.extend((child,'Int') for child in children)
        elif sort=='Bool' and head in ('=','<=','<','>=','>') and len(children)==2:todo.extend((child,'Int') for child in children)
        elif sort=='Bool' and head in ('and','or'):todo.extend((child,'Bool') for child in children)
        elif sort=='Bool' and head=='not' and len(children)==1:todo.append((children[0],'Bool'))
        else:raise Unsupported('unsupported raw operator/sort: '+head)

def _coefficient_prefix(source):
    index=0
    while True:
        prefix=f'affine_ray_coeff_{index}_'
        if prefix not in source:return prefix
        index+=1

def _source_dag(z3,roots,remaining):
    order=[];seen=set();pending=[(root,False) for root in roots]
    arithmetic={z3.Z3_OP_ADD,z3.Z3_OP_SUB,z3.Z3_OP_UMINUS,z3.Z3_OP_MUL}
    comparisons={z3.Z3_OP_EQ,z3.Z3_OP_LE,z3.Z3_OP_LT,z3.Z3_OP_GE,z3.Z3_OP_GT}
    boolean={z3.Z3_OP_AND,z3.Z3_OP_OR,z3.Z3_OP_NOT}
    while pending:
        remaining();node,ready=pending.pop();identity=node.get_id()
        if ready:order.append(node);continue
        if identity in seen:continue
        seen.add(identity)
        if len(seen)>MAX_NODES:raise Unsupported('source DAG node guard exceeded')
        if not z3.is_app(node):raise Unsupported('quantified or bound source expression')
        children=node.children();kind=node.decl().kind();sort=node.sort().kind();sorts=[child.sort().kind() for child in children]
        if z3.is_int_value(node):valid=not children and node.as_long().bit_length()<=MAX_BITS
        elif z3.is_true(node) or z3.is_false(node):valid=not children
        elif kind==z3.Z3_OP_UNINTERPRETED:valid=not children and sort==z3.Z3_INT_SORT
        elif kind in arithmetic:valid=bool(children) and sort==z3.Z3_INT_SORT and all(s==z3.Z3_INT_SORT for s in sorts)
        elif kind in comparisons:valid=sort==z3.Z3_BOOL_SORT and len(children)==2 and sorts==[z3.Z3_INT_SORT]*2
        elif kind in boolean:valid=sort==z3.Z3_BOOL_SORT and all(s==z3.Z3_BOOL_SORT for s in sorts) and (kind!=z3.Z3_OP_NOT or len(children)==1)
        else:valid=False
        if not valid:raise Unsupported('unsupported SDK source operator/sort: '+str(node.decl().name()))
        pending.append((node,True));pending.extend((child,False) for child in reversed(children))
    return order

def _add(left,right,sign=1):
    return tuple((left[i] if i<len(left) else 0)+sign*(right[i] if i<len(right) else 0) for i in range(max(len(left),len(right))))

def _condition(z3,coefficients,relation):
    zero=z3.And(*[value==0 for value in coefficients])
    negative=z3.Or(*[z3.And(*[value==0 for value in coefficients[i+1:]],coefficients[i]<0) for i in range(len(coefficients))])
    positive=z3.Or(*[z3.And(*[value==0 for value in coefficients[i+1:]],coefficients[i]>0) for i in range(len(coefficients))])
    return {'=':zero,'<':negative,'<=':z3.Or(negative,zero),'>':positive,'>=':z3.Or(positive,zero)}[relation]

def _coefficient_query(z3,order,hard,objective,prefix,remaining):
    ctx=objective.ctx
    variables=sorted((node for node in order if node.decl().kind()==z3.Z3_OP_UNINTERPRETED),key=lambda node:str(node.decl().name()))
    names=[str(node.decl().name()) for node in variables]
    if len(set(names))!=len(names):raise Unsupported('ambiguous source constant names')
    coordinates={node.get_id():(z3.Int(prefix+f'a{i}',ctx=ctx),z3.Int(prefix+f'd{i}',ctx=ctx)) for i,node in enumerate(variables)}
    polynomials={};booleans={};atoms=[]
    relations={z3.Z3_OP_EQ:'=',z3.Z3_OP_LE:'<=',z3.Z3_OP_LT:'<',z3.Z3_OP_GE:'>=',z3.Z3_OP_GT:'>'}
    for node in order:
        remaining();identity=node.get_id();kind=node.decl().kind();children=node.children()
        if node.sort().kind()==z3.Z3_INT_SORT:
            if z3.is_int_value(node):value=(z3.IntVal(str(node.as_long()),ctx=ctx),)
            elif kind==z3.Z3_OP_UNINTERPRETED:value=coordinates[identity]
            elif kind==z3.Z3_OP_MUL:
                value=(z3.IntVal(1,ctx=ctx),)
                for child in children:remaining();value=_multiply(value,polynomials[child.get_id()])
            else:
                value=polynomials[children[0].get_id()]
                if len(children)==1 and kind in (z3.Z3_OP_SUB,z3.Z3_OP_UMINUS):value=tuple(-c for c in value)
                for child in children[1:]:value=_add(value,polynomials[child.get_id()],-1 if kind==z3.Z3_OP_SUB else 1)
            polynomials[identity]=value
        elif kind in relations:
            coefficients=_add(polynomials[children[0].get_id()],polynomials[children[1].get_id()],-1)
            booleans[identity]=_condition(z3,coefficients,relations[kind]);atoms.append((node,coefficients,relations[kind]))
        elif z3.is_true(node) or z3.is_false(node):booleans[identity]=node
        elif kind==z3.Z3_OP_NOT:booleans[identity]=z3.Not(booleans[children[0].get_id()])
        else:
            values=[booleans[child.get_id()] for child in children]
            booleans[identity]=(z3.And(*values) if values else z3.BoolVal(True,ctx=ctx)) if kind==z3.Z3_OP_AND else (z3.Or(*values) if values else z3.BoolVal(False,ctx=ctx))
    return variables,coordinates,polynomials,booleans,atoms,[booleans[form.get_id()] for form in hard]

def _digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def _sdk_identity(z3,remaining):
    libraries=set()
    for line in Path('/proc/self/maps').read_text(errors='surrogateescape').splitlines():
        fields=line.split(None,5)
        if len(fields)!=6:continue
        path=re.sub(r'\\([0-7]{3})',lambda m:chr(int(m[1],8)),fields[5])
        if Path(path.removesuffix(' (deleted)')).name.startswith('libz3.so'):
            if path.endswith(' (deleted)'):raise ValueError('SDK mapping was deleted')
            libraries.add(str(Path(path).resolve(strict=True)))
    if not libraries:raise ValueError('no mapped SDK library')
    values=[]
    for path in sorted(libraries):remaining();values.append({'path':path,'sha256':_digest(path)})
    return {'version':z3.get_version_string(),'python':str(Path(z3.__file__).resolve()),'python_sha256':_digest(z3.__file__),'libraries':values}

def _remove_owned(path,identity):
    try:
        stat=Path(path).lstat()
        if (stat.st_dev,stat.st_ino)==identity:Path(path).unlink()
    except FileNotFoundError:pass

def _publish(path,content,remaining):
    """Same-directory no-clobber publication; late publication is rolled back."""
    remaining();path=Path(path);temporary=path.with_name(path.name+'.own-tmp')
    fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    stat=os.fstat(fd);identity=(stat.st_dev,stat.st_ino);published=False
    try:
        data=content.encode('utf-8');offset=0
        while offset<len(data):remaining();offset+=os.write(fd,data[offset:offset+65536])
        os.close(fd);fd=-1;remaining();os.link(temporary,path);published=True;remaining()
        _remove_owned(temporary,identity);remaining();return identity
    except BaseException:
        if published:_remove_owned(path,identity)
        raise
    finally:
        if fd>=0:os.close(fd)
        _remove_owned(temporary,identity)

def analyze(source_path,direction,seconds,output_dir,z3_module,*,strict_helper=None):
    """Return one synthesis result; caller must impose an external child limit."""
    started=time.monotonic();owned=False;candidate_identity=None;candidate=None
    result=dict(status='unknown',answer=None,reason='',candidate_path=None,candidate_sha256=None,
                query_path=None,query_sha256=None,coefficient_model_path=None,coefficient_model_sha256=None,
                check_started=False,check_seconds=0.,setup_seconds=0.,diagnostic_only=True,is_unbounded=False,is_optimal=False)
    try:
        if type(seconds) not in (int,float) or not math.isfinite(seconds) or not 0<seconds<=1200:
            raise ValueError('seconds must be finite in (0,1200]')
        if direction not in ('min','max'):raise ValueError('direction must be min or max')
        deadline=started+seconds
        def remaining():
            value=deadline-time.monotonic()
            if value<=0:raise TimeoutError('source affine-ray deadline reached')
            return value
        source=Path(source_path).resolve(strict=True);output=Path(output_dir).absolute()
        if not source.is_file() or source.stat().st_size>MAX_SOURCE_BYTES:raise Unsupported('source file size/type guard')
        if output.exists() or output.is_symlink():raise ValueError('output directory must be new')
        remaining();raw=source.read_bytes();source_hash=hashlib.sha256(raw).hexdigest();text=raw.decode('utf-8')
        output.mkdir(parents=False,exist_ok=False);owned=True;output=output.resolve()
        result.update(source=str(source),source_sha256=source_hash,direction=direction,module_sha256=_digest(__file__))
        strict=_strict(strict_helper);filtered,actual_direction,_=strict._query(text)
        if actual_direction!=direction:raise ValueError('source direction differs from requested direction')
        _raw_validate(strict._commands(filtered),remaining)
        result['strict_helper_sha256']=_digest(strict.__file__)
        z3=z3_module;result['sdk']=_sdk_identity(z3,remaining);remaining()
        ctx=z3.Context();parser=z3.Optimize(ctx=ctx);parser.from_string(filtered) # Parse only.
        objectives=parser.objectives();hard=list(parser.assertions())
        if len(objectives)!=1 or not objectives[0].is_int():raise Unsupported('expected one Int source objective')
        objective=objectives[0];order=_source_dag(z3,hard+[objective],remaining)
        variables,coordinates,polys,booleans,atoms,assertions=_coefficient_query(z3,order,hard,objective,_coefficient_prefix(text),remaining)
        objective_coefficients=polys[objective.get_id()];improve=[]
        for i in range(1,len(objective_coefficients)):
            improve.append(z3.And(*[c==0 for c in objective_coefficients[i+1:]],
                                  objective_coefficients[i]<0 if direction=='min' else objective_coefficients[i]>0))
        assertions.append(z3.Or(*improve) if improve else z3.BoolVal(False,ctx=ctx))
        seen=set();todo=list(assertions)
        while todo:
            remaining();node=todo.pop()
            if node.get_id() in seen:continue
            seen.add(node.get_id());todo.extend(node.children())
            if len(seen)>MAX_NODES:raise Unsupported('coefficient query DAG node guard exceeded')
        query=z3.Solver(ctx=ctx);query.add(*assertions)
        query_path=output/'coefficients.smt2';_publish(query_path,query.to_smt2(),remaining)
        result.update(objective=objective.sexpr(),query_path=str(query_path),query_sha256=_digest(query_path),
                      source_dag_nodes=len(order),coefficient_dag_nodes=len(seen),integer_variable_count=len(variables))
        query.set(timeout=max(1,int(1000*remaining())))
        result.update(setup_seconds=time.monotonic()-started,check_started=True);began=time.monotonic()
        try:answer=query.check()
        finally:result['check_seconds']=time.monotonic()-began
        remaining();result['answer']=str(answer)
        if answer==z3.unsat:
            result.update(status='no_affine_ray',reason='only this affine coefficient template is UNSAT; no boundedness conclusion')
        elif answer==z3.unknown:result.update(status='unknown',reason=query.reason_unknown())
        else:
            model=query.model()
            if not all(z3.is_true(model.eval(form,model_completion=True)) for form in assertions):raise ValueError('coefficient model fails complete synthesis query')
            def exact(expression):
                remaining();value=model.eval(expression,model_completion=True)
                if not z3.is_int_value(value):raise ValueError('noninteger coefficient model')
                number=value.as_long()
                if number.bit_length()>MAX_BITS:raise Unsupported('coefficient bit guard exceeded')
                return number
            coefficients=[];point=[];threshold=1;atom_values=[]
            for atom,poly,relation in atoms:
                values=tuple(exact(c) for c in poly);threshold=max(threshold,_threshold(values))
                atom_values.append(dict(atom=atom.sexpr(),coefficients=[str(c) for c in values],eventual=_eventual(values,relation)))
            objective_values=tuple(exact(c) for c in objective_coefficients);threshold=max(threshold,_threshold(objective_values))
            if threshold.bit_length()>MAX_BITS:raise Unsupported('threshold bit guard exceeded')
            degree=next((i for i in range(len(objective_values)-1,0,-1) if objective_values[i]),None)
            if degree is None or (objective_values[degree]>=0 if direction=='min' else objective_values[degree]<=0):raise ValueError('objective does not diverge in the improving direction')
            for variable in variables:
                a,d=coordinates[variable.get_id()];intercept,slope=exact(a),exact(d);value=intercept+slope*threshold
                if value.bit_length()>MAX_BITS:raise Unsupported('threshold point bit guard exceeded')
                coefficients.append(dict(name=str(variable.decl().name()),intercept=str(intercept),slope=str(slope)))
                point.append((variable,z3.IntVal(str(value),ctx=ctx)))
            for form in hard:
                remaining()
                if not z3.is_true(z3.simplify(z3.substitute(form,*point))):raise ValueError('full original Boolean formula is false at common threshold')
            model_data=dict(source=str(source),source_sha256=source_hash,direction=direction,objective=objective.sexpr(),
                            integer_variables=coefficients,atom_coefficients=atom_values,objective_coefficients=[str(c) for c in objective_values])
            model_path=output/'coefficient-model.json';_publish(model_path,json.dumps(model_data,indent=2)+'\n',remaining)
            result.update(coefficient_model_path=str(model_path),coefficient_model_sha256=_digest(model_path))
            certificate=dict(schema='source-affine-ray-v1',source=str(source),source_sha256=source_hash,direction=direction,
                             objective=objective.sexpr(),integer_variables=coefficients,threshold=str(threshold))
            candidate=output/'candidate.json';candidate_identity=_publish(candidate,json.dumps(certificate,indent=2,ensure_ascii=False)+'\n',remaining)
            result.update(status='candidate',candidate_path=str(candidate),candidate_sha256=_digest(candidate),
                          threshold=str(threshold),reason='SAT affine-ray candidate; independent exact source checker required')
        remaining()
        if _digest(source)!=source_hash:raise ValueError('source bytes changed during analysis')
        remaining()
    except TimeoutError as error:result.update(status='timeout',answer=None,reason=str(error))
    except Unsupported as error:result.update(status='unsupported',answer=None,reason=str(error))
    except Exception as error:result.update(status='error',answer=None,reason=f'{type(error).__name__}: {error}')
    finally:
        if result['status']!='candidate':
            if candidate_identity is not None:_remove_owned(candidate,candidate_identity)
            result.update(candidate_path=None,candidate_sha256=None)
        result['elapsed_seconds']=time.monotonic()-started
        if owned:
            try:_publish(output/'result.json',json.dumps(result,indent=2,ensure_ascii=False,allow_nan=False)+'\n',lambda:None)
            except OSError as error:result['report_write_error']=str(error)
    return result
