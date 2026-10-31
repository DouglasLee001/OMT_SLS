"""Full-source guarded cubic consequences, as a diagnostic QF_LRA relaxation.

Import is SDK-free. A caller must impose a hard child deadline. Relaxation SAT
is never a source model; UNSAT alone is never an optimality result.
"""
from __future__ import annotations
import hashlib,importlib.util,json,math,os,re,sys,time
from pathlib import Path

MAX_VARIABLES=256
MAX_MONOMIALS=20000
MAX_CUTS=12000
MAX_PAIR_SCANS=200000
MAX_LINEAR_MOMENT_WORK=50000
MAX_QUADRATIC_MOMENT_WORK=50000
MAX_NODES=100000
MAX_BITS=8192
MAX_SOURCE_BYTES=16*1024*1024

class Unsupported(ValueError):pass
class _GenerationExpired(Exception):pass
class _IndexScanCap(Exception):pass

def _clean(poly):
    result={}
    for monomial,coefficient in poly.items():
        if type(coefficient)is not int:raise Unsupported('noninteger polynomial coefficient')
        key=tuple(sorted(monomial));result[key]=result.get(key,0)+coefficient
    result={m:c for m,c in result.items()if c}
    if len(result)>MAX_MONOMIALS:raise Unsupported('polynomial monomial cap')
    if any(type(c)is not int or c.bit_length()>MAX_BITS for c in result.values()):raise Unsupported('integer coefficient bit cap')
    return result

def _add(left,right,sign=1):
    result=dict(left)
    for monomial,coefficient in right.items():result[monomial]=result.get(monomial,0)+sign*coefficient
    return _clean(result)

def _scale(poly,value):return _clean({m:c*value for m,c in poly.items()})
def _degree(poly):return max(map(len,poly),default=0)

def _multiply(left,right,remaining=lambda:None):
    result={}
    for a,ca in left.items():
        for b,cb in right.items():
            remaining();monomial=tuple(sorted(a+b))
            if len(monomial)>3:raise Unsupported('polynomial degree exceeds three')
            result[monomial]=result.get(monomial,0)+ca*cb
            if len(result)>MAX_MONOMIALS:raise Unsupported('polynomial monomial cap')
    return _clean(result)

def _normalized(relation,difference,polarity=True):
    if relation=='>=':positive=dict(difference)
    elif relation=='>':positive=_add(difference,{():-1})
    elif relation=='<=':positive=_scale(difference,-1)
    elif relation=='<':positive=_add(_scale(difference,-1),{():-1})
    else:raise Unsupported('equality has no nonnegative complementary fact')
    return _clean(positive)if polarity else _add(_scale(positive,-1),{():-1})

def _bound_poly(objective,direction,best):
    if direction=='min':return _add(_scale(objective,-1),{():best-1})
    if direction=='max':return _add(objective,{():-best-1})
    raise ValueError('direction must be min or max')

def _strict(helper):
    if helper is not None and hasattr(helper,'_query')and hasattr(helper,'_commands'):return helper
    path=(Path(helper)if helper is not None else Path(__file__).resolve().parents[2]/'script/run_strict_bound.py').resolve(strict=True)
    name='_guarded_product_strict_'+hashlib.sha256(str(path).encode()).hexdigest()[:16]
    if name not in sys.modules:
        spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module)
    return sys.modules[name]

def _raw_validate(forms,remaining):
    declarations={};bodies=[];nodes=0
    for form in forms:
        remaining()
        if not isinstance(form,list)or not form:raise Unsupported('invalid source command')
        head=form[0]
        if head=='declare-const'and len(form)==3 and form[2]=='Int':declarations[form[1]]='Int'
        elif head=='declare-fun'and len(form)==4 and form[2]==[]and form[3]=='Int':declarations[form[1]]='Int'
        elif head=='define-fun'and len(form)==5 and form[2]==[]and form[3]=='Int':declarations[form[1]]='Int';bodies.append((form[4],'Int'))
        elif head=='assert'and len(form)==2:bodies.append((form[1],'Bool'))
        elif head=='minimize'and len(form)==2:bodies.append((form[1],'Int'))
        elif head=='set-logic'and len(form)==2:pass
        else:raise Unsupported('unsupported source declaration/command: '+str(head))
    for expression,sort in bodies:
        degrees={};todo=[(expression,sort,False)]
        while todo:
            remaining();node,wanted,ready=todo.pop();key=id(node)
            if ready:
                children=node[1:];head=node[0]
                if wanted=='Int':
                    degree=sum(degrees[id(c)]for c in children)if head=='*'else max((degrees[id(c)]for c in children),default=0)
                    if degree>2:raise Unsupported('source syntactic polynomial degree exceeds two')
                    degrees[key]=degree
                continue
            nodes+=1
            if nodes>MAX_NODES:raise Unsupported('raw source node cap')
            if isinstance(node,str):
                if wanted=='Bool'and node in('true','false'):continue
                if wanted=='Int'and node in declarations:degrees[key]=1;continue
                if wanted=='Int'and re.fullmatch(r'-?[0-9]+',node):
                    if len(node)>2500 or int(node).bit_length()>MAX_BITS:raise Unsupported('source integer bit cap')
                    degrees[key]=0;continue
                raise Unsupported('unsupported raw symbol/sort: '+node[:100])
            if not node or not isinstance(node[0],str):raise Unsupported('invalid raw expression')
            head,children=node[0],node[1:]
            if wanted=='Int'and head in('+','-','*')and children:child_sort='Int'
            elif wanted=='Bool'and head in('=','<=','<','>=','>')and len(children)==2:child_sort='Int'
            elif wanted=='Bool'and head in('and','or'):child_sort='Bool'
            elif wanted=='Bool'and head=='not'and len(children)==1:child_sort='Bool'
            else:raise Unsupported('unsupported raw operator/sort: '+head)
            todo.append((node,wanted,True));todo.extend((c,child_sort,False)for c in children)

