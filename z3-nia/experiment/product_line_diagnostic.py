"""Lazy valid product lines, reusing the complete-source diagnostic core."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

_path = Path(__file__).with_name('product_graph_diagnostic.py')
_spec = importlib.util.spec_from_file_location('line_product_core', _path)
core = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = core
_spec.loader.exec_module(core)
Config = core.Config
ARMS = ['line_plain','line_seeded']

def arm_order(index):
    return core.runner.arm_order(index,ARMS)

def run_case(record,config,case_dir,stop):
    core_sha256=hashlib.sha256(_path.read_bytes()).hexdigest()
    return core.runner.run_case(dict(record,product_line_core_sha256=core_sha256),config,case_dir,stop,
        arms=ARMS,worker_path=Path(__file__),run_metadata=dict(version='product-line-diagnostic-v1',
        tactic='product-line-qf-lia',strategy='line',core_sha256=core_sha256))

if __name__=='__main__':
    if len(sys.argv)!=4 or sys.argv[1]!='--arm':
        raise SystemExit('only private --arm PAYLOAD RECEIPT is supported')
    payload=json.loads(Path(sys.argv[2]).read_text())
    def factory(z3,arm):
        core.runner._require(hashlib.sha256(_path.read_bytes()).hexdigest()==payload['record']['product_line_core_sha256'],
                             'product-line core identity changed')
        facade=core.SolverFacade(z3,arm,payload,sys.argv[3],strategy='line')
        return facade,facade.policy
    core.runner._run_arm(sys.argv[2],sys.argv[3],solver_factory=factory)
