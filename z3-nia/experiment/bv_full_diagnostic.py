"""One long-budget call to the unchanged qfnia extended-BV tactic pipeline.

The whole tactic2solver retains built-in UNDER and model converters. Source SAT
models are durable diagnostic witnesses; a source range flag is not a verified
native model bridge or an optimality claim.
"""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import time

_bv_path=Path(__file__).with_name('bv_budget_diagnostic.py')
_spec=importlib.util.spec_from_file_location('full_bv_budget',_bv_path)
bv=importlib.util.module_from_spec(_spec);sys.modules[_spec.name]=bv;_spec.loader.exec_module(bv)
runner=bv.runner
Config=runner.Config
ARMS=['bv_extended_full']

def classify_int128(assignments,sorts,objective_value):
    source_safe=all(-(1<<127)<=int(value)<(1<<127) for name,value in assignments.items() if sorts[name]=='Int')
    objective_safe=-(1<<127)<=objective_value<(1<<127)
    return dict(source_int128_compatible=source_safe,objective_int128_compatible=objective_safe,
                native_int128_compatible=source_safe and objective_safe)

class SourceModelProxy:
    """Delegate the original solver while validating/persisting original values."""
    def __init__(self,z3,solver,policy,payload,receipt_path):
        self.z3,self.solver,self.policy=z3,solver,policy
        self.payload,self.receipt=payload,Path(receipt_path)
        self.record,self.deadline=payload['record'],payload['deadline']
        self.first=runner._first(payload['paths']['first_helper'])
        self.assertions,self.answer_model=[],None
        self.remaining()
        source=self.first._file(self.record['source'],self.record['source_sha256'],content=True).decode('utf-8')
        strict=self.first._helper(payload['paths']['strict_helper'])
        filtered,direction,self.objective_text=strict._query(source)
        runner._require(direction==self.record['direction'],'source objective direction changed')
        self.best=self.first._exact(self.record['M'])
        parsed=z3.Optimize();parsed.from_string(filtered)
        objectives=parsed.objectives()
        runner._require(len(objectives)==1 and objectives[0].sort().kind()==z3.Z3_INT_SORT,'expected one original Int objective')
        self.objective,self.hard=objectives[0],list(parsed.assertions())
        self.policy.update(model_path=None,model_sha256=None,source_model_validation='full original F and own strict bound',
                           native_bridge_verified=False)
        self.remaining()

    def remaining(self):
        value=self.deadline-time.monotonic()
        if value<=0:raise TimeoutError('full BV arm deadline reached')
        return value

    def add(self,*assertions):
        self.assertions.extend(assertions)
        return self.solver.add(*assertions)
    def to_smt2(self):return self.solver.to_smt2()
    def set(self,**parameters):return self.solver.set(**parameters)
    def model(self):return self.answer_model if self.answer_model is not None else self.solver.model()
    def reason_unknown(self):return self.solver.reason_unknown()

    def check(self):
        self.remaining()
        answer=self.solver.check()
        self.remaining()
        if answer!=self.z3.sat:return answer
        z3=self.z3;model=self.solver.model()
        for formula in self.hard+self.assertions:
            self.remaining()
            runner._require(z3.is_true(model.eval(formula,model_completion=True)),'BV model fails complete original assertions')
        objective_value=model.eval(self.objective,model_completion=True)
        runner._require(z3.is_int_value(objective_value),'original objective has no exact integer value')
        value=objective_value.as_long()
        improved=value<self.best if self.record['direction']=='min' else value>self.best
        runner._require(improved,'BV source model does not improve the own incumbent')
        order=runner._supported_dag(z3,self.hard+[self.objective],self.remaining)
        assignments,sorts={},{}
        for constant in order:
            self.remaining()
            if constant.decl().kind()!=z3.Z3_OP_UNINTERPRETED:continue
            name=str(constant.decl().name());datum=model.eval(constant,model_completion=True)
            runner._require(name not in assignments,'overloaded source constant name in model receipt')
            if constant.sort().kind()==z3.Z3_INT_SORT:
                runner._require(z3.is_int_value(datum),'noninteger original model constant')
                exact=datum.as_long();assignments[name],sorts[name]=str(exact),'Int'
            else:
                runner._require(z3.is_true(datum) or z3.is_false(datum),'nonboolean original model constant')
                assignments[name],sorts[name]=z3.is_true(datum),'Bool'
        self.first._file(self.record['source'],self.record['source_sha256'])
        range_flags=classify_int128(assignments,sorts,value)
        query_path=self.receipt.with_suffix('.smt2')
        saved=dict(version='bv-full-source-model-v1',source_path=self.record['source'],source_sha256=self.record['source_sha256'],
                   direction=self.record['direction'],M=self.record['M'],original_objective=self.objective_text,
                   objective_value=objective_value.sexpr(),objective_value_exact=str(value),assignments=assignments,sorts=sorts,
                   query_path=str(query_path.resolve()),query_sha256=self.first._file(query_path),
                   **range_flags,native_int128_scope='original source Int constants and original objective only; not a native bridge result',
                   native_bridge_verified=False,diagnostic_only=True,is_optimal=False)
        self.remaining()
        path=self.receipt.with_suffix('.model.json');data=json.dumps(saved,indent=2,ensure_ascii=False,allow_nan=False)+'\n'
        with path.open('x') as stream:stream.write(data)
        self.policy.update(model_path=str(path.resolve()),model_sha256=hashlib.sha256(data.encode()).hexdigest(),
                           **range_flags)
        self.first._file(self.record['source'],self.record['source_sha256'])
        self.remaining()
        self.answer_model=model
        return answer

def run_case(record,config,case_dir,stop):
    dependencies={str(p.resolve()):hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in (_bv_path,Path(runner.__file__))}
    return runner.run_case(dict(record,bv_full_dependencies=dependencies),config,case_dir,stop,
        arms=ARMS,worker_path=Path(__file__),max_arm_seconds=min(1140,float(config.timeout)),
        run_metadata=dict(version='bv-full-diagnostic-v1',tactic='qfnia-bv',max_arm_seconds=min(1140,float(config.timeout)),
                          nominal_full_arm_seconds=1140,dependencies=dependencies))

if __name__=='__main__':
    if len(sys.argv)!=4 or sys.argv[1]!='--arm':raise SystemExit('only private --arm PAYLOAD RECEIPT is supported')
    payload=json.loads(Path(sys.argv[2]).read_text())
    def factory(z3,arm):
        runner._require(arm=='bv_extended_full','unexpected full BV arm')
        first=runner._first(payload['paths']['first_helper'])
        expected={str(_bv_path.resolve()),str(Path(runner.__file__).resolve())}
        dependencies=payload['record']['bv_full_dependencies']
        runner._require(set(dependencies)==expected,'BV full dependency path set mismatch')
        for path,sha in dependencies.items():first._file(path,sha)
        solver,policy=bv.solver_factory(z3,'bv_extended')
        policy.update(full_arm=True,dependencies=dependencies)
        proxy=SourceModelProxy(z3,solver,policy,payload,sys.argv[3])
        return proxy,policy
    runner._run_arm(sys.argv[2],sys.argv[3],solver_factory=factory)