def _leading_monomial(poly,checkpoint=lambda:None):
    """Graded exponent-lex order: greatest degree, then smallest sorted tuple."""
    leading=None
    for monomial,coefficient in poly.items():
        checkpoint()
        if not coefficient:continue
        monomial=tuple(sorted(monomial))
        if leading is None or len(monomial)>len(leading)or(len(monomial)==len(leading)and monomial<leading):leading=monomial
    if leading is None:raise Unsupported('zero polynomial has no leading monomial')
    return leading

def _index_state(initial_scans=0):
    if type(initial_scans)is not int or not 0<=initial_scans<=MAX_PAIR_SCANS:raise ValueError('invalid indexed scan offset')
    return dict(total_scans=initial_scans,index_scans=0,leading_term_visits=0,inventory_visits=0,split_visits=0,bucket_visits=0,pair_visits=0,multiply_term_visits=0,candidate_pairs=0,truncations=[])

def _indexed_charge(state,remaining,optional,category):
    remaining()
    if not optional():raise _GenerationExpired
    if state['total_scans']>=MAX_PAIR_SCANS:raise _IndexScanCap
    state['total_scans']+=1;state['index_scans']+=1;state[category]+=1

def _indexed_rlt_pairs(facts,inventory,remaining,optional,state):
    """Necessary LM filter, lazily enumerated; full products are checked later.

    If a nonzero product is covered by the inventory, its leading monomial is
    covered too. This monomial order is multiplicative, including cancellation,
    so splitting each inventory monomial cannot lose a covered pair uncapped.
    """
    def charge(category):_indexed_charge(state,remaining,optional,category)
    try:
        buckets={}
        for index,(_,poly)in enumerate(facts):
            leading=_leading_monomial(poly,lambda:charge('leading_term_visits'))
            charge('bucket_visits')
            if 0<len(leading)<=2:buckets.setdefault(leading,[]).append(index)
        relevant=[]
        for monomial in inventory:
            charge('inventory_visits')
            if len(monomial)in(2,3):relevant.append(tuple(sorted(monomial)))
        seen=set()
        for monomial in sorted(relevant,key=lambda m:(-len(m),m)):
            # Removing one occurrence preserves multiplicities, unlike set subtraction.
            splits=[((monomial[0],),(monomial[1],))]if len(monomial)==2 else[((variable,),monomial[:position]+monomial[position+1:])for position,variable in enumerate(monomial)if position==0 or variable!=monomial[position-1]]
            for left,right in splits:
                charge('split_visits');charge('bucket_visits');a=buckets.get(left,());charge('bucket_visits');b=buckets.get(right,())
                if not a or not b:continue
                for position,i in enumerate(a):
                    charge('bucket_visits')
                    for other in range(position if left==right else 0,len(b)):
                        charge('pair_visits');j=b[other];pair=(min(i,j),max(i,j))
                        if pair in seen:continue
                        seen.add(pair);state['candidate_pairs']+=1;yield pair
    except _GenerationExpired:
        if 'generation_deadline'not in state['truncations']:state['truncations'].append('generation_deadline')
    except _IndexScanCap:
        if 'index_scan_cap'not in state['truncations']:state['truncations'].append('index_scan_cap')

def _closure(atoms,variables,bound,inventory,remaining,optional,*,_equality_candidates=None,_pair_scan_offset=0,_rlt_policy='all-pairs'):
    """Only optional consequences are capped; never remove a base monomial."""
    if _rlt_policy not in('all-pairs','indexed'):raise ValueError('unsupported RLT policy')
    inventory=set(inventory);cuts=[];truncations=[];pairs=0;equalities=0;products=0
    index_state=_index_state(_pair_scan_offset)if _rlt_policy=='indexed'else None
    def truncated(reason):
        if reason not in truncations:truncations.append(reason)
    def checkpoint():
        remaining()
        if not optional():raise _GenerationExpired
    def multiplication_checkpoint():
        if index_state is None:checkpoint()
        else:_indexed_charge(index_state,remaining,optional,'multiply_term_visits')
    def room():
        remaining()
        if len(cuts)>=MAX_CUTS:truncated('cut_cap');return False
        if not optional():truncated('generation_deadline');return False
        return True
    def finish():
        stats=dict(pair_scans=pairs,equality_cuts=equalities,product_cuts=products,truncations=truncations)
        if index_state is not None:
            stats.update({k:v for k,v in index_state.items()if k!='truncations'})
            stats['truncations']=list(dict.fromkeys(truncations+index_state['truncations']))
        return inventory,cuts,stats
    equality_work=((atom,variable)for atom in atoms if atom['relation']=='='for variable in variables)if _equality_candidates is None else((atoms[index],variable)for index,variable in _equality_candidates)
    for atom,variable in equality_work:
        if not room():return finish()
        try:
            poly=_multiply(atom['poly'],{(variable,):1},multiplication_checkpoint);checkpoint()
        except _GenerationExpired:
            truncated('generation_deadline');return finish()
        except _IndexScanCap:
            truncated('index_scan_cap');return finish()
        except Unsupported:truncated('optional_coefficient_or_monomial_cap');continue
        if not poly:continue
        new=set(poly)-{()}-inventory
        if len(inventory)+len(new)>MAX_MONOMIALS:truncated('monomial_cap');continue
        if not room():return finish()
        inventory.update(new);cuts.append(dict(kind='equality_times_variable',atom=atom['atom'],variable=variable));equalities+=1
    facts=[]
    for atom in atoms+[dict(atom='@bound',relation='>=',poly=bound)]:
        if not room():return finish()
        if atom['relation']=='=':continue
        for polarity in(True,False):
            try:poly=_normalized(atom['relation'],atom['poly'],polarity)
            except Unsupported:truncated('optional_coefficient_or_monomial_cap');continue
            if _degree(poly)==0:continue
            facts.append((dict(atom=atom['atom'],polarity=polarity),poly))
    pair_work=((i,j)for i in range(len(facts))for j in range(i,len(facts)))if index_state is None else _indexed_rlt_pairs(facts,inventory,remaining,optional,index_state)
    for i,j in pair_work:
        left,h=facts[i];right,k=facts[j]
        if not room():return finish()
        if index_state is None and pairs+_pair_scan_offset>=MAX_PAIR_SCANS:truncated('pair_scan_cap');return finish()
        pairs+=1
        if min(_degree(h),_degree(k))>1 or _degree(h)+_degree(k)>3:continue
        try:
            poly=_multiply(h,k,multiplication_checkpoint);checkpoint()
        except _GenerationExpired:
            truncated('generation_deadline');return finish()
        except _IndexScanCap:
            truncated('index_scan_cap');return finish()
        except Unsupported:truncated('optional_coefficient_or_monomial_cap');continue
        if not poly or any(m and m not in inventory for m in poly):continue
        if not room():return finish()
        cuts.append(dict(kind='nonnegative_product',left=left,right=right));products+=1
    return finish()

