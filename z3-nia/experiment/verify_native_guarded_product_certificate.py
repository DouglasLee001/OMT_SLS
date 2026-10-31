"""Read-only native/source guarded-product bridge; no satisfiability checks.

Native AST strings are untrusted. Reparse and bind them to the authoritative
source, then let the frozen independent exact checker validate the actual lift.
"""
from collections import Counter
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import time

TRANSLATION_SHA256='1abdad7d3975796a40a0f045bc740d4d9bfd9c3ac36e4a241c16d1bc14c7d3e8'
PREVIOUS_TRANSLATION_SHA256='dd897b9bd1aa4596cf899dc66da45f567d4a88c97a4e43e26a15337cabd39091'
LEGACY_TRANSLATION_SHA256='9fb3cf5db313d7968f68c75960999049969ea85d4b0931ef3ef516af4704d5b4'
FIELDS={'schema','source_hard','original_objective','direction','own_best','used_integer_names','used_bool_names','monomials','cuts','query_path','actual_query_smt2','truncations'}

def require(condition,message):
    if not condition:raise ValueError(message)

def digest(data):return hashlib.sha256(data).hexdigest()

def translation_module(value):
    if value is not None and hasattr(value,'validate_certificate'):
        path=Path(value.__file__).resolve(strict=True)
        identity=digest(path.read_bytes());require(identity in(TRANSLATION_SHA256,PREVIOUS_TRANSLATION_SHA256,LEGACY_TRANSLATION_SHA256),'translation checker identity mismatch')
        return value,path,identity
    path=Path(value)if value is not None else Path(__file__).with_name('verify_guarded_product_certificate.py')
    path=path.resolve(strict=True);identity=digest(path.read_bytes());require(path.is_file()and(identity==TRANSLATION_SHA256 or value is not None and identity in(PREVIOUS_TRANSLATION_SHA256,LEGACY_TRANSLATION_SHA256)),'translation checker identity mismatch')
    spec=importlib.util.spec_from_file_location('_frozen_independent_guarded_product_'+identity,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module,path,identity

def duplicate_free_object(pairs):
    result={}
    for key,value in pairs:
        require(key not in result,'duplicate native certificate JSON key');result[key]=value
    return result

def canonical_value(value):
    try:return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8')
    except (TypeError,ValueError,RecursionError)as error:raise ValueError('invalid native certificate value')from error

def validate_certificate(z3,native_certificate,*,expected_source,expected_query,expected_best,expected_source_sha256=None,expected_direction=None,deadline=None,translation_checker=None,max_nodes=4000000,max_bits=8192,max_variables=None):
    """Bind a native cert file/value to source/query/M and validate translation.

Expected source/query paths and own M are mandatory external authorities.
An optional source hash binds an already frozen snapshot. A mapping input's
certificate hash is a canonical-value hash, explicitly labelled in the result;
a file input is bound to its original JSON bytes before and after validation.
    """
    started=time.monotonic();deadline=started+5 if deadline is None else deadline
    base,checker_path,checker_hash=translation_module(translation_checker);budget=base.Budget(deadline,max_nodes,max_bits)
    require(max_variables is None or type(max_variables)is int and 0<max_variables<=8192,'invalid variable resource limit')
    require(checker_hash!=LEGACY_TRANSLATION_SHA256 or max_variables is None,'legacy checker has a fixed 256-variable resource contract')
    for value,label in((expected_source,'source'),(expected_query,'query')):
        require(isinstance(value,(str,Path))and Path(value).is_absolute(),'authoritative '+label+' path must be absolute')
    source=Path(expected_source).resolve(strict=True);query=Path(expected_query).resolve(strict=True)
    require(source.is_file()and query.is_file()and source!=query,'invalid authoritative source/query files')
    require(source.stat().st_size<=16*1024*1024 and query.stat().st_size<=64*1024*1024,'source/query size resource limit')
    source_bytes,query_bytes=source.read_bytes(),query.read_bytes();budget.step()
    require(len(source_bytes)<=16*1024*1024 and len(query_bytes)<=64*1024*1024,'source/query size resource limit')
    source_hash,query_hash=digest(source_bytes),digest(query_bytes)
    if expected_source_sha256 is not None:
        require(type(expected_source_sha256)is str and re.fullmatch('[0-9a-f]{64}',expected_source_sha256)and expected_source_sha256==source_hash,'authoritative source hash mismatch')
    require(type(expected_best)is str,'authoritative own M must be an exact decimal string');best=str(budget.integer(expected_best))
    native_path=None
    if type(native_certificate)is dict:
        native_bytes=canonical_value(native_certificate);native_mode='canonical_mapping_value'
    else:
        require(isinstance(native_certificate,(str,Path))and Path(native_certificate).is_absolute(),'native certificate path must be absolute')
        native_path=Path(native_certificate).resolve(strict=True)
        require(native_path.is_file()and native_path not in(source,query),'invalid native certificate file')
        require(native_path.stat().st_size<=64*1024*1024,'native certificate byte resource limit');native_bytes=native_path.read_bytes();native_mode='file_bytes'
    require(len(native_bytes)<=64*1024*1024,'native certificate byte resource limit');native_hash=digest(native_bytes);budget.step()
    try:cert=json.loads(native_bytes,object_pairs_hook=duplicate_free_object,parse_constant=lambda v:(_ for _ in()).throw(ValueError('nonfinite native JSON value')))
    except (UnicodeError,json.JSONDecodeError,RecursionError)as error:raise ValueError('invalid native certificate JSON')from error
    require(type(cert)is dict and set(cert)==FIELDS and cert['schema']=='native-source-guarded-product-bound-v1','invalid native certificate schema/fields')
    require(cert['own_best']==best,'native own M differs from authoritative incumbent')
    require(type(cert['source_hard'])is list and len(cert['source_hard'])<=100000 and all(type(v)is str for v in cert['source_hard']),'invalid native hard AST strings')
    require(type(cert['original_objective'])is str,'invalid native original objective')
    require(type(cert['actual_query_smt2'])is str and cert['actual_query_smt2'].encode('utf-8')==query_bytes,'embedded native actual query differs from authoritative query bytes')
    require(type(cert['query_path'])is str,'invalid native query path')
    if cert['query_path']:
        require(Path(cert['query_path']).is_absolute()and Path(cert['query_path']).is_file()and Path(cert['query_path']).resolve(strict=True)==query,'native query path differs from authoritative query')
    filtered,direction,source_symbols=base.source_text(source_bytes.decode('utf-8'),budget)
    require(cert['direction']==direction and(expected_direction is None or expected_direction==direction),'native/source/expected direction mismatch')
    declarations=[f for f in base.forms(filtered,budget)if f[0]in('set-logic','declare-const','declare-fun','define-fun')]
    def expression(text,wanted):
        roots=base.forms(text,budget);require(len(roots)==1,'native AST string must contain exactly one expression')
        def sort(node,local,depth=0):
            budget.step();require(depth<=256,'native printed AST depth resource limit')
            if isinstance(node,str):
                if node in('true','false'):return'Bool'
                if base.INTEGER.fullmatch(node):budget.integer(node);return'Int'
                symbol=base.name(node)
                if symbol in local:return local[symbol]
                require(symbol in source_symbols,'foreign native AST symbol');return'Int'
            require(type(node)is list and node and type(node[0])is str,'invalid native printed expression');op,args=node[0],node[1:]
            if op=='let':
                require(len(args)==2 and type(args[0])is list,'invalid native printer let');bindings={}
                for binding in args[0]:
                    require(type(binding)is list and len(binding)==2,'invalid native let binding');symbol=base.name(binding[0]);require(symbol not in bindings,'duplicate native let binding')
                    # SMT-LIB let bindings are simultaneous, not sequential.
                    bindings[symbol]=sort(binding[1],local,depth+1)
                return sort(args[1],local|bindings,depth+1)
            require(op in('+','-','*','=','<=','<','>=','>','and','or','not'),'unsupported native printed operator')
            sorts=[sort(v,local,depth+1)for v in args]
            if op in('+','-','*'):require(sorts and all(s=='Int'for s in sorts),'native arithmetic sort/arity');return'Int'
            if op in('=','<=','<','>=','>'):require(len(sorts)==2 and sorts==['Int','Int'],'native comparison sort/arity');return'Bool'
            require(all(s=='Bool'for s in sorts)and(op!='not'or len(sorts)==1),'native Boolean sort/arity');return'Bool'
        require(sort(roots[0],{})==wanted,'native printed expression sort mismatch');return base.render(roots[0])
    native_forms=[base.render(f)for f in declarations]
    native_forms.extend('(assert '+expression(text,'Bool')+')'for text in cert['source_hard'])
    native_forms.append('(minimize '+expression(cert['original_objective'],'Int')+')')
    # Permit only native-printer let in addition to the original source MVP;
    # its complete syntax/sorts, including unused bindings, were checked above.
    native_filtered='\n'.join(native_forms)
    def summary(text):
        context=z3.Context();parser=z3.Optimize(ctx=context)
        try:parser.from_string(text)
        except z3.Z3Exception as error:raise ValueError('native/source AST parse failed')from error
        goals=list(parser.objectives());require(len(goals)==1,'expected one original objective')
        return [f.sexpr()for f in parser.assertions()],goals[0].sexpr()
    source_hard,source_goal=summary(filtered);budget.step()
    native_hard,native_goal=summary(native_filtered);budget.step()
    require(Counter(native_hard)==Counter(source_hard),'native hard ASTs are not the complete authoritative source assertions')
    require(native_goal==source_goal,'native objective AST differs from authoritative source objective')
    converted=dict(schema='source-guarded-product-bound-v1',source=str(source),source_sha256=source_hash,direction=direction,own_best=best,original_objective=source_goal,used_integer_names=cert['used_integer_names'],used_bool_names=cert['used_bool_names'],monomials=cert['monomials'],cuts=cert['cuts'],query_path=str(query),query_sha256=query_hash,truncations=cert['truncations'])
    remaining=max_nodes-budget.work;require(remaining>0,'native/source bridge work resource limit')
    resources=dict(max_nodes=remaining,max_bits=max_bits)
    if max_variables is not None:resources['max_variables']=max_variables
    result=base.validate_certificate(z3,converted,deadline=deadline,expected_source=source,expected_query=query,expected_best=best,**resources)
    budget.step(result['work'])
    require(digest(source.read_bytes())==source_hash and digest(query.read_bytes())==query_hash,'authoritative source/query changed during verification')
    require(digest(checker_path.read_bytes())==checker_hash,'translation checker changed during verification')
    if native_path is not None:require(digest(native_path.read_bytes())==native_hash,'native certificate changed during verification')
    else:require(digest(canonical_value(native_certificate))==native_hash,'native certificate mapping changed during verification')
    budget.step()
    result.update(schema='native-guarded-product-translation-verification-v1',native_translation_verified=True,source_hard_verified=True,native_source_hard_count=len(native_hard),native_query_path_present=bool(cert['query_path']),native_certificate_path=str(native_path)if native_path is not None else None,native_certificate_sha256=native_hash,native_certificate_hash_kind=native_mode,translation_checker_sha256=checker_hash,translation_variable_resource_limit=256 if checker_hash==LEGACY_TRANSLATION_SHA256 else 8192 if max_variables is None else max_variables,converted_certificate=converted,work=budget.work,elapsed_seconds=time.monotonic()-started)
    budget.step();return result
