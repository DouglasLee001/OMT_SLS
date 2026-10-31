#include "../src/nia_omt_convex_exchange.h"
#include <gmpxx.h>
#include <cmath>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <limits>
#include <map>
#include <sstream>
#include <string>

namespace {
int failures=0;
std::string directory;
void check(bool value,const std::string& why){if(!value){++failures;std::cerr<<"FAIL: "<<why<<'\n';}}
struct Fixture {
    z3::context context;
    z3::expr_vector hard{context};
    z3::expr objective{context.int_val(0)};
    std::string text;
    explicit Fixture(std::string source):text(std::move(source)){
        z3::optimize parser(context);parser.from_string(text.c_str());hard=parser.assertions();objective=parser.objectives()[0];
    }
    nia::OmtConvexExchangeResult run(const std::string& best,const std::map<std::string,std::string>& hints={},
        double seconds=3,const std::string& name={},bool affine=true,bool minimize=true,bool diagonal=true){
        const auto path=name.empty()?std::string{}:directory+"/"+name+".json";
        if(!path.empty()){
            auto source=text;if(!minimize){auto at=source.find("(minimize");if(at!=std::string::npos)source.replace(at,9,"(maximize");}
            std::ofstream(path+".source.smt2")<<source<<"\n(check-sat)\n";
        }
#ifdef OMT_AFFINE_RED_CONTROL
        (void)affine;
        return nia::solve_omt_convex_exchange(context,hard,objective,minimize,best,hints,seconds,path,diagonal);
#else
        return nia::solve_omt_convex_exchange(context,hard,objective,minimize,best,hints,seconds,path,diagonal,affine);
#endif
    }
    void proof(const nia::OmtConvexExchangeResult& result,const std::string& name,const std::string& version="omt-convex-exchange-v3"){
        check(result.applicable&&result.answer==z3::unsat&&!result.model,"source strict bound proved: "+name+": "+result.reason);
        check(result.queries>0&&!result.certificate_json.empty(),"proof uses a real linear query and certificate: "+name);
        check(result.certificate_json.find(version)!=std::string::npos,"proof version "+version+": "+name);
        std::ifstream saved(directory+"/"+name+".json");const std::string bytes((std::istreambuf_iterator<char>(saved)),{});
        check(!bytes.empty()&&bytes==result.certificate_json,"saved certificate agrees: "+name);
    }
    void sat(const nia::OmtConvexExchangeResult& result,const std::string& expected){
        check(result.applicable&&result.answer==z3::sat&&result.model,"verified original-source SAT: "+result.reason);
        if(!result.model)return;
        for(const auto& c:hard)check(result.model->eval(c,true).is_true(),"every original hard assertion is true");
        const auto value=result.model->eval(objective,true);
        check(value.is_int()&&value.is_numeral()&&std::string(Z3_get_numeral_string(context,value))==expected,"restored exact original objective is "+expected);
    }
};
std::string chain(int n,std::map<std::string,std::string>& hints){
    std::ostringstream out;for(int i=0;i<n;++i){out<<"(declare-const x"<<i<<" Int)(assert (>= x"<<i<<" 0))(assert (<= x"<<i<<" 1))";hints["x"+std::to_string(i)]="0";}
    out<<"(minimize (+";for(int i=0;i<n;++i){const auto t=i+1<n?"(+ x"+std::to_string(i)+" x"+std::to_string(i+1)+")":"x"+std::to_string(i);out<<" (* (- (* 2 "<<t<<") 1) (- (* 2 "<<t<<") 1))";}out<<"))";return out.str();
}
void core(){
    std::map<std::string,std::string> hints;Fixture coupled(chain(14,hints));
    auto legacy=coupled.run("14",hints,.1,{},false);
    check(legacy.answer==z3::unknown&&legacy.certificate_json.empty(),"987-point aggregate integer gap remains open in small budget");
    auto proof=coupled.run("14",hints,.5,"chain14");coupled.proof(proof,"chain14");
    check(proof.queries==1,"coupled parity gap is closed by one affine integer query");
    check(proof.cuts==29,"chain starts with 28 adjacent supports and one fixed source bound");
    Fixture hybrid("(declare-const x Int)(declare-const y Int)(assert (>= x 0))(assert (<= x 1))(assert (>= y 0))(assert (<= y 1))"
                   "(minimize (+ (* 5 x x) (* 2 x y) (* 5 y y) (* (- 3) x) (* (- 5) y)))");
    auto uniform=hybrid.run("0",{{"x","0"},{"y","0"}},3,{},false);
    check(uniform.answer==z3::unsat&&uniform.queries==2,"old diagonal leaves the unique false candidate and needs a second query");
    auto strengthened=hybrid.run("0",{{"x","0"},{"y","0"}},3,"hybrid");hybrid.proof(strengthened,"hybrid");
    check(strengthened.queries==1&&strengthened.cuts==7,"coordinate squares plus residual coupling close the gap in one query");
    std::cout<<"chain legacy_queries="<<legacy.queries<<" affine_queries="<<proof.queries<<" affine_cuts="<<proof.cuts<<" answer="<<proof.answer<<'\n';
    std::cout<<"hybrid uniform_queries="<<uniform.queries<<" affine_queries="<<strengthened.queries<<" answer="<<strengthened.answer<<'\n';
}
void arithmetic_and_sources(){
    Fixture rational("(declare-const x Int)(declare-const y Int)(assert (= y 1))(minimize (+ (* x x) (* x y) (* y y)))");
    auto r=rational.run("1",{{"x","0"},{"y","1"}},3,"rational");rational.proof(r,"rational");
    check(r.queries==1&&r.certificate_json.find("1/4")!=std::string::npos,"half-integral Q keeps exact rational factor weights");
    Fixture singular("(declare-const x Int)(declare-const y Int)(minimize (* (- (* 2 (+ x y)) 1) (- (* 2 (+ x y)) 1)))");
    r=singular.run("1",{},3,"singular");singular.proof(r,"singular");check(r.queries==1&&r.cuts==3,"zero PSD pivots are omitted from exact rank-one identity");
    std::map<std::string,std::string> hints;auto maximum_source=chain(14,hints);auto at=maximum_source.find("(minimize ");maximum_source.insert(at+10,"(- ");maximum_source+=")";
    Fixture maximum(maximum_source);r=maximum.run("-14",hints,3,"maximum",true,false);maximum.proof(r,"maximum");check(r.queries==1,"concave maximization normalizes the same exact source identity");
    mpz_class big=1;mpz_mul_2exp(big.get_mpz_t(),big.get_mpz_t(),140);const auto b=big.get_str();
    Fixture wide("(declare-const x Int)(assert (= x "+b+"))(minimize (* (- (* 2 (- x "+b+")) 1) (- (* 2 (- x "+b+")) 1)))");
    r=wide.run("1",{{"x",b}},3,"wide");wide.proof(r,"wide");check(r.queries==1,"GMP factors/supports preserve >signed128 coordinates");
    Fixture alias("(declare-const x Int)(declare-const cost Int)(declare-const b Bool)(declare-const __omt_affine_square!0 Bool)"
                  "(assert __omt_affine_square!0)(assert (= cost (* (- (* 2 x) 1) (- (* 2 x) 1))))"
                  "(assert (or (and b (= x 2)) (and (not b) (= x 3))))(minimize cost)");
    auto sat=alias.run("25",{{"x","3"}});alias.sat(sat,"9");
    r=alias.run("9",{{"x","2"}},3,"alias");alias.proof(r,"alias");check(r.certificate_json.find("__omt_affine_square!1")!=std::string::npos,"epigraph avoids source-only Boolean symbol collision");
    Fixture hard("(declare-const x Int)(declare-const y Int)(assert (<= (* x y) 10))(minimize (+ (* x x) (* y y)))");
    r=hard.run("0");check(!r.applicable&&r.answer==z3::unknown&&r.queries==0,"nonlinear original hard assertions remain unsupported rather than discarded");
    Fixture indefinite("(declare-const x Int)(declare-const y Int)(minimize (* x y))");r=indefinite.run("0");
    check(!r.applicable&&r.answer==z3::unknown&&r.queries==0,"indefinite source cannot publish affine proof");
}
void fallback_off_and_failures(){
    // M=4 is small, but scaling this valid source linear coefficient exceeds
    // the arithmetic guard. Rejection must happen before selecting affine.
    mpz_class source_linear=1;mpz_mul_2exp(source_linear.get_mpz_t(),source_linear.get_mpz_t(),65535);
    Fixture scaled("(declare-const x Int)(declare-const y Int)(assert (= x 0))(assert (= y 0))"
                   "(minimize (+ (* x x) (* x y) (* y y) (* "+source_linear.get_str()+" x)))");
    auto scaled_off=scaled.run("0",{},3,{},false);
    check(scaled_off.answer==z3::unsat&&scaled_off.queries==1,"unscaled source tangent proves the large-linear-coefficient bound");
    auto scaled_fallback=scaled.run("0",{},3,"scaled-objective-fallback");
    scaled.proof(scaled_fallback,"scaled-objective-fallback","omt-convex-exchange-v1");
    check(scaled_fallback.queries==1&&scaled_fallback.reason.find("affine-fallback=")!=std::string::npos,
          "scaled objective bit guard retains the verified aggregate mode within the shared budget");
    std::cout<<"scaled-objective bits=65536 old_queries="<<scaled_off.queries<<" affine_queries="<<scaled_fallback.queries<<" answer="<<scaled_fallback.answer<<'\n';
    mpz_class p=1;mpz_mul_2exp(p.get_mpz_t(),p.get_mpz_t(),600);mpz_class coefficient=p+2;
    Fixture large("(declare-const x Int)(declare-const y Int)(minimize (+ (* "+coefficient.get_str()+" x x) (* 2 x y) (* 2 y y)))");
    auto fallback=large.run("0",{},3,"fallback");large.proof(fallback,"fallback","omt-convex-exchange-v2");
    check(fallback.reason.find("512")!=std::string::npos&&fallback.reason.find("mu=1")!=std::string::npos,"common scale guard keeps the proven uniform diagonal");
    Fixture gap("(declare-const x Int)(minimize (* (- (* 2 x) 1) (- (* 2 x) 1)))");
    const auto implicit_path=directory+"/off-implicit.json";
    std::ofstream(implicit_path+".source.smt2")<<gap.text<<"\n(check-sat)\n";
    auto legacy=nia::solve_omt_convex_exchange(gap.context,gap.hard,gap.objective,true,"1",{{"x","0"}},3,implicit_path,true);
    auto off=gap.run("1",{{"x","0"}},3,"off-explicit",false);
    check(legacy.answer==off.answer&&legacy.queries==off.queries&&legacy.cuts==off.cuts&&legacy.reason==off.reason,
          "explicit affine OFF matches the old default answer, work counters and mode");
    // SMT2 pretty printing uses context-local AST IDs for let names. The
    // Python harness compares parsed query ASTs and every other JSON field.
    auto output=gap.run("1",{},3,"absent/certificate");
    check(output.answer==z3::unknown&&output.queries>0&&output.certificate_json.empty()&&!output.model,"requested output failure suppresses UNSAT publication");
    auto expired=gap.run("1",{},1e-300);check(expired.answer==z3::unknown&&expired.queries==0&&expired.certificate_json.empty(),"expired shared budget cannot publish a proof");
    mpz_class huge=1;mpz_mul_2exp(huge.get_mpz_t(),huge.get_mpz_t(),40000);
    auto guarded=gap.run("1",{{"x",huge.get_str()}},3);
    check(guarded.answer==z3::unknown&&guarded.queries==0&&guarded.certificate_json.empty()&&guarded.reason.find("bit guard")!=std::string::npos,"exact support arithmetic guard never rounds a huge hint");
    auto unlimited=gap.run("1",{},std::numeric_limits<double>::infinity(),"infinite");gap.proof(unlimited,"infinite");
}
}
int main(int argc,char** argv){
    if(argc<2){std::cerr<<"artifact directory required\n";return 2;}directory=argv[1];std::filesystem::create_directories(directory);
    core();if(argc<3||std::string(argv[2])!="--core"){arithmetic_and_sources();fallback_off_and_failures();}
    if(failures){std::cerr<<failures<<" affine checks failed\n";return 1;}std::cout<<"PASS native exact affine convex exchange\n";return 0;
}