def _shared_equality_candidates(atoms,variables,bound,inventory,remaining,optional):
    """Discovery only: shared cubic monomials, never new mathematical premises."""
    used=set(variables);linear=set();quadratic={};equations=[];base_neighbors={};candidates=set();scans=0;truncations=[]
    def checkpoint():
        remaining()
        if not optional():raise _GenerationExpired
    def edges(target,pair):
        a,b=pair;target.setdefault(a,set()).add(b);target.setdefault(b,set()).add(a)
    def fact_support(poly):
        if _degree(poly)<=1:linear.update(m[0]for m in poly if len(m)==1)
        for monomial in poly:
            checkpoint()
            if len(monomial)==2:edges(quadratic,monomial)
    def candidate(index,variable):
        nonlocal scans
        checkpoint()
        if scans>=MAX_PAIR_SCANS:return False
        scans+=1
        if variable in used:candidates.add((index,variable))
        return True
    capped=False
    try:
        for index,atom in enumerate(atoms):
            checkpoint();poly=_clean(atom['poly'])
            if atom['relation']=='=':
                if poly:equations.append((index,poly))
            else:fact_support(poly)
        fact_support(_clean(bound))
        for monomial in inventory:
            checkpoint()
            if len(monomial)==2:edges(base_neighbors,tuple(sorted(monomial)))
        for index,poly in equations:
            checkpoint()
            if _degree(poly)==2:
                for monomial in sorted(poly):
                    if len(monomial)!=2:continue
                    for common,leftover in sorted(set((monomial,monomial[::-1]))):
                        if leftover not in linear:continue
                        for variable in sorted(quadratic.get(common,())):
                            if not candidate(index,variable):capped=True;break
                        if capped:break
                    if capped:break
            else:
                for monomial in sorted(poly):
                    if len(monomial)!=1:continue
                    for variable in sorted(base_neighbors.get(monomial[0],())):
                        if not candidate(index,variable):capped=True;break
                    if capped:break
            if capped:break
    except _GenerationExpired:truncations.append('generation_deadline')
    if capped:truncations.append('join_scan_cap')
    return sorted(candidates),dict(join_scans=scans,join_candidates=len(candidates),truncations=truncations)

def _sparse_closure(atoms,variables,bound,inventory,remaining,optional):
    candidates,discovery=_shared_equality_candidates(atoms,variables,bound,inventory,remaining,optional)
    lifted,cuts,stats=_closure(atoms,variables,bound,inventory,remaining,optional,_equality_candidates=candidates,_pair_scan_offset=discovery['join_scans'])
    stats['truncations']=list(dict.fromkeys(discovery['truncations']+stats['truncations']))
    stats.update({k:v for k,v in discovery.items()if k!='truncations'})
    stats.update(closure_policy='shared-cubic',total_scans=stats['join_scans']+stats['pair_scans'])
    return lifted,cuts,stats

def _indexed_sparse_closure(atoms,variables,bound,inventory,remaining,optional):
    candidates,discovery=_shared_equality_candidates(atoms,variables,bound,inventory,remaining,optional)
    lifted,cuts,stats=_closure(atoms,variables,bound,inventory,remaining,optional,_equality_candidates=candidates,_pair_scan_offset=discovery['join_scans'],_rlt_policy='indexed')
    stats['truncations']=list(dict.fromkeys(discovery['truncations']+stats['truncations']))
    stats.update({k:v for k,v in discovery.items()if k!='truncations'})
    stats['closure_policy']='indexed-shared-cubic'
    return lifted,cuts,stats

class _MomentWorkCap(Exception):pass

def _moment_clean(poly,charge):
    """Canonical exact source Eq; every input/output term visit is charged."""
    accumulated={}
    for monomial,coefficient in poly.items():
        charge('moment_term_visits')
        if type(coefficient)is not int:raise Unsupported('noninteger moment coefficient')
        key=tuple(sorted(monomial));accumulated[key]=accumulated.get(key,0)+coefficient
    result={}
    for monomial,coefficient in accumulated.items():
        charge('moment_normalization_visits')
        if coefficient.bit_length()>MAX_BITS:raise Unsupported('moment coefficient bit cap')
        if coefficient:result[monomial]=coefficient
        if len(result)>MAX_MONOMIALS:raise Unsupported('moment polynomial monomial cap')
    return result

def _moment_product(poly,variable,charge):
    """Full exact e*x, with charged multiplication and normalization passes."""
    product={}
    for monomial,coefficient in poly.items():
        charge('moment_multiply_term_visits');key=tuple(sorted(monomial+(variable,)))
        if len(key)>3:raise Unsupported('moment polynomial degree cap')
        product[key]=product.get(key,0)+coefficient
        if len(product)>MAX_MONOMIALS:raise Unsupported('moment polynomial monomial cap')
    result={}
    for monomial,coefficient in product.items():
        charge('moment_normalization_visits')
        if coefficient.bit_length()>MAX_BITS:raise Unsupported('moment coefficient bit cap')
        if coefficient:result[monomial]=coefficient
    return result

