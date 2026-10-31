#!/usr/bin/env python3
"""Task20 exact/pure tests, plus one opt-in <=6s generated SDK child."""
from pathlib import Path
import hashlib
import importlib.util
import itertools
import json
import math
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT=Path(__file__).resolve().parents[3]
MODULE=ROOT/'z3-nia/experiment/affine_ray_diagnostic.py'
def load():
    spec=importlib.util.spec_from_file_location('_affine_ray_producer_test',MODULE)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
producer=load()
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

class PureTests(unittest.TestCase):
    def test_exact_quadratic_coefficient_product(self):
        self.assertEqual(producer._multiply((1,2),(1,2)),(1,4,4))
        self.assertEqual(producer._multiply((-3,2),(4,-1)),(-12,11,-2))
        with self.assertRaises(producer.Unsupported):producer._multiply((0,0,1),(0,1))

    def test_dominance_threshold_uses_ceiling_and_positive_one(self):
        self.assertEqual(producer._threshold((100,-3)),35)
        self.assertEqual(producer._threshold((2,-3,2)),4)
        self.assertEqual(producer._threshold((0,0,0)),1)

    def test_signed_eventual_truth_preserves_equal_and_strict(self):
        relations={'=':lambda n:n==0,'<':lambda n:n<0,'<=':lambda n:n<=0,
                   '>':lambda n:n>0,'>=':lambda n:n>=0}
        for coefficients in itertools.product(range(-2,3),repeat=3):
            threshold=producer._threshold(coefficients)
            for relation,predicate in relations.items():
                actual=producer._eventual(coefficients,relation)
                for t in (threshold,threshold+1,threshold+5):
                    self.assertEqual(actual,predicate(sum(c*t**i for i,c in enumerate(coefficients))))

    def test_invalid_seconds_direction_and_existing_output_are_safe(self):
        with tempfile.TemporaryDirectory() as folder:
            directory=Path(folder);source=directory/'source.smt2'
            original='(declare-const x Int)(minimize x)';source.write_text(original)
            for i,seconds in enumerate((0,-1,math.nan,math.inf,1200.1)):
                result=producer.analyze(source,'min',seconds,directory/f'bad{i}',None)
                self.assertEqual(result['status'],'error');self.assertIsNone(result['candidate_path'])
            self.assertEqual(producer.analyze(source,'other',1,directory/'direction',None)['status'],'error')
            self.assertEqual(producer.analyze(source,'min',1,directory,None)['status'],'error')
            self.assertEqual(source.read_text(),original)

    def test_module_import_does_not_import_z3(self):
        self.assertNotIn('z3',sys.modules)

    def test_raw_fragment_rejects_hidden_unsupported_syntax(self):
        strict=producer._strict(None)
        bad=['(declare-const b Bool)(minimize 0)',
             '(declare-const x Int)(minimize (ite true x x))',
             '(declare-const x Int)(minimize (div x 1))',
             '(declare-fun f (Int) Int)(minimize 0)',
             '(declare-const x Real)(minimize 0)',
             '(declare-const x Int)(minimize (^ x 0))',
             '(declare-const x Int)(minimize (let ((y x)) y))']
        for source in bad:
            filtered,_,_=strict._query(source)
            with self.assertRaises(producer.Unsupported):producer._raw_validate(strict._commands(filtered),lambda:None)

    def test_fresh_coefficient_prefix_avoids_source_names(self):
        source='(declare-const affine_ray_coeff Int)(declare-const affine_ray_coeff_1 Int)'
        prefix=producer._coefficient_prefix(source)
        self.assertNotIn(prefix,source)

    def test_publication_never_clobbers_files_symlinks_or_directories(self):
        with tempfile.TemporaryDirectory() as folder:
            folder=Path(folder);original=folder/'original';original.write_text('protected bytes')
            link=folder/'link';link.symlink_to(original);directory=folder/'directory';directory.mkdir()
            for target in (original,link,directory):
                with self.assertRaises(OSError):producer._publish(target,'new',lambda:None)
                self.assertEqual(original.read_text(),'protected bytes')
                self.assertFalse(target.with_name(target.name+'.own-tmp').exists())
            self.assertTrue(link.is_symlink());self.assertTrue(directory.is_dir())

    def test_late_publication_rolls_back_only_its_own_file(self):
        with tempfile.TemporaryDirectory() as folder:
            target=Path(folder)/'candidate.json';calls=0
            def remaining():
                nonlocal calls
                calls+=1
                if calls==4:raise TimeoutError('post-link deadline')
            with self.assertRaises(TimeoutError):producer._publish(target,'candidate',remaining)
            self.assertFalse(target.exists());self.assertEqual(list(Path(folder).iterdir()),[])

    def test_resource_and_immediate_deadline_do_not_start_sdk(self):
        strict=producer._strict(None)
        forms=strict._commands('(declare-const x Int)(minimize (+ x 1))')
        with mock.patch.object(producer,'MAX_NODES',1):
            with self.assertRaises(producer.Unsupported):producer._raw_validate(forms,lambda:None)
        forms=strict._commands('(minimize '+str(1<<8192)+')')
        with self.assertRaises(producer.Unsupported):producer._raw_validate(forms,lambda:None)
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'source.smt2';source.write_text('(minimize 0)')
            result=producer.analyze(source,'min',5e-324,Path(folder)/'new',None)
            self.assertEqual(result['status'],'timeout');self.assertFalse(result['check_started'])
            self.assertIsNone(result['candidate_path']);self.assertFalse((Path(folder)/'new').exists())

class GeneratedTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('NIA_AFFINE_RAY_REAL')=='1','explicit generated SDK test only')
    def test_one_fixed_generated_group(self):
        output=Path(os.environ['NIA_AFFINE_RAY_ARTIFACTS'])
        result=subprocess.run([sys.executable,str(Path(__file__).resolve()),'--child',
            os.environ['NIA_AFFINE_RAY_LIBRARY'],os.environ['NIA_AFFINE_RAY_SDK'],str(output)],
            text=True,capture_output=True,timeout=6)
        (output.parent/'generated.stdout').write_text(result.stdout)
        (output.parent/'generated.stderr').write_text(result.stderr)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)

def generated(library,sdk,output):
    import builtins,ctypes
    library=Path(library).resolve();sdk=Path(sdk).resolve();output=Path(output);output.mkdir(exist_ok=False)
    assert sha(library)=='2641bd9dd1fb41c1051e5893fa070913d2e215161756a65a56206fbc8b372bd5'
    ctypes.CDLL(str(library));builtins.Z3_LIB_DIRS=[str(library.parent)]
    for key in ('Z3_LIBRARY_PATH','PYTHONPATH','PATH'):os.environ.pop(key,None)
    sys.path.insert(0,str(sdk))
    import z3
    assert Path(z3.__file__).resolve().is_relative_to(sdk)
    cases=[('zero-own-objective-independent-direction','min','(declare-const x Int)(declare-const y Int)(assert (= y (+ x 1)))(minimize x)','candidate'),
           ('discrete-slope-100','min','(declare-const x Int)(declare-const g Int)(assert (= g (* 100 x)))(minimize g)','candidate'),
           ('max-or-not','max','(declare-const x Int)(assert (or (< x 0) (> x 10)))(assert (not (= x 5)))(maximize (* 100 x))','candidate'),
           ('quadratic-improving-leading-term','min','(declare-const x Int)(minimize (- (* x x)))','candidate'),
           ('bounded-objective','min','(declare-const x Int)(assert (>= x 0))(minimize x)','no_affine_ray'),
           ('constant-objective','max','(declare-const unused Int)(maximize 7)','no_affine_ray')]
    results=[]
    try:
        for index,(name,direction,text,expected) in enumerate(cases):
            directory=output/f'{index}-{name}';directory.mkdir();source=directory/'original.smt2';source.write_text(text)
            result=producer.analyze(source,direction,.5,directory/'producer',z3)
            (directory/'result.json').write_text(json.dumps(result,indent=2)+'\n')
            results.append(result)
            assert source.read_text()==text and result['status']==expected,(name,result)
            assert result['diagnostic_only'] and not result['is_unbounded'] and not result['is_optimal']
            if expected=='candidate':
                cert=json.loads(Path(result['candidate_path']).read_text())
                assert cert['schema']=='source-affine-ray-v1' and cert['source_sha256']==sha(source)
                assert cert['direction']==direction and int(cert['threshold'])>=1
                variables={v['name']:v for v in cert['integer_variables']}
                assert list(variables)==sorted(variables)
                if index==0:
                    assert int(variables['x']['slope'])<0
                    assert variables['x']['slope']==variables['y']['slope']
                    assert int(variables['y']['intercept'])==int(variables['x']['intercept'])+1
                elif index==1:
                    assert int(variables['g']['slope'])<0 and int(variables['g']['slope'])%100==0
                elif index==2:assert int(variables['x']['slope'])>0
                elif index==3:assert int(variables['x']['slope'])!=0
            else:assert result['candidate_path'] is None
        receipt=dict(passed=True,results=results,library_sha256=sha(library),module_sha256=sha(MODULE),test_sha256=sha(__file__),
                     explicit_parent_window_seconds=6,internal_search_windows_seconds=3,solver_checks=6,no_benchmark=True)
        (output/'verification.json').write_text(json.dumps(receipt,indent=2)+'\n')
        print('PASS six generated affine-ray cases')
        return 0
    except Exception as error:
        (output/'failure.json').write_text(json.dumps(dict(error=repr(error),results=results),indent=2)+'\n')
        raise

if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='--child':raise SystemExit(generated(*sys.argv[2:]))
    unittest.main()
