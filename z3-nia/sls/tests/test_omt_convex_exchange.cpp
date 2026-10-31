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
        const std::string& output={},bool integer_diagonal=false){
        if(!output.empty()){
            auto source=source_text;
            if(!minimize){const auto at=source.find("(minimize");if(at!=std::string::npos)source.replace(at,9,"(maximize");}
            std::ofstream(output+".source.smt2")<<source<<"\n(check-sat)\n";
        }
        return nia::solve_omt_convex_exchange(context,hard,objective,minimize,best,hints,seconds,output,integer_diagonal);
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
void separable_integer_gap_small_budget(){
    std::ostringstream source;
    constexpr int n=14;
    std::map<std::string,std::string> hints;
    for(int i=0;i<n;++i){source<<"(declare-const x"<<i<<" Int)(assert (>= x"<<i<<" 0))(assert (<= x"<<i<<" 1))";hints["x"+std::to_string(i)]="0";}
    source<<"(minimize (+";
    for(int i=0;i<n;++i)source<<" (* (- (* 2 x"<<i<<") 1) (- (* 2 x"<<i<<") 1))";
    source<<"))";
    Fixture gap(source.str());const auto path=certificate();
    // An aggregate support at an integer point excludes at most one of the
    // 2^14 cube vertices, so even the old 10000-support cap cannot prove this.
    auto legacy=gap.run(std::to_string(n),true,hints,.25);
    check(legacy.answer==z3::unknown&&legacy.certificate_json.empty(),"legacy aggregate supports do not claim the high-dimensional integer gap within the small budget");
    auto proof=gap.run(std::to_string(n),true,hints,.25,path,true);
    gap.verified_unsat(proof,path);
    check(proof.queries<=2,"separable fourteen-dimensional integer gap is proved with at most two LIA queries");
    check(proof.reason.find("integer-diagonal mu=4")!=std::string::npos,"only the exactly verified maximal integer diagonal is used");
    check(proof.certificate_json.find("omt-convex-exchange-v2")!=std::string::npos,"integer diagonal proof carries the v2 certificate");
    auto max_source=source.str();const auto at=max_source.find("(minimize (+");max_source.replace(at,12,"(minimize (- (+");max_source+=")";
    Fixture maximum(max_source);auto max_path=certificate();auto max_proof=maximum.run("-14",false,hints,.25,max_path,true);
    maximum.verified_unsat(max_proof,max_path);
    check(max_proof.queries<=2&&max_proof.reason.find("mu=4")!=std::string::npos,"concave maximization uses the normalized integer relaxation");
    std::cout<<"separable-legacy queries="<<legacy.queries<<" cuts="<<legacy.cuts<<" answer="<<legacy.answer<<'\n';
    std::cout<<"separable-integer-gap queries="<<proof.queries<<" cuts="<<proof.cuts<<" answer="<<proof.answer<<" reason="<<proof.reason<<'\n';
}
void integer_mode_models_fallback_and_source_names(){
    Fixture alias("(declare-const x Int)(declare-const cost Int)(declare-const b Bool)"
                  "(declare-const __omt_integer_square!0 Bool)(declare-const __omt_integer_square!1 Int)"
                  "(assert (= cost (* x x)))(assert (or (and b (= x 2)) (and (not b) (= x 3))))"
                  "(assert __omt_integer_square!0)(assert (= __omt_integer_square!1 77))(minimize cost)");
    auto sat=alias.run("25",true,{{"x","5"}},3,{},true);alias.verified_sat(sat,"");
    if(sat.model)check(mpz_class(Z3_get_numeral_string(alias.context,sat.model->eval(alias.objective,true)),10)<25,"integer-relaxation SAT candidate improves the real source objective");
    auto path=certificate();auto proof=alias.run("4",true,{{"x","2"}},3,path,true);alias.verified_unsat(proof,path);
    check(proof.certificate_json.find("__omt_integer_square!2")!=std::string::npos,"fresh epigraph skips source-only Int and Boolean names");
    Fixture maximum("(declare-const x Int)(declare-const cost Int)(assert (= x 2))(assert (= cost (- (* x x))))(minimize cost)");
    auto max_sat=maximum.run("-25",false,{{"x","5"}},3,{},true);maximum.verified_sat(max_sat,"-4");
    Fixture singular("(declare-const x Int)(declare-const y Int)(minimize (* (+ x y) (+ x y)))");
    path=certificate();proof=singular.run("0",true,{},3,path,true);singular.verified_unsat(proof,path);
    check(proof.reason.find("aggregate-tangent mu=0")!=std::string::npos&&proof.certificate_json.find("omt-convex-exchange-v1")!=std::string::npos,"zero safe integer diagonal falls back to the unchanged aggregate certificate");
    Fixture unsafe("(declare-const x Int)(declare-const y Int)(minimize (* x y))");
    auto rejected=unsafe.run("0",true,{},3,{},true);
    check(!rejected.applicable&&rejected.answer==z3::unknown&&rejected.queries==0,"indefinite source cannot produce integer diagonal cuts or a proof");
    const std::string big="1393796574908163946345982392040522594123776";
    Fixture wide("(declare-const x Int)(assert (= x "+big+"))(minimize (* (- (* 2 (- x "+big+")) 1) (- (* 2 (- x "+big+")) 1)))");
    path=certificate();proof=wide.run("1",true,{},3,path,true);wide.verified_unsat(proof,path);
    check(proof.queries>1,"a false relaxed model triggers exact wide-coordinate square and residual cuts");
    check(proof.reason.find("mu=4")!=std::string::npos,"integer epigraph cuts retain exact GMP coordinates beyond signed128");
    auto wide_sat=wide.run("2",true,{{"x",big}},3,{},true);wide.verified_sat(wide_sat,"1");
    auto bad_output=wide.run("1",true,{{"x",big}},3,directory+"/absent/integer-certificate.json",true);
    check(bad_output.answer==z3::unknown&&!bad_output.model&&bad_output.certificate_json.empty()&&bad_output.queries>0,
          "integer certificate output failure prevents publishing an UNSAT result");
    auto expired=wide.run("1",true,{},1e-300,{},true);
    check(expired.answer==z3::unknown&&expired.queries==0&&expired.certificate_json.empty(),"expired integer mode never publishes proof or source model");
}
void integer_residual_exchanges_and_bounded_mu_search(){
    Fixture coupled("(declare-const x Int)(declare-const y Int)"
                    "(minimize (+ (* (- (* 2 x) 1) (- (* 2 x) 1)) (* (- (* 2 y) 1) (- (* 2 y) 1)) (* (- x y) (- x y))))");
    auto path=certificate();auto proof=coupled.run("2",true,{{"x","17"},{"y","-8"}},3,path,true);coupled.verified_unsat(proof,path);
    check(proof.reason.find("mu=4")!=std::string::npos&&proof.queries>1,"coupled PSD residual refines false LIA candidates using their exact coordinates");
    Fixture rounding("(declare-const x Int)(declare-const y Int)(minimize (+ (* 3 x x) (* x y) (* 3 y y)))");
    path=certificate();proof=rounding.run("0",true,{},3,path,true);rounding.verified_unsat(proof,path);
    check(proof.reason.find("mu=2")!=std::string::npos,"integer mu search retains floor of a nonintegral positive eigenvalue bound");
    mpz_class huge=1;mpz_mul_2exp(huge.get_mpz_t(),huge.get_mpz_t(),80);
    Fixture capped("(declare-const x Int)(minimize (* "+huge.get_str()+" x x))");
    path=certificate();proof=capped.run("0",true,{},3,path,true);capped.verified_unsat(proof,path);
    check(proof.reason.find("mu=2147483648")!=std::string::npos,"32 exact PSD checks cap exponential selection at its last actually verified diagonal");
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
    integer_gap_and_improvement();separable_integer_gap_small_budget();integer_mode_models_fallback_and_source_names();integer_residual_exchanges_and_bounded_mu_search();concave_max_and_singular_psd();complete_boolean_linear_disjunction();
    direct_objective_definition_is_restored();unsupported_source_is_never_a_proof();unbounded_precision_source_models();
    invalid_budgets_best_and_certificate_failures();
    if(!failures)std::cout<<"PASS OMT convex exchange\n";
    return failures?1:0;
}