def _append_linear_moments(atoms,variables,inventory,cuts,remaining,optional):
    """Append covered linear-Eq moments; inventory and the old cut prefix stay fixed.

    This independent50k counter is additional to the completed EB200k phase.
    It uses exactly the caller's remaining/optional clocks; no clock is reset.
    Sorted smallest-neighbor enumeration avoids uncharged set intersections.
    """
    initial=len(cuts)
    categories=('inventory','adjacency','atom','term','normalization','support','variable','ledger','neighbor','candidate','intersection','membership','multiply_term','commit')
    stats={'moment_'+name+'_visits':0 for name in categories};stats.update(moment_work=0,moment_cuts=0,moment_truncations=[])
    def checkpoint():
        remaining()
        if not optional():raise _GenerationExpired
    def charge(category):
        checkpoint()
        if stats['moment_work']>=MAX_LINEAR_MOMENT_WORK:raise _MomentWorkCap
        stats['moment_work']+=1;stats[category]+=1
    def room():
        checkpoint()
        if len(cuts)>=MAX_CUTS:
            if 'moment_cut_cap'not in stats['moment_truncations']:stats['moment_truncations'].append('moment_cut_cap')
            return False
        return True
    try:
        if not room():return inventory,cuts,stats
        equations=[];required=set()
        for atom in atoms:
            charge('moment_atom_visits')
            if atom['relation']!='=':continue
            poly=_moment_clean(atom['poly'],charge);support=[];linear=True
            for monomial in poly:
                charge('moment_support_visits')
                if len(monomial)>1:linear=False
                elif monomial:support.append(monomial[0])
            if linear and support:
                equations.append((atom,poly,tuple(sorted(support))))
                for variable in support:charge('moment_neighbor_visits');required.add(variable)
        if not equations:return inventory,cuts,stats
        used=set()
        for variable in variables:charge('moment_variable_visits');used.add(variable)
        seen=set()
        for cut in cuts:
            charge('moment_ledger_visits')
            if cut['kind']=='equality_times_variable':seen.add((cut['atom'],cut['variable']))
        neighbors={}
        # Building no partial candidate cuts until the complete index is ready
        # makes an index cap independent of set iteration order.
        for monomial in inventory:
            charge('moment_inventory_visits')
            if len(monomial)!=2:continue
            a,b=monomial
            for left,right in ((a,b),(b,a)):
                charge('moment_membership_visits')
                if left in required:charge('moment_adjacency_visits');neighbors.setdefault(left,set()).add(right)
        for atom,poly,support in equations:
            if not room():break
            adjacent=[]
            for variable in support:charge('moment_neighbor_visits');adjacent.append((len(neighbors.get(variable,())),variable))
            adjacent.sort();checkpoint()
            if not adjacent[0][0]:continue
            seed=sorted(neighbors[adjacent[0][1]]);checkpoint()
            for variable in seed:
                charge('moment_candidate_visits');charge('moment_membership_visits')
                if variable not in used:continue
                covered=True
                for position in range(1,len(adjacent)):
                    name=adjacent[position][1];charge('moment_intersection_visits')
                    if variable not in neighbors[name]:covered=False;break
                if not covered:continue
                pair=(atom['atom'],variable);charge('moment_membership_visits')
                if pair in seen:continue
                product=_moment_product(poly,variable,charge);checkpoint()
                for monomial in product:
                    charge('moment_membership_visits')
                    if monomial and monomial not in inventory:covered=False;break
                if not product or not covered:continue
                if not room():break
                cut=dict(kind='equality_times_variable',atom=atom['atom'],variable=variable);before=len(cuts)
                try:
                    charge('moment_commit_visits');seen.add(pair);cuts.append(cut);charge('moment_commit_visits')
                except BaseException:
                    while len(cuts)>before:cuts.pop()
                    seen.discard(pair);raise
                stats['moment_cuts']=len(cuts)-initial
    except _GenerationExpired:stats['moment_truncations'].append('moment_generation_deadline')
    except _MomentWorkCap:stats['moment_truncations'].append('moment_work_cap')
    except Unsupported:stats['moment_truncations'].append('moment_optional_arithmetic_cap')
    except MemoryError:stats['moment_truncations'].append('moment_memory_cap')
    stats['moment_cuts']=len(cuts)-initial
    return inventory,cuts,stats

def _linear_moment_closure(atoms,variables,bound,inventory,remaining,optional):
    lifted,cuts,stats=_indexed_sparse_closure(atoms,variables,bound,inventory,remaining,optional)
    base_equalities=stats['equality_cuts'];base_count=len(cuts)
    lifted,cuts,extra=_append_linear_moments(atoms,variables,lifted,cuts,remaining,optional)
    stats.update(extra);stats['truncations']=list(dict.fromkeys(stats['truncations']+extra['moment_truncations']))
    stats.update(closure_policy='indexed-linear-equality-moments',base_equality_cuts=base_equalities,base_cut_count=base_count)
    return lifted,cuts,stats

class _QuadraticWorkCap(Exception):pass

def _quadratic_product(poly,multiplier,charge):
    """Exact e*m reconstruction; no lifted coordinate is treated as an Int."""
    product={}
    for monomial,coefficient in poly.items():
        charge('quadratic_multiply_term_visits');key=tuple(sorted(monomial+multiplier))
        if len(key)>3:raise Unsupported('quadratic moment degree cap')
        product[key]=product.get(key,0)+coefficient
        if len(product)>MAX_MONOMIALS:raise Unsupported('quadratic moment polynomial cap')
    cleaned={}
    for monomial,coefficient in product.items():
        charge('quadratic_normalization_visits')
        if coefficient.bit_length()>MAX_BITS:raise Unsupported('quadratic moment coefficient cap')
        if coefficient:cleaned[monomial]=coefficient
    return cleaned

