import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.dont_write_bytecode=True
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'script'))
import verify_reference_case_splits as checker

class CaseSplitProofTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        certificate=ROOT/'result/optimize/reference_diagnostics/case_split_certificates/0062.json'
        if not certificate.is_file() or not (ROOT/'result/optimize/full_v8_20261022/manifest.json').is_file():
            raise unittest.SkipTest('optional retained reference proof artifacts are unavailable')
        cls.certificate=json.loads(certificate.read_text())
        cls.references,cls.artifacts=checker.context(ROOT/'result/optimize/full_v8_20261022')
    def result(self,certificate):
        with tempfile.TemporaryDirectory(prefix='omt-casesplit-test-') as temp:
            p=Path(temp)/'certificate.json';p.write_text(json.dumps(certificate))
            return checker.verify_certificate(p,self.references,self.artifacts)
    def changed(self):return copy.deepcopy(self.certificate)
    def dleaf(self,c):return c['tree']['righttrue']['righttrue']['righttrue']['leftfalse']['certificate']
    def reject(self,c,fragment):
        result=self.result(c)
        self.assertFalse(result['verified'],result)
        self.assertIn(fragment,result['error'])
    def test_exact_case62_complete_five_leaf_proof(self):
        result=self.result(self.certificate)
        self.assertTrue(result['verified'],result)
        self.assertEqual(result['leaves'],5)
    def test_missing_branch_rejected(self):
        c=self.changed();del c['tree']['righttrue'];self.reject(c,'incomplete')
    def test_wrong_weight_rejected(self):
        c=self.changed();c['tree']['leftfalse']['certificate']['certificate_terms'][0]['weight']='1'
        self.reject(c,'not exactly -1')
    def test_negative_nonnegative_weight_rejected(self):
        c=self.changed();c['tree']['leftfalse']['certificate']['certificate_terms'][0]['weight']='-1/2'
        self.reject(c,'negative inequality weight')
    def test_forged_column_rejected(self):
        c=self.changed();c['tree']['leftfalse']['certificate']['certificate_terms'][0]['polynomial'][0][1]='100'
        self.reject(c,'attached polynomial differs')
    def test_cancel_requires_proven_nonzero_factor(self):
        c=self.changed();self.dleaf(c)['derived_equalities'][0]['why']['nonzero_literal']=70
        self.reject(c,'nonzero')
    def test_cancel_requires_matching_factor(self):
        c=self.changed();self.dleaf(c)['derived_equalities'][0]['why']['factor_variable']=28
        self.reject(c,'factor')
    def test_cancel_requires_zero_product(self):
        c=self.changed();self.dleaf(c)['derived_equalities'][0]['why']['product']={'type':'forced_equality','literal':29}
        self.reject(c,'divisible')
    def test_cancel_attached_polynomial_checked(self):
        c=self.changed();self.dleaf(c)['derived_equalities'][0]['polynomial'][0][1]='2'
        self.reject(c,'derived equality differs')
    def test_future_derived_reference_rejected(self):
        c=self.changed();self.dleaf(c)['derived_equalities'][0]['why']['product']={'type':'derived_equality','id':0}
        self.reject(c,'unproved derived equality')
    def test_unit_trail_provenance_checked(self):
        c=self.changed();leaf=c['tree']['leftfalse']['certificate']
        entry=next(e for e in leaf['unitprop_trail'] if e[2]['type']=='unit_clause' and e[2]['clause']!=0)
        entry[2]['clause']=0;self.reject(c,'already satisfied')
    def test_complete_path_must_match_leaf(self):
        c=self.changed();c['tree']['leftfalse']['certificate']['branch_assumptions'][0][1]=True
        self.reject(c,'exhaustive split path')
    def test_reference_not_trusted(self):
        c=self.changed();c['reference_best_value']='1';self.reject(c,'nonzero historical reference')
    def test_hash_not_trusted(self):
        c=self.changed();c['input_sha256']='0'*64;self.reject(c,'SHA256')

if __name__=='__main__':unittest.main()
