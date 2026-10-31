"""Independent exact guarded-product translation checker; never solves.

The supplied Z3 module is only a source/query parser. Multivariate expansion,
integer comparison normalization, guarded consequences and Boolean matching
are implemented here without importing the candidate producer.
"""
from collections import Counter
from fractions import Fraction
import hashlib
import math
from pathlib import Path
import re
import time

FIELDS={'schema','source','source_sha256','direction','own_best','original_objective',
        'used_integer_names','used_bool_names','monomials','cuts','query_path','query_sha256','truncations'}
INTEGER=re.compile(r'-?(?:0|[1-9][0-9]*)\Z')
NUMBER=re.compile(r'-?[0-9]+(?:\.[0-9]+)?\Z')
TRIVIA=re.compile(r'(?:\s+|;[^\n]*(?:\n|$))*')

def require(condition,message):
    if not condition:raise ValueError(message)

class Budget:
    def __init__(self,deadline,max_nodes,max_bits):
        require(type(max_nodes)is int and 0<max_nodes<=4000000,'invalid work budget')
        require(type(max_bits)is int and 0<max_bits<=65536,'invalid bit budget')
        self.deadline=time.monotonic()+10 if deadline is None else float(deadline)
        require(math.isfinite(self.deadline),'invalid deadline')
        self.limit,self.bits,self.work=max_nodes,max_bits,0;self.step()
    def step(self,n=1):
        self.work+=n
        if time.monotonic()>=self.deadline:raise TimeoutError('guarded-product checker deadline reached')
        require(self.work<=self.limit,'guarded-product work resource limit')
    def value(self,value):
        self.step();v=Fraction(value)
        require(abs(v.numerator).bit_length()<=self.bits and v.denominator.bit_length()<=self.bits,'exact coefficient bit resource limit')
        return v
    def integer(self,text):
        require(type(text)is str and INTEGER.fullmatch(text),'expected exact decimal integer')
        require(len(text.lstrip('-'))<=self.bits*30103//100000+2,'integer bit resource limit')
        v=self.value(int(text));require(v.denominator==1,'noninteger source coefficient');return int(v)

def forms(text,budget):
    """Small self-contained SMT-LIB reader, preserving quoted symbols/strings."""
    root,stack,i=[],[],0
    while i<len(text):
        budget.step();i=TRIVIA.match(text,i).end()
        if i==len(text):break
        c=text[i]
        if c=='(':
            node=[];(stack[-1]if stack else root).append(node);stack.append(node)
            require(len(stack)<=8192,'SMT nesting resource limit');i+=1;continue
        if c==')':require(stack,'unmatched SMT parenthesis');stack.pop();i+=1;continue
        start=i
        if c=='|':
            i=text.find('|',i+1);require(i>=0,'unterminated quoted symbol');i+=1
        elif c=='"':
            i+=1
            while i<len(text):
                if text[i]=='"':
                    i+=1
                    if i<len(text)and text[i]=='"':i+=1;continue
                    break
                i+=1
            else:raise ValueError('unterminated SMT string')
        else:
            while i<len(text)and not text[i].isspace()and text[i]not in'();':i+=1
        (stack[-1]if stack else root).append(text[start:i])
    require(not stack,'unclosed SMT parenthesis');return root

def name(token):
    require(type(token)is str and token and not token.startswith('"'),'invalid SMT symbol')
    result=token[1:-1]if token.startswith('|')and token.endswith('|')else token
    require(result and '\\'not in result and not NUMBER.fullmatch(result),'unsupported SMT symbol');return result

def render(node):
    parts=[];pending=[node]
    while pending:
        value=pending.pop()
        if isinstance(value,str):parts.append(value);continue
        require(type(value)is list,'invalid SMT form');parts.append('(');pending.append(')')
        for i in range(len(value)-1,-1,-1):
            pending.append(value[i])
            if i:pending.append(' ')
    return''.join(parts)

def source_text(text,budget):
    symbols=set();kept=[];direction=None;checked=False
    def expr(node,depth=0):
        budget.step();require(depth<=256,'source expression depth limit')
        if isinstance(node,str):
            if node in('true','false'):return'Bool'
            if INTEGER.fullmatch(node):budget.integer(node);return'Int'
            require(name(node)in symbols,'unknown source symbol');return'Int'
        require(type(node)is list and node and type(node[0])is str,'invalid source expression')
        op,args=node[0],node[1:]
        if not args and name(op)in symbols:return'Int'
        require(op in('+','-','*','=','<=','<','>=','>','and','or','not'),'unsupported raw source operator: '+op)
        sorts=[expr(v,depth+1)for v in args]
        if op in('+','-','*'):require(sorts and all(s=='Int'for s in sorts),'source arithmetic sort/arity');return'Int'
        if op in('=','<=','<','>=','>'):require(len(sorts)>=2 and all(s=='Int'for s in sorts),'source comparison sort/arity');return'Bool'
        require(all(s=='Bool'for s in sorts)and(op!='not'or len(sorts)==1),'source Boolean sort/arity');return'Bool'
    for f in forms(text,budget):
        budget.step();require(type(f)is list and f and type(f[0])is str,'invalid source command');op=f[0]
        if op=='exit':require(len(f)==1,'invalid exit');break
        if op in('set-info','set-option','echo')or op.startswith('get-'):continue
        require(not checked,'source mutation after check-sat')
        if op=='check-sat':require(len(f)==1,'unsupported check command');checked=True;continue
        if op=='set-logic':require(len(f)==2,'invalid source logic')
        elif op in('declare-const','declare-fun','define-fun'):
            if op=='declare-const':require(len(f)==3 and f[2]=='Int','only Int source constants supported')
            elif op=='declare-fun':require(len(f)==4 and f[2]==[]and f[3]=='Int','source Bool/Real/UF unsupported')
            else:require(len(f)==5 and f[2]==[]and f[3]=='Int'and expr(f[4])=='Int','unsupported source definition')
            symbol=name(f[1]);require(symbol not in symbols,'duplicate source symbol');symbols.add(symbol)
        elif op=='assert':require(len(f)==2 and expr(f[1])=='Bool','invalid source assertion')
        elif op in('minimize','maximize'):
            require(direction is None and len(f)==2 and expr(f[1])=='Int','expected one Int objective')
            direction='min'if op=='minimize'else'max';f=['minimize',f[1]]
        else:raise ValueError('unsupported raw source command: '+op)
        kept.append(f)
    require(direction is not None,'missing original objective');return'\n'.join(render(f)for f in kept),direction,symbols

def query_text(text,mapping,budget):
    declarations=set();kept=[];checked=False
    def expr(node):
        # Descriptors carry (sort, degree, expanded mathematical AST depth).
        # A let changes scope, not mathematical depth, and never expands text.
        local={};values=[];pending=[('visit',node)];missing=object()
        while pending:
            action=pending.pop();kind=action[0];budget.step(1 if kind=='visit'else 0)
            if kind=='restore':
                for symbol,old in action[1]:
                    budget.step(0)
                    if old is missing:local.pop(symbol,None)
                    else:local[symbol]=old
                continue
            if kind=='bind':
                _,symbols,body,start=action;bindings=values[start:];del values[start:]
                require(len(bindings)==len(symbols),'invalid query let values');saved=[(s,local.get(s,missing))for s in symbols]
                local.update(zip(symbols,bindings));pending.append(('restore',saved));pending.append(('visit',body));continue
            if kind=='combine':
                _,op,start,count=action;children=values[start:];del values[start:];require(len(children)==count,'invalid query expression values')
                sorts=[v[0]for v in children];depth=1+max((v[2]for v in children),default=-1);require(depth<=256,'query mathematical AST depth limit')
                if op in('+','-','*','/'):
                    require(children and all(s=='Real'for s in sorts),'non-Real query arithmetic')
                    if op=='/':require(len(children)==2 and children[1][1]==0,'query division is not linear/QF_LRA');degree=children[0][1]
                    else:degree=sum(v[1]for v in children)if op=='*'else max(v[1]for v in children)
                    require(degree<=1,'actual query arithmetic is not linear/QF_LRA');values.append(('Real',degree,depth));continue
                if op in('=','<=','<','>=','>'):
                    require(len(children)==2 and sorts==['Real','Real'],'non-Real query comparison');values.append(('Bool',None,depth));continue
                require(all(s=='Bool'for s in sorts)and(op!='not'or len(children)==1)and(op!='=>'or len(children)==2),'unsupported query Boolean sort/arity');values.append(('Bool',None,depth));continue
            current=action[1]
            if isinstance(current,str):
                if current in('true','false'):values.append(('Bool',None,0));continue
                if NUMBER.fullmatch(current):budget.value(Fraction(current));values.append(('Real',0,0));continue
                symbol=name(current)
                if symbol in local:values.append(local[symbol]);continue
                require(symbol in declarations,'query contains foreign symbol');values.append(('Real',1,0));continue
            require(type(current)is list and current and type(current[0])is str,'invalid query expression');op,args=current[0],current[1:];start=len(values)
            if op=='let':
                require(len(args)==2 and type(args[0])is list,'invalid query let');symbols=[];seen=set();rhs=[]
                for binding in args[0]:
                    budget.step(0);require(type(binding)is list and len(binding)==2,'invalid query let binding');symbol=name(binding[0]);require(symbol not in seen,'duplicate query let symbol');seen.add(symbol);symbols.append(symbol);rhs.append(binding[1])
                # Evaluate all RHSs in the outer scope before applying bindings.
                pending.append(('bind',symbols,args[1],start));pending.extend(('visit',v)for v in reversed(rhs));continue
            require(op in('+','-','*','/','=','<=','<','>=','>','and','or','not','=>'),'unsupported raw query operator: '+op)
            pending.append(('combine',op,start,len(args)));pending.extend(('visit',v)for v in reversed(args))
        require(len(values)==1,'invalid query expression result');return values[0]
    for f in forms(text,budget):
        budget.step();require(type(f)is list and f and type(f[0])is str,'invalid query command');op=f[0]
        if op=='exit':require(len(f)==1,'invalid query exit');break
        if op=='set-info'or op.startswith('get-'):continue
        require(not checked,'query mutation after check-sat')
        if op=='check-sat':require(len(f)==1,'unsupported query check');checked=True;continue
        if op=='set-logic':require(f==['set-logic','QF_LRA'],'actual query logic must be QF_LRA')
        elif op in('declare-const','declare-fun'):
            require((len(f)==3 and op=='declare-const'and f[2]=='Real')or(len(f)==4 and op=='declare-fun'and f[2]==[]and f[3]=='Real'),'actual query declarations must be fresh Real constants')
            symbol=name(f[1]);require(symbol in mapping and symbol not in declarations,'foreign/duplicate query declaration');declarations.add(symbol)
        elif op=='assert':require(len(f)==2 and expr(f[1])[0]=='Bool','invalid query assertion')
        else:raise ValueError('unsupported actual query command: '+op)
        kept.append(f)
    return'\n'.join(render(f)for f in kept),declarations

class Polynomials:
    def __init__(self,budget):self.budget=budget
    def add(self,a,b,scale=1):
        result=dict(a)
        for factors,value in b.items():
            self.budget.step();v=self.budget.value(result.get(factors,0)+scale*value)
            if v:result[factors]=v
            else:result.pop(factors,None)
        require(len(result)<=20000,'monomial resource limit');return result
    def scale(self,a,value):return{f:self.budget.value(c*value)for f,c in a.items()if c*value}
    def multiply(self,a,b,degree,reason):
        result={}
        for left,x in a.items():
            for right,y in b.items():
                self.budget.step();f=tuple(sorted(left+right));require(len(f)<=degree,reason)
                c=self.budget.value(result.get(f,0)+x*y)
                if c:result[f]=c
                else:result.pop(f,None)
                require(len(result)<=20000,'monomial resource limit')
        return result
    def degree(self,a):return max((len(f)for f in a),default=0)
    def primitive(self,a,equality=False):
        if not a:return()
        denominator=1
        for c in a.values():denominator=int(self.budget.value(denominator*c.denominator//math.gcd(denominator,c.denominator)))
        terms=[(f,int(self.budget.value(c.numerator*(denominator//c.denominator))))for f,c in sorted(a.items())if c]
        common=0
        for f,c in terms:common=math.gcd(common,abs(c))
        if equality and terms[0][1]<0:common=-common
        return tuple((f,c//common)for f,c in terms)

def combine(kind,children):
    flat=[]
    for child in children:
        if child[0]==kind:flat.extend(child[1])
        else:flat.append(child)
    absorbing=('bool',kind=='or');neutral=('bool',kind=='and')
    if absorbing in flat:return absorbing
    values=tuple(sorted(set(v for v in flat if v!=neutral),key=repr))
    if not values:return neutral
    return values[0]if len(values)==1 else(kind,values)

def negate(value):
    if value[0]=='bool':return'bool',not value[1]
    if value[0]=='not':return value[1]
    if value[0]in('and','or'):return combine('or'if value[0]=='and'else'and',[negate(v)for v in value[1]])
    return'not',value

class Reconstruction:
    def __init__(self,z3,budget):self.z3=z3;self.budget=budget;self.poly=Polynomials(budget);self.source_cache={};self.source_bool_cache={};self.atoms={};self.used=set();self.linear_cache={};self.query_bool_cache={};self.mapping={};self.declared_query=set()
    def number(self,node):
        z=self.z3
        if z.is_int_value(node):return self.budget.value(node.as_long())
        require(z.is_rational_value(node),'unsupported algebraic/nonrational numeral');return self.budget.value(Fraction(node.numerator_as_long(),node.denominator_as_long()))
    def source_polynomial(self,node,depth=0):
        self.budget.step();require(depth<=256,'source AST depth resource limit');identity=node.get_id()
        if identity in self.source_cache:return self.source_cache[identity]
        z=self.z3;require(z.is_app(node)and z.is_int(node),'source arithmetic must be Int polynomial');kind=node.decl().kind()
        if z.is_int_value(node):v=self.number(node);result={():v}if v else{}
        elif kind==z.Z3_OP_UNINTERPRETED and node.num_args()==0:
            symbol=str(node.decl().name());self.used.add(symbol);result={(symbol,):Fraction(1)}
        else:
            require(kind in(z.Z3_OP_ADD,z.Z3_OP_SUB,z.Z3_OP_UMINUS,z.Z3_OP_MUL),'unsupported source arithmetic AST');parts=[self.source_polynomial(c,depth+1)for c in node.children()];require(parts,'source arithmetic arity')
            if kind==z.Z3_OP_MUL:
                result={():Fraction(1)}
                for p in parts:result=self.poly.multiply(result,p,2,'original source degree exceeds2')
            elif kind==z.Z3_OP_ADD:
                result={}
                for p in parts:result=self.poly.add(result,p)
            elif kind==z.Z3_OP_UMINUS:require(len(parts)==1,'source unary minus arity');result=self.poly.scale(parts[0],-1)
            else:
                result=parts[0]
                for p in parts[1:]:result=self.poly.add(result,p,-1)
        self.source_cache[identity]=result;return result
    def atom(self,kind,p):
        if all(not factors for factors in p):
            value=p.get((),Fraction(0));return'bool',value==0 if kind=='eq'else value>=0
        return kind,self.poly.primitive(p,kind=='eq')
    def source_boolean(self,node,depth=0):
        self.budget.step();require(depth<=256,'source Boolean depth limit');identity=node.get_id()
        if identity in self.source_bool_cache:return self.source_bool_cache[identity]
        z=self.z3;require(z.is_app(node)and z.is_bool(node),'unsupported source Boolean AST');kind=node.decl().kind()
        if kind in(z.Z3_OP_TRUE,z.Z3_OP_FALSE):result='bool',kind==z.Z3_OP_TRUE
        elif kind in(z.Z3_OP_AND,z.Z3_OP_OR):result=combine('and'if kind==z.Z3_OP_AND else'or',[self.source_boolean(c,depth+1)for c in node.children()])
        elif kind==z.Z3_OP_NOT:require(node.num_args()==1,'source not arity');result=negate(self.source_boolean(node.arg(0),depth+1))
        else:
            require(kind in(z.Z3_OP_EQ,z.Z3_OP_LE,z.Z3_OP_LT,z.Z3_OP_GE,z.Z3_OP_GT)and node.num_args()==2,'unsupported source comparison')
            left=self.source_polynomial(node.arg(0),depth+1);right=self.source_polynomial(node.arg(1),depth+1)
            if kind==z.Z3_OP_EQ:p=self.poly.add(left,right,-1);relation='eq'
            else:
                p=self.poly.add(right,left,-1)if kind in(z.Z3_OP_LE,z.Z3_OP_LT)else self.poly.add(left,right,-1)
                if kind in(z.Z3_OP_LT,z.Z3_OP_GT):p=self.poly.add(p,{():Fraction(-1)})
                relation='ge'
            result=self.atom(relation,p);self.atoms[node.sexpr()]=(relation,p,result)
        self.source_bool_cache[identity]=result;return result
    def linear(self,node,depth=0):
        self.budget.step();require(depth<=256,'query AST depth limit');identity=node.get_id()
        if identity in self.linear_cache:return self.linear_cache[identity]
        z=self.z3;require(z.is_app(node),'nonlinear/unsupported query AST');kind=node.decl().kind()
        if z.is_int_value(node)or z.is_rational_value(node):v=self.number(node);result={():v}if v else{}
        elif kind==z.Z3_OP_UNINTERPRETED and node.num_args()==0:
            symbol=str(node.decl().name());require(z.is_real(node)and symbol in self.mapping and symbol in self.declared_query,'query has foreign/non-Real variable');result={(symbol,):Fraction(1)}
        else:
            require(z.is_real(node)and kind in(z.Z3_OP_ADD,z.Z3_OP_SUB,z.Z3_OP_UMINUS,z.Z3_OP_MUL,z.Z3_OP_DIV),'actual query must be linear/QF_LRA')
            parts=[self.linear(c,depth+1)for c in node.children()];require(parts,'query arithmetic arity')
            if kind==z.Z3_OP_MUL:
                result={():Fraction(1)}
                for p in parts:result=self.poly.multiply(result,p,1,'actual query must be linear/QF_LRA')
            elif kind==z.Z3_OP_DIV:
                require(len(parts)==2 and all(not f for f in parts[1])and parts[1].get((),0)!=0,'query division is not linear/QF_LRA');result=self.poly.scale(parts[0],1/parts[1][()])
            elif kind==z.Z3_OP_ADD:
                result={}
                for p in parts:result=self.poly.add(result,p)
            elif kind==z.Z3_OP_UMINUS:require(len(parts)==1,'query unary minus arity');result=self.poly.scale(parts[0],-1)
            else:
                result=parts[0]
                for p in parts[1:]:result=self.poly.add(result,p,-1)
        self.linear_cache[identity]=result;return result
    def expand(self,p):
        result={}
        for f,c in p.items():
            self.budget.step();require(len(f)<=1,'actual query is not linear/QF_LRA');original=()if not f else self.mapping[f[0]];result=self.poly.add(result,{original:c})
        return result
    def query_boolean(self,node,depth=0):
        self.budget.step();require(depth<=256,'query Boolean depth limit');identity=node.get_id()
        # One parsed query/context and fixed monomial mapping per instance;
        # its retained root assertions keep shared AST identities alive.
        if identity in self.query_bool_cache:return self.query_bool_cache[identity]
        result=self._query_boolean(node,depth);self.query_bool_cache[identity]=result;return result
    def _query_boolean(self,node,depth):
        z=self.z3;require(z.is_app(node)and z.is_bool(node),'unsupported query Boolean AST');kind=node.decl().kind()
        if kind in(z.Z3_OP_TRUE,z.Z3_OP_FALSE):return'bool',kind==z.Z3_OP_TRUE
        if kind in(z.Z3_OP_AND,z.Z3_OP_OR):return combine('and'if kind==z.Z3_OP_AND else'or',[self.query_boolean(c,depth+1)for c in node.children()])
        if kind==z.Z3_OP_NOT:require(node.num_args()==1,'query not arity');return negate(self.query_boolean(node.arg(0),depth+1))
        if kind==z.Z3_OP_IMPLIES:require(node.num_args()==2,'query implication arity');return combine('or',[negate(self.query_boolean(node.arg(0),depth+1)),self.query_boolean(node.arg(1),depth+1)])
        require(kind in(z.Z3_OP_EQ,z.Z3_OP_LE,z.Z3_OP_LT,z.Z3_OP_GE,z.Z3_OP_GT)and node.num_args()==2,'unsupported query atom')
        left=self.expand(self.linear(node.arg(0),depth+1));right=self.expand(self.linear(node.arg(1),depth+1))
        if kind==z.Z3_OP_EQ:return self.atom('eq',self.poly.add(left,right,-1))
        p=self.poly.add(right,left,-1)if kind in(z.Z3_OP_LE,z.Z3_OP_LT)else self.poly.add(left,right,-1)
        # Real strict comparison is NOT(-p>=0), with no integer -1 shift.
        return negate(self.atom('ge',self.poly.scale(p,-1)))if kind in(z.Z3_OP_LT,z.Z3_OP_GT)else self.atom('ge',p)

def validate_certificate(z3,certificate,deadline=None,expected_source=None,expected_query=None,expected_best=None,*,max_nodes=4000000,max_bits=8192,max_variables=8192):
    """Validate the exact source-bound lifting and actual query, never UNSAT."""
    b=Budget(deadline,max_nodes,max_bits);require(type(max_variables)is int and 0<max_variables<=8192,'invalid variable resource limit');require(type(certificate)is dict and set(certificate)==FIELDS,'invalid certificate fields');c=certificate
    require(c['schema']in('source-guarded-product-bound-v1','source-guarded-product-bound-v2'),'unsupported certificate schema')
    for key in('source','query_path'):require(type(c[key])is str and Path(c[key]).is_absolute(),'source/query path must be absolute')
    source,query=Path(c['source']),Path(c['query_path']);require(source.is_file()and query.is_file()and source.resolve()!=query.resolve(),'invalid source/query files')
    if expected_source is not None:require(source.resolve()==Path(expected_source).resolve(),'source path identity mismatch')
    if expected_query is not None:require(query.resolve()==Path(expected_query).resolve(),'query path identity mismatch')
    for key in('source_sha256','query_sha256'):require(type(c[key])is str and re.fullmatch('[0-9a-f]{64}',c[key]),'invalid input hash')
    raw_source,raw_query=source.read_bytes(),query.read_bytes();require(len(raw_source)<=16*1024*1024 and len(raw_query)<=64*1024*1024,'source/query size resource limit')
    require(hashlib.sha256(raw_source).hexdigest()==c['source_sha256']and hashlib.sha256(raw_query).hexdigest()==c['query_sha256'],'source/query hash mismatch')
    best=b.integer(c['own_best'])
    if expected_best is not None:require(c['own_best']==str(expected_best),'own-best external identity mismatch')
    require(type(c['truncations'])is list and all(type(v)is str for v in c['truncations']),'truncations must be diagnostic strings')
    filtered,direction,source_symbols=source_text(raw_source.decode('utf-8'),b);require(c['direction']==direction,'original direction mismatch')
    context=z3.Context();parser=z3.Optimize(ctx=context)
    try:parser.from_string(filtered)
    except z3.Z3Exception as error:raise ValueError('original source parse failed')from error
    b.step();objectives=list(parser.objectives());require(len(objectives)==1,'expected one objective');objective=objectives[0];require(type(c['original_objective'])is str and objective.sexpr()==c['original_objective'],'original objective AST mismatch')
    r=Reconstruction(z3,b);hard=list(parser.assertions());expected=[r.source_boolean(f)for f in hard];goal=r.source_polynomial(objective);used=sorted(r.used)
    require(len(used)<=max_variables,'source variable resource limit');require(type(c['used_integer_names'])is list and c['used_integer_names']==used,'complete sorted source-used Int set mismatch');require(type(c['used_bool_names'])is list and c['used_bool_names']==[],'source Bool variables unsupported')
    mapping={};tuples=set();require(type(c['monomials'])is list and len(c['monomials'])<=20000,'monomial mapping resource limit')
    reserved={'true','false','Int','Real','and','or','not','=>','=','<','<=','>','>=','+','-','*','/','let'}
    for entry in c['monomials']:
        b.step();require(type(entry)is dict and set(entry)=={'name','variables'},'invalid monomial entry');symbol=entry['name'];variables=entry['variables']
        require(type(symbol)is str and symbol and '\x00'not in symbol and symbol not in source_symbols and symbol not in reserved and symbol not in mapping,'monomial name is not fresh/unique')
        require(type(variables)is list and 1<=len(variables)<=3 and all(type(v)is str and v in used for v in variables)and variables==sorted(variables),'invalid canonical monomial variables')
        monomial=tuple(variables);require(monomial not in tuples,'duplicate original monomial definition');tuples.add(monomial);mapping[symbol]=monomial
    require(all((v,)in tuples for v in used),'missing source-used degree1 monomial');r.mapping=mapping
    bound=r.poly.add({():Fraction(best-1)},r.poly.scale(goal,-1))if direction=='min'else r.poly.add(goal,{():Fraction(-best-1)})
    bound_guard=r.atom('ge',bound);expected.append(bound_guard)
    def covered(poly):require(all(not f or f in tuples for f in poly),'missing needed source/cut monomial')
    for p in list(r.source_cache.values())+[bound]:covered(p)
    def fact(ref):
        require(type(ref)is dict and set(ref)=={'atom','polarity'}and type(ref['atom'])is str and type(ref['polarity'])is bool,'invalid guarded nonnegative fact')
        if ref['atom']=='@bound':p,guard=bound,bound_guard
        else:
            require(ref['atom']in r.atoms and r.atoms[ref['atom']][0]=='ge','fact must reference an original inequality atom');_,p,guard=r.atoms[ref['atom']]
        if ref['polarity']:return p,guard
        return r.poly.add(r.poly.scale(p,-1),{():Fraction(-1)}),negate(guard)
    require(type(c['cuts'])is list and len(c['cuts'])<=12000,'cut ledger resource limit')
    for cut in c['cuts']:
        b.step();require(type(cut)is dict and type(cut.get('kind'))is str,'invalid cut ledger entry')
        if cut['kind']=='equality_times_variable':
            require(set(cut)=={'kind','atom','variable'}and cut['atom']in r.atoms and r.atoms[cut['atom']][0]=='eq'and cut['variable']in used,'invalid equality-times-variable rule')
            _,equation,guard=r.atoms[cut['atom']];product=r.poly.multiply(equation,{(cut['variable'],):Fraction(1)},3,'cut degree exceeds3');target=r.atom('eq',product)
        elif cut['kind']=='equality_times_monomial':
            require(c['schema']=='source-guarded-product-bound-v2','new monomial rule requires v2 schema')
            require(set(cut)=={'kind','atom','variables'}and type(cut['atom'])is str and cut['atom']in r.atoms and r.atoms[cut['atom']][0]=='eq','invalid equality-times-monomial fields/atom')
            variables=cut['variables'];require(type(variables)is list and len(variables)==2 and all(type(v)is str and v in used for v in variables)and variables==sorted(variables),'invalid canonical quadratic multiplier variables')
            multiplier=tuple(variables)
            _,equation,guard=r.atoms[cut['atom']];require(r.poly.degree(equation)==1,'monomial rule requires nonconstant linear source equality')
            product=r.poly.multiply(equation,{multiplier:Fraction(1)},3,'cut degree exceeds3');target=r.atom('eq',product)
        elif cut['kind']=='nonnegative_product':
            require(set(cut)=={'kind','left','right'},'invalid nonnegative-product fields');left,lg=fact(cut['left']);right,rg=fact(cut['right']);require(min(r.poly.degree(left),r.poly.degree(right))<=1,'at least one product factor must be linear');product=r.poly.multiply(left,right,3,'product degree exceeds3');guard=combine('and',[lg,rg]);target=r.atom('ge',product)
        else:raise ValueError('unsupported cut ledger kind')
        covered(product);expected.append(combine('or',[negate(guard),target]))
    filtered_query,declarations=query_text(raw_query.decode('utf-8'),mapping,b);r.declared_query=declarations
    try:actual=list(z3.parse_smt2_string(filtered_query,ctx=context))
    except z3.Z3Exception as error:raise ValueError('actual query parse failed')from error
    # A printer may omit standalone true assertions. Remove only formulae
    # independently proved constant true, on both sides of the conjunction;
    # preserve false and every non-neutral base/bound/ledger assertion.
    keys=[r.query_boolean(f)for f in actual];neutral=('bool',True)
    require(Counter(k for k in keys if k!=neutral)==Counter(k for k in expected if k!=neutral),'actual query does not contain exactly complete base/strict-bound and cut ledger')
    require(hashlib.sha256(source.read_bytes()).hexdigest()==c['source_sha256']and hashlib.sha256(query.read_bytes()).hexdigest()==c['query_sha256'],'source/query changed during verification');b.step()
    result=dict(schema='guarded-product-translation-verification-v1',translation_verified=True,certificate_valid=True,solver_calls=0,source=str(source),source_sha256=c['source_sha256'],direction=direction,own_best=c['own_best'],original_objective=c['original_objective'],query_path=str(query),query_sha256=c['query_sha256'],formal_A2=False,is_optimal=False,source_bound_verified=False,UNSAT_replayed=False,used_integer_count=len(used),monomial_count=len(mapping),cut_count=len(c['cuts']),query_assertion_count=len(actual),work=b.work)
    b.step();return result