def _append_quadratic_moments(atoms,variables,inventory,cuts,remaining,optional):
    """Append covered linear E*(x*y) after Task28, sharing its original clocks.

    Carrier lists use existing cubics with one occurrence removed. Only full
    product coverage is required; the pair needs no separate lifted coordinate.
    """
    initial=len(cuts);categories=('atom','term','normalization','support','variable','ledger','inventory','removal','carrier','neighbor','candidate','intersection','membership','multiply_term','commit')
    stats={'quadratic_'+name+'_visits':0 for name in categories};stats.update(quadratic_work=0,quadratic_cuts=0,quadratic_truncations=[])
    def checkpoint():
        remaining()
        if not optional():raise _GenerationExpired
    def charge(category):
        checkpoint()
        if stats['quadratic_work']>=MAX_QUADRATIC_MOMENT_WORK:raise _QuadraticWorkCap
        stats['quadratic_work']+=1;stats[category]+=1
    def room():
        checkpoint()
        if len(cuts)>=MAX_CUTS:
            if 'quadratic_cut_cap'not in stats['quadratic_truncations']:stats['quadratic_truncations'].append('quadratic_cut_cap')
            return False
        return True
    try:
        if not room():return inventory,cuts,stats
        equations=[];required=set()
        for atom in atoms:
            charge('quadratic_atom_visits')
            if atom['relation']!='=':continue
            poly=_moment_clean(atom['poly'],lambda name:charge(name.replace('moment_','quadratic_',1)));support=[];linear=True
            for monomial in poly:
                charge('quadratic_support_visits')
                if len(monomial)>1:linear=False
                elif monomial:support.append(monomial[0])
            if linear and support:
                equations.append((atom,poly,tuple(sorted(support))))
                for variable in support:charge('quadratic_neighbor_visits');required.add(variable)
        if not equations:return inventory,cuts,stats
        used=set()
        for variable in variables:charge('quadratic_variable_visits');used.add(variable)
        seen=set()
        for cut in cuts:
            charge('quadratic_ledger_visits')
            if cut['kind']=='equality_times_monomial':seen.add((cut['atom'],tuple(cut['variables'])))
        cubics=[]
        for monomial in inventory:
            charge('quadratic_inventory_visits')
            if len(monomial)==3:cubics.append(monomial)
        carriers={}
        for cubic in sorted(cubics):
            for position,variable in enumerate(cubic):
                if position and variable==cubic[position-1]:continue
                charge('quadratic_removal_visits');charge('quadratic_membership_visits')
                if variable not in required:continue
                pair=cubic[:position]+cubic[position+1:]
                charge('quadratic_carrier_visits');carriers.setdefault(variable,set()).add(pair)
        for atom,poly,support in equations:
            if not room():break
            adjacent=[]
            for variable in support:charge('quadratic_neighbor_visits');adjacent.append((len(carriers.get(variable,())),variable))
            adjacent.sort();checkpoint()
            if not adjacent[0][0]:continue
            seed=sorted(carriers[adjacent[0][1]]);checkpoint()
            for pair in seed:
                charge('quadratic_candidate_visits');covered=True
                for variable in pair:
                    charge('quadratic_membership_visits')
                    if variable not in used:covered=False;break
                if not covered:continue
                for position in range(1,len(adjacent)):
                    charge('quadratic_intersection_visits')
                    if pair not in carriers[adjacent[position][1]]:covered=False;break
                if not covered:continue
                key=(atom['atom'],pair);charge('quadratic_membership_visits')
                if key in seen:continue
                product=_quadratic_product(poly,pair,charge);checkpoint()
                for monomial in product:
                    charge('quadratic_membership_visits')
                    if monomial and monomial not in inventory:covered=False;break
                if not product or not covered:continue
                if not room():break
                cut=dict(kind='equality_times_monomial',atom=atom['atom'],variables=list(pair));before=len(cuts)
                try:
                    charge('quadratic_commit_visits');seen.add(key);cuts.append(cut);charge('quadratic_commit_visits')
                except BaseException:
                    while len(cuts)>before:cuts.pop()
                    seen.discard(key);raise
                stats['quadratic_cuts']=len(cuts)-initial
    except _GenerationExpired:stats['quadratic_truncations'].append('quadratic_generation_deadline')
    except _QuadraticWorkCap:stats['quadratic_truncations'].append('quadratic_work_cap')
    except Unsupported:stats['quadratic_truncations'].append('quadratic_optional_arithmetic_cap')
    except MemoryError:stats['quadratic_truncations'].append('quadratic_memory_cap')
    stats['quadratic_cuts']=len(cuts)-initial
    return inventory,cuts,stats

def _quadratic_moment_closure(atoms,variables,bound,inventory,remaining,optional):
    lifted,cuts,stats=_linear_moment_closure(atoms,variables,bound,inventory,remaining,optional);base_count=len(cuts)
    lifted,cuts,extra=_append_quadratic_moments(atoms,variables,lifted,cuts,remaining,optional)
    stats.update(extra);stats['truncations']=list(dict.fromkeys(stats['truncations']+extra['quadratic_truncations']))
    stats.update(closure_policy='indexed-quadratic-equality-moments',quadratic_base_cut_count=base_count)
    return lifted,cuts,stats

def _profile(closure_policy,variable_limit):
    if type(closure_policy)is not str or type(variable_limit)is not int or(closure_policy,variable_limit)not in(('all-variables',256),('shared-cubic',8192),('indexed-shared-cubic',8192)):
        raise ValueError('only(all-variables,256),(shared-cubic,8192)or(indexed-shared-cubic,8192)profiles supported')
    return closure_policy,variable_limit

def _analysis_profile(closure_policy,variable_limit):
    if type(closure_policy)is str and closure_policy=='indexed-linear-equality-moments':
        if type(variable_limit)is not int or variable_limit!=8192:raise ValueError('indexed-linear-equality-moments requires variable_limit8192')
        return closure_policy,variable_limit
    return _profile(closure_policy,variable_limit)

def _quadratic_profile(closure_policy,variable_limit):
    if type(closure_policy)is str and closure_policy=='indexed-quadratic-equality-moments':
        if type(variable_limit)is not int or variable_limit!=8192:raise ValueError('indexed-quadratic-equality-moments requires variable_limit8192')
        return closure_policy,variable_limit
    return _analysis_profile(closure_policy,variable_limit)

def _digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def _source_guard(path,expected,remaining):
    remaining()
    if _digest(path)!=expected:raise ValueError('source bytes changed during analysis')
    remaining()

def _artifact_guard(source,source_hash,query,query_hash,certificate,certificate_hash,remaining):
    _source_guard(source,source_hash,remaining)
    if _digest(query)!=query_hash:raise ValueError('query artifact changed')
    remaining()
    if _digest(certificate)!=certificate_hash:raise ValueError('certificate artifact changed')
    remaining()

def _remove_owned(path,identity):
    try:
        stat=Path(path).lstat()
        if (stat.st_dev,stat.st_ino)==identity:Path(path).unlink()
    except FileNotFoundError:pass

