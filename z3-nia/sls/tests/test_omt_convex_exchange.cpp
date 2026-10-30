#include "../src/nia_omt_convex_exchange.h"
#include <gmpxx.h>
#include <cmath>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <limits>
#include <sstream>
#include <string>

namespace {
int failures=0, certificates=0;
std::string directory;
void check(bool yes,const std::string& why){if(!yes){++failures;std::cerr<<"FAIL: "<<why<<'\n';}}
struct Fixture {
    z3::context context;
    z3::expr_vector hard{context};
    z3::expr objective{context.int_val(0)};
    std::string source_text;
    explicit Fixture(const std::string& source):source_text(source){z3::optimize parser(context);parser.from_string(source.c_str());hard=parser.assertions();objective=parser.objectives()[0];}
    nia::OmtConvexExchangeResult run(const std::string& best,bool minimize=true,
        const std::map<std::string,std::string>& hints={},double seconds=3,
        const std::string& output={}){
        if(!output.empty()){
            auto source=source_text;
            if(!minimize){const auto at=source.find("(minimize");if(at!=std::string::npos)source.replace(at,9,"(maximize");}
            std::ofstream(output+".source.smt2")<<source<<"\n(check-sat)\n";
        }
        return nia::solve_omt_convex_exchange(context,hard,objective,minimize,best,hints,seconds,output);
    }
    void verified_sat(const nia::OmtConvexExchangeResult& result,const std::string& expected){
        check(result.applicable && result.answer==z3::sat && result.model!=nullptr,"convex exchange returns a concrete source SAT model: "+result.reason);
        if(!result.model)return;
        for(const auto& constraint:hard)check(result.model->eval(constraint,true).is_true(),"returned source model satisfies every original hard constraint");
        const auto value=result.model->eval(objective,true);
        check(value.is_int() && value.is_numeral() && (expected.empty() || std::string(Z3_get_numeral_string(context,value))==expected),"source objective is restored in its original sign");
    }
    void verified_unsat(nia::OmtConvexExchangeResult& result,const std::string& path){
        check(result.applicable && result.answer==z3::unsat && !result.model,"exact convex cuts prove source strict query UNSAT: "+result.reason);
        check(result.queries>0 && result.cuts>0 && !result.certificate_json.empty(),"proof requires actual linear queries, cuts, and full certificate");
        std::ifstream file(path);const std::string saved((std::istreambuf_iterator<char>(file)),{});
        check(saved==result.certificate_json && !saved.empty(),"returned proof certificate was fully saved");
    }
};
std::string certificate(){return directory+"/certificate-"+std::to_string(++certificates)+".json";}
void integer_gap_and_improvement(){
    Fixture gap("(declare-const x Int)(minimize (* (- (* 2 x) 1) (- (* 2 x) 1)))");
    const auto path=certificate();auto proof=gap.run("1",true,{{"x","0"}},3,path);
    gap.verified_unsat(proof,path);
    check(proof.cuts>=2 && proof.queries>=2,"(2x-1)^2 requires multiple real tangent exchanges to prove its integer gap");
    std::cout<<"integer-gap queries="<<proof.queries<<" cuts="<<proof.cuts<<'\n';
    Fixture lower("(declare-const x Int)(assert (>= x 0))(minimize (* x x))");
    auto sat=lower.run("4",true,{{"x","2"}});lower.verified_sat(sat,"");
    if(sat.model)check(mpz_class(Z3_get_numeral_string(lower.context,sat.model->eval(lower.objective,true)),10)<4,"convex SAT strictly improves the incumbent");
}
void concave_max_and_singular_psd(){
    Fixture upper("(declare-const x Int)(assert (= x 1))(minimize (- (* x x)))");
    auto sat=upper.run("-4",false,{{"x","2"}});upper.verified_sat(sat,"-1");
    auto maximum_path=certificate();auto maximum=upper.run("-1",false,{},3,maximum_path);upper.verified_unsat(maximum,maximum_path);
    Fixture singular("(declare-const x Int)(declare-const y Int)(minimize (* (+ x y) (+ x y)))");
    auto path=certificate();auto proof=singular.run("0",true,{},3,path);singular.verified_unsat(proof,path);
    Fixture rational("(declare-const x Int)(declare-const y Int)(minimize (+ (* x x) (* x y) (* y y)))");
    path=certificate();proof=rational.run("0",true,{},3,path);rational.verified_unsat(proof,path);
    check(proof.certificate_json.find("\"3/2\"")!=std::string::npos,"noninteger rational LDL pivots are recorded exactly");
    Fixture constant("(assert true)(minimize 7)");path=certificate();proof=constant.run("7",true,{},3,path);constant.verified_unsat(proof,path);
}
void complete_boolean_linear_disjunction(){
    Fixture f("(declare-const x Int)(declare-const b Bool)"
              "(assert (or (and b (= x 0)) (and (not b) (= x 2))))"
              "(minimize (* (- x 1) (- x 1)))");
    auto path=certificate();auto proof=f.run("1",true,{{"x","1"}},3,path);f.verified_unsat(proof,path);
    check(proof.cuts>=3 && proof.queries>=3,"all linear disjuncts survive multiple actual objective cuts");
    std::cout<<"boolean-disjunction queries="<<proof.queries<<" cuts="<<proof.cuts<<'\n';
    Fixture factor_zero("(declare-const x Int)(declare-const y Int)(assert (= (* x y) 0))(minimize (* x x))");
    path=certificate();proof=factor_zero.run("0",true,{},3,path);factor_zero.verified_unsat(proof,path);
}
void direct_objective_definition_is_restored(){
    Fixture f("(declare-const x Int)(declare-const cost Int)(assert (= cost (* x x)))"
              "(assert (= x 2))(minimize cost)");
    auto sat=f.run("9",true,{{"x","3"},{"cost","9"}});f.verified_sat(sat,"4");
    Fixture gap("(declare-const x Int)(declare-const cost Int)"
                "(assert (= (* (- (* 2 x) 1) (- (* 2 x) 1)) cost))(minimize cost)");
    auto path=certificate();auto proof=gap.run("1",true,{},3,path);gap.verified_unsat(proof,path);
}
void unsupported_source_is_never_a_proof(){
    const std::vector<std::string> sources={
      "(declare-const x Int)(minimize (- (* x x)))",
      "(declare-const x Int)(declare-const y Int)(minimize (* 2 x y))",
      "(declare-const x Int)(declare-const y Int)(minimize (- (* x x) (* y y)))",
      "(declare-const x Int)(minimize (* x x x))",
      "(declare-const x Int)(declare-const y Int)(assert (= (* x y) 2))(minimize (* x x))",
      "(declare-const x Int)(declare-const cost Int)(assert (= cost (* x x)))(assert (= cost 4))(minimize cost)",
      "(declare-const x Int)(declare-const cost Int)(assert (= cost (+ cost x)))(minimize cost)",
      "(declare-const x Int)(declare-const cost Int)(declare-const b Bool)(assert (or b (= cost (* x x))))(minimize cost)",
      "(declare-const x Int)(declare-const cost Int)(assert (= cost (* x x)))(assert (<= cost 3))(minimize cost)"
    };
    for(const auto& source:sources){Fixture f(source);auto result=f.run("0");
      check(!result.applicable && result.answer==z3::unknown && !result.model && result.queries==0 && result.certificate_json.empty(),
            "non-PSD, degree>2, nonlinear hard, ambiguous/self/nested definitions conservatively reject: "+source+" / "+result.reason);}
    std::ostringstream many;for(int i=0;i<65;++i)many<<"(declare-const x"<<i<<" Int)";
    many<<"(minimize (+";for(int i=0;i<65;++i)many<<" x"<<i;many<<"))";
    Fixture dimension(many.str());auto result=dimension.run("0");check(!result.applicable && result.answer==z3::unknown,"dimension guard rejects without querying");
    mpz_class huge=1;mpz_mul_2exp(huge.get_mpz_t(),huge.get_mpz_t(),65536);
    Fixture bits("(declare-const x Int)(minimize (* "+huge.get_str()+" x x))");result=bits.run("0");
    check(!result.applicable && result.answer==z3::unknown && result.queries==0,"arithmetic bit guard rejects without truncation or proof");
}
void unbounded_precision_source_models(){
    const std::string big="1393796574908163946345982392040522594123776"; // 2^140
    const std::string prefix="(declare-const x Int)(assert (= x "+big+"))";
    Fixture exact(prefix+"(minimize (* (- x "+big+") (- x "+big+")))");
    auto sat=exact.run("1");exact.verified_sat(sat,"0");
    if(sat.model)check(std::string(Z3_get_numeral_string(exact.context,sat.model->eval(exact.context.int_const("x"),true)))==big,
      "source candidate preserves coordinates above signed128 without artificial domain restrictions");
    const std::string term="(- (* 2 (- x "+big+")) 1)";
    Fixture cut(prefix+"(minimize (* "+term+" "+term+"))");
    auto path=certificate();auto proof=cut.run("1",true,{},3,path);cut.verified_unsat(proof,path);
    check(proof.cuts>=2,"a source model above signed128 is used as an exact tangent point");
    std::cout<<"wide-coordinate queries="<<proof.queries<<" cuts="<<proof.cuts<<'\n';
}
void invalid_budgets_best_and_certificate_failures(){
    Fixture f("(declare-const x Int)(minimize (* x x))");
    for(const std::string best:{"","0.5","--1","nan","0) (assert false)"}){auto r=f.run(best);
      check(!r.applicable && r.answer==z3::unknown && !r.model && r.queries==0,"invalid incumbent decimal cannot initiate a proof");}
    for(double seconds:{0.,-1.,1e-300,std::numeric_limits<double>::quiet_NaN(),-std::numeric_limits<double>::infinity()}){auto r=f.run("0",true,{},seconds);
      check(r.answer==z3::unknown && !r.model && r.queries==0 && r.certificate_json.empty(),"zero/tiny/invalid budget cannot publish SAT or UNSAT");}
    auto path=certificate();auto proof=f.run("0",true,{},std::numeric_limits<double>::infinity(),path);f.verified_unsat(proof,path);
    auto bad=f.run("0",true,{},3,directory+"/absent/certificate.json");
    check(bad.answer==z3::unknown && !bad.model && bad.certificate_json.empty() && bad.queries>0,"certificate write failure prevents an UNSAT result");
    z3::context other;z3::expr_vector wrong(other);auto r=nia::solve_omt_convex_exchange(f.context,wrong,f.objective,true,"0",{},3);
    check(!r.applicable && r.answer==z3::unknown && r.queries==0,"cross-context source inputs reject before Z3 assertions");
}
}
int main(int argc,char** argv){
    directory=argc>1?argv[1]:"/tmp/omt-convex-exchange-tests";std::filesystem::create_directories(directory);
    integer_gap_and_improvement();concave_max_and_singular_psd();complete_boolean_linear_disjunction();
    direct_objective_definition_is_restored();unsupported_source_is_never_a_proof();unbounded_precision_source_models();
    invalid_budgets_best_and_certificate_failures();
    if(!failures)std::cout<<"PASS OMT convex exchange\n";
    return failures?1:0;
}