def _publish(path,content,remaining):
    remaining();path=Path(path);temporary=path.with_name(path.name+'.own-tmp')
    fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600);stat=os.fstat(fd);identity=(stat.st_dev,stat.st_ino);published=False
    try:
        data=content.encode('utf-8');offset=0
        while offset<len(data):remaining();offset+=os.write(fd,data[offset:offset+65536])
        os.close(fd);fd=-1;remaining();os.link(temporary,path);published=True;remaining();_remove_owned(temporary,identity);remaining();return identity
    except BaseException:
        if published:_remove_owned(path,identity)
        raise
    finally:
        if fd>=0:os.close(fd)
        _remove_owned(temporary,identity)

def _deadline_gate(result,deadline,now=None):
    if deadline is not None and(time.monotonic()if now is None else now)>=deadline:
        result.update(status='timeout',answer=None,diagnostic_bound_unsat=False,is_optimal=False,reason='guarded-product deadline reached')

def _source_order(z3,roots,remaining):
    order=[];seen=set();todo=[(node,False)for node in roots]
    comparisons={z3.Z3_OP_EQ,z3.Z3_OP_LE,z3.Z3_OP_LT,z3.Z3_OP_GE,z3.Z3_OP_GT}
    arithmetic={z3.Z3_OP_ADD,z3.Z3_OP_SUB,z3.Z3_OP_UMINUS,z3.Z3_OP_MUL}
    boolean={z3.Z3_OP_AND,z3.Z3_OP_OR,z3.Z3_OP_NOT}
    while todo:
        remaining();node,ready=todo.pop();identity=node.get_id()
        if ready:order.append(node);continue
        if identity in seen:continue
        seen.add(identity)
        if len(seen)>MAX_NODES:raise Unsupported('source DAG node cap')
        if not z3.is_app(node):raise Unsupported('source quantifier or bound variable')
        children=node.children();kind=node.decl().kind();sort=node.sort().kind();sorts=[c.sort().kind()for c in children]
        if z3.is_int_value(node):valid=not children and node.as_long().bit_length()<=MAX_BITS
        elif z3.is_true(node)or z3.is_false(node):valid=not children
        elif kind==z3.Z3_OP_UNINTERPRETED:valid=not children and sort==z3.Z3_INT_SORT
        elif kind in arithmetic:valid=bool(children)and sort==z3.Z3_INT_SORT and all(s==z3.Z3_INT_SORT for s in sorts)
        elif kind in comparisons:valid=sort==z3.Z3_BOOL_SORT and len(children)==2 and sorts==[z3.Z3_INT_SORT]*2
        elif kind in boolean:valid=sort==z3.Z3_BOOL_SORT and all(s==z3.Z3_BOOL_SORT for s in sorts)and(kind!=z3.Z3_OP_NOT or len(children)==1)
        else:valid=False
        if not valid:raise Unsupported('unsupported source operator/sort: '+str(node.decl().name()))
        todo.append((node,True));todo.extend((c,False)for c in reversed(children))
    return order

def _source_polynomials(z3,order,remaining,variable_limit=MAX_VARIABLES):
    polys={};variables={};atoms=[];relations={z3.Z3_OP_EQ:'=',z3.Z3_OP_LE:'<=',z3.Z3_OP_LT:'<',z3.Z3_OP_GE:'>=',z3.Z3_OP_GT:'>'}
    for node in order:
        remaining();identity=node.get_id();kind=node.decl().kind();children=node.children()
        if node.sort().kind()==z3.Z3_INT_SORT:
            if z3.is_int_value(node):poly={():node.as_long()}
            elif kind==z3.Z3_OP_UNINTERPRETED:
                name=str(node.decl().name())
                if name in variables and not variables[name].eq(node):raise Unsupported('ambiguous source Int name')
                variables[name]=node;poly={(name,):1}
                if len(variables)>variable_limit:raise Unsupported('used Int variable cap')
            elif kind==z3.Z3_OP_MUL:
                poly={():1}
                for child in children:poly=_multiply(poly,polys[child.get_id()],remaining)
            else:
                poly=polys[children[0].get_id()]
                if len(children)==1 and kind in(z3.Z3_OP_SUB,z3.Z3_OP_UMINUS):poly=_scale(poly,-1)
                for child in children[1:]:poly=_add(poly,polys[child.get_id()],-1 if kind==z3.Z3_OP_SUB else 1)
            if _degree(poly)>2:raise Unsupported('source polynomial degree exceeds two')
            polys[identity]=_clean(poly)
        elif kind in relations:
            atoms.append(dict(atom=node.sexpr(),relation=relations[kind],poly=_add(polys[children[0].get_id()],polys[children[1].get_id()],-1),node=node))
    return polys,variables,atoms

def _fresh_prefix(source):
    index=0
    while f'guarded_product_lift_{index}_'in source:index+=1
    return f'guarded_product_lift_{index}_'

def _lift_query(z3,ctx,order,hard,objective,polys,atoms,bound,inventory,cuts,prefix,remaining):
    monomials=[dict(name=prefix+str(i),variables=list(m))for i,m in enumerate(sorted(inventory,key=lambda m:(len(m),m)))]
    symbols={tuple(m['variables']):z3.Real(m['name'],ctx=ctx)for m in monomials}
    def lift(poly):
        remaining();terms=[z3.RealVal(str(c),ctx=ctx)*(symbols[m]if m else z3.RealVal(1,ctx=ctx))for m,c in sorted(poly.items())]
        return z3.Sum(*terms)if terms else z3.RealVal(0,ctx=ctx)
    atom_records={a['atom']:a for a in atoms};atom_values={};booleans={}
    by_id={a['node'].get_id():a for a in atoms}
    for node in order:
        remaining()
        if node.sort().kind()!=z3.Z3_BOOL_SORT:continue
        identity=node.get_id();kind=node.decl().kind();children=node.children()
        if identity in by_id:
            atom=by_id[identity];value=lift(atom['poly'])==0 if atom['relation']=='='else lift(_normalized(atom['relation'],atom['poly']))>=0
            atom_values[atom['atom']]=value
        elif z3.is_true(node)or z3.is_false(node):value=node
        elif kind==z3.Z3_OP_NOT:value=z3.Not(booleans[children[0].get_id()])
        elif kind==z3.Z3_OP_AND:value=z3.And(*[booleans[c.get_id()]for c in children])if children else z3.BoolVal(True,ctx=ctx)
        elif kind==z3.Z3_OP_OR:value=z3.Or(*[booleans[c.get_id()]for c in children])if children else z3.BoolVal(False,ctx=ctx)
        else:raise Unsupported('unsupported Boolean source node')
        booleans[identity]=value
    bound_value=lift(bound)>=0;atom_values['@bound']=bound_value
    def fact(ref):
        if ref['atom']=='@bound':poly=bound
        else:
            a=atom_records[ref['atom']];poly=_normalized(a['relation'],a['poly'])
        if not ref['polarity']:poly=_add(_scale(poly,-1),{():-1})
        guard=atom_values[ref['atom']]
        return guard if ref['polarity']else z3.Not(guard),poly
    assertions=[booleans[f.get_id()]for f in hard]+[bound_value]
    for entry in cuts:
        remaining()
        if entry['kind']=='equality_times_variable':
            a=atom_records[entry['atom']];consequence=lift(_multiply(a['poly'],{(entry['variable'],):1},remaining))==0
            assertions.append(z3.Implies(atom_values[entry['atom']],consequence))
        else:
            left,h=fact(entry['left']);right,k=fact(entry['right'])
            assertions.append(z3.Implies(z3.And(left,right),lift(_multiply(h,k,remaining))>=0))
    query=z3.SolverFor('QF_LRA',ctx=ctx);query.add(*assertions)
    return query,monomials,len(hard)+1

def _lift_monomial_query(z3,ctx,order,hard,objective,polys,atoms,bound,inventory,cuts,prefix,remaining):
    """v2-only routing: legacy builder receives only its unchanged prefix."""
    legacy=[];extra=[]
    for entry in cuts:
        remaining()
        if entry['kind']=='equality_times_monomial':extra.append(entry)
        elif entry['kind']in('equality_times_variable','nonnegative_product'):
            if extra:raise Unsupported('legacy cut after quadratic append phase')
            legacy.append(entry)
        else:raise Unsupported('unknown guarded-product cut kind')
    query,monomials,base_count=_lift_query(z3,ctx,order,hard,objective,polys,atoms,bound,inventory,legacy,prefix,remaining)
    symbols={tuple(m['variables']):z3.Real(m['name'],ctx=ctx)for m in monomials};records={a['atom']:a for a in atoms}
    names={str(node.decl().name())for node in order if node.sort().kind()==z3.Z3_INT_SORT and node.decl().kind()==z3.Z3_OP_UNINTERPRETED}
    def lift(poly):
        terms=[]
        for monomial,coefficient in sorted(poly.items()):
            remaining();terms.append(z3.RealVal(str(coefficient),ctx=ctx)*(symbols[monomial]if monomial else z3.RealVal(1,ctx=ctx)))
        return z3.Sum(*terms)if terms else z3.RealVal(0,ctx=ctx)
    for entry in extra:
        remaining()
        if set(entry)!={'kind','atom','variables'}:raise Unsupported('quadratic moment fields mismatch')
        pair=entry['variables']
        if type(pair)is not list or len(pair)!=2 or any(type(name)is not str or name not in names for name in pair)or pair!=sorted(pair):raise Unsupported('quadratic multiplier must be two canonical original Int names')
        pair=tuple(pair)
        atom=records[entry['atom']]
        if atom['relation']!='='or _degree(atom['poly'])!=1:raise Unsupported('quadratic multiplier requires nonconstant original linear equality')
        product=_multiply(atom['poly'],{pair:1},remaining)
        if any(m and m not in inventory for m in product):raise Unsupported('quadratic product missing from inventory')
        query.add(z3.Implies(lift(atom['poly'])==0,lift(product)==0))
    return query,monomials,base_count

def _sdk_identity(z3,remaining):
    libraries=[]
    for line in Path('/proc/self/maps').read_text(errors='surrogateescape').splitlines():
        fields=line.split(None,5)
        if len(fields)!=6:continue
        path=re.sub(r'\\([0-7]{3})',lambda m:chr(int(m[1],8)),fields[5])
        if Path(path.removesuffix(' (deleted)')).name.startswith('libz3.so'):
            if path.endswith(' (deleted)'):raise ValueError('SDK mapping deleted')
            path=str(Path(path).resolve(strict=True))
            if path not in libraries:libraries.append(path)
    if not libraries:raise ValueError('no mapped SDK library')
    values=[]
    for path in sorted(libraries):remaining();values.append(dict(path=path,sha256=_digest(path)))
    return dict(version=z3.get_version_string(),python=str(Path(z3.__file__).resolve()),libraries=values)

def analyze(source_path,direction,best,seconds,output_dir,z3_module,strict_helper=None,*,closure_policy='all-variables',variable_limit=256):
    """One externally bounded diagnostic, not a source SAT/optimality API."""
    started=time.monotonic();deadline=None;owned=False;report_identity=None
    result=dict(status='unknown',answer=None,reason='',diagnostic_only=True,diagnostic_bound_unsat=False,is_optimal=False,
                source_model_verified=False,independent_certificate_verified=False,check_started=False,check_seconds=0.,generation_seconds=0.,setup_seconds=0.,query_path=None,query_sha256=None,certificate_path=None,certificate_sha256=None)
    try:
        if type(seconds)not in(int,float)or not math.isfinite(seconds)or not 0<seconds<=1200:raise ValueError('seconds must be finite in (0,1200]')
        closure_policy,variable_limit=_quadratic_profile(closure_policy,variable_limit)
        result.update(closure_policy=closure_policy,variable_limit=variable_limit,discovery_version='guarded-product-discovery-v5'if closure_policy=='indexed-quadratic-equality-moments'else'guarded-product-discovery-v4'if closure_policy=='indexed-linear-equality-moments'else'guarded-product-discovery-v3'if closure_policy=='indexed-shared-cubic'else'guarded-product-discovery-v2')
        if direction not in('min','max'):raise ValueError('direction must be min or max')
        if not isinstance(best,str)or not re.fullmatch(r'-?[0-9]+',best)or len(best)>2500:raise ValueError('own best must be an exact decimal integer string')
        value=int(best)
        if value.bit_length()>MAX_BITS:raise ValueError('own best integer bit cap')
        best=str(value);deadline=started+seconds;generation_deadline=started+seconds*.5
        def remaining():
            value=deadline-time.monotonic()
            if value<=0:raise TimeoutError('guarded-product deadline reached')
            return value
        remaining();source=Path(source_path).resolve(strict=True);output=Path(output_dir).absolute()
        if not source.is_file()or source.stat().st_size>MAX_SOURCE_BYTES:raise Unsupported('source file type/size cap')
        if output.exists()or output.is_symlink():raise ValueError('output directory must be new')
        raw=source.read_bytes();source_hash=hashlib.sha256(raw).hexdigest();text=raw.decode('utf-8');remaining()
        output.mkdir(parents=False,exist_ok=False);owned=True;output=output.resolve()
        result.update(source=str(source),source_sha256=source_hash,direction=direction,own_best=best,module_sha256=_digest(__file__))
        strict=_strict(strict_helper);filtered,actual_direction,_=strict._query(text)
        if actual_direction!=direction:raise ValueError('requested/source direction mismatch')
        _raw_validate(strict._commands(filtered),remaining)
        result['strict_helper_sha256']=_digest(strict.__file__)
        z3=z3_module;result['sdk']=_sdk_identity(z3,remaining);ctx=z3.Context();parser=z3.Optimize(ctx=ctx);parser.from_string(filtered) # parse only
        objectives=list(parser.objectives());hard=list(parser.assertions())
        if len(objectives)!=1 or not objectives[0].is_int():raise Unsupported('one original Int objective required')
        objective=objectives[0];order=_source_order(z3,hard+[objective],remaining);polys,variables,atoms=_source_polynomials(z3,order,remaining,variable_limit)
        names=sorted(variables);bound=_bound_poly(polys[objective.get_id()],direction,value)
        inventory={(name,)for name in names}
        for poly in list(polys.values())+[bound]:inventory.update(m for m in poly if m)
        if len(inventory)>MAX_MONOMIALS:raise Unsupported('base monomial cap; base cannot be truncated')
        began=time.monotonic();closure={'all-variables':_closure,'shared-cubic':_sparse_closure,'indexed-shared-cubic':_indexed_sparse_closure,'indexed-linear-equality-moments':_linear_moment_closure,'indexed-quadratic-equality-moments':_quadratic_moment_closure}[closure_policy]
        inventory,cuts,stats=closure(atoms,names,bound,inventory,remaining,lambda:time.monotonic()<generation_deadline)
        result.update(generation_seconds=time.monotonic()-began,generation_counts=stats,source_nodes=len(order),source_comparison_atoms=len(atoms))
        builder=_lift_monomial_query if closure_policy=='indexed-quadratic-equality-moments'else _lift_query
        query,mapping,base_count=builder(z3,ctx,order,hard,objective,polys,atoms,bound,inventory,cuts,_fresh_prefix(text),remaining)
        result.update(monomial_count=len(mapping),cut_count=len(cuts),base_assertion_count=base_count,objective=objective.sexpr())
        query_path=output/'relaxation.smt2';certificate_path=output/'certificate.json'
        query_text=query.to_smt2();query_text='(set-logic QF_LRA)\n'+'\n'.join(line for line in query_text.splitlines()if not line.startswith('(set-info :status'))+'\n'
        _publish(query_path,query_text,remaining);query_sha=_digest(query_path)
        certificate=dict(schema='source-guarded-product-bound-v2'if closure_policy=='indexed-quadratic-equality-moments'else'source-guarded-product-bound-v1',source=str(source),source_sha256=source_hash,direction=direction,own_best=best,original_objective=objective.sexpr(),used_integer_names=names,used_bool_names=[],monomials=mapping,cuts=cuts,query_path=str(query_path),query_sha256=query_sha,truncations=stats['truncations'])
        _publish(certificate_path,json.dumps(certificate,ensure_ascii=False,indent=2)+'\n',remaining);cert_sha=_digest(certificate_path)
        result.update(query_path=str(query_path),query_sha256=query_sha,certificate_path=str(certificate_path),certificate_sha256=cert_sha)
        _source_guard(source,source_hash,remaining)
        query.set(timeout=max(1,int(1000*remaining())));result.update(setup_seconds=time.monotonic()-started,check_started=True);began=time.monotonic()
        try:answer=query.check()
        finally:result['check_seconds']=time.monotonic()-began
        remaining();_artifact_guard(source,source_hash,query_path,query_sha,certificate_path,cert_sha,remaining)
        remaining();result['answer']=str(answer)
        if answer==z3.unsat:result.update(status='bound_unsat',diagnostic_bound_unsat=True,reason='diagnostic source bound UNSAT; independent certificate validation and incumbent validation are separate')
        elif answer==z3.sat:result.update(status='relaxation_sat',reason='only a Real monomial relaxation model; never an original integer model')
        else:result.update(status='unknown',reason=query.reason_unknown())
        remaining()
    except TimeoutError as error:result.update(status='timeout',answer=None,diagnostic_bound_unsat=False,reason=str(error))
    except Unsupported as error:result.update(status='unsupported',answer=None,diagnostic_bound_unsat=False,reason=str(error))
    except Exception as error:result.update(status='error',answer=None,diagnostic_bound_unsat=False,reason=f'{type(error).__name__}: {error}')
    finally:
        _deadline_gate(result,deadline);result['elapsed_seconds']=time.monotonic()-started
        if owned:
            report_path=output/'result.json'
            try:
                report_identity=_publish(report_path,json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n',lambda:None)
                before=result['status'];_deadline_gate(result,deadline)
                if before!=result['status']:
                    _remove_owned(report_path,report_identity);result['elapsed_seconds']=time.monotonic()-started
                    _publish(report_path,json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n',lambda:None)
            except OSError as error:
                result.update(status='error',answer=None,diagnostic_bound_unsat=False,report_write_error=str(error))
    return result
