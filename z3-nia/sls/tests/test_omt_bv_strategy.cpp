#include "../src/nia_ls.h"
#include "../src/nia_omt_input.h"
#include "../src/nia_omt_optimality.h"
#include <z3++.h>
#include <gmpxx.h>
#include <dlfcn.h>
#include <chrono>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <limits>
#include <random>
#include <sstream>
#include <thread>

extern "C" Z3_tactic __real_Z3_mk_tactic(Z3_context, const char*);
extern "C" Z3_tactic __wrap_Z3_mk_tactic(Z3_context context, const char* name) {
    std::cerr << "bv-test native_tactic name=" << name << std::endl;
    return __real_Z3_mk_tactic(context, name);
}
extern "C" Z3_lbool __real_Z3_solver_check(Z3_context, Z3_solver);
extern "C" Z3_lbool __wrap_Z3_solver_check(Z3_context context, Z3_solver solver) {
    std::cerr << "bv-test sdk_check_enter" << std::endl;
    const auto result = __real_Z3_solver_check(context, solver);
    std::cerr << "bv-test sdk_check_return answer=" << int(result) << std::endl;
    return result;
}
namespace {
int failures = 0;
void require(bool value, const std::string& message) {
    if (!value) { ++failures; std::cerr << "FAIL " << message << std::endl; }
}
std::string read(const std::filesystem::path& file) {
    std::ifstream input(file); return {std::istreambuf_iterator<char>(input), {}};
}
std::string hex(const std::string& text) {
    const char* digits="0123456789abcdef"; std::string result;
    for (unsigned char ch:text) { result += digits[ch>>4]; result += digits[ch&15]; }
    return result;
}
struct Fixture {
    nia::ls_solver native{19,0,true};
    std::filesystem::path source,query;
    std::string text;
    Fixture(const std::filesystem::path& directory,const std::string& original,
            const std::string& objective="g",const std::string& direction="min")
        : source(directory/"original.smt2"),query(directory/"strict.smt2"),text(original) {
        std::filesystem::create_directories(directory); std::ofstream(source)<<text;
        native.omt_loaded=true; native.omt_source=source.string();
        native.omt_original_objective=objective; native.omt_direction=direction;
        native._num_vars=native._num_lits=native._num_clauses=0;
    }
    void load(const std::string& body) {
        std::istringstream input("NIA_LS_OMT 1\nSTATUS ready\nSOURCE "+hex(source.string())+
            "\nORIGINAL_OBJECTIVE "+hex(native.omt_original_objective)+"\nDIRECTION "+native.omt_direction+
            "\n"+body+"\nEND\n");
        std::string error; native.omt_loaded=false;
        if (!native.load_omt_instance(input,error)) throw std::runtime_error(error);
    }
};
nia::OmtOptimalityResult finite(Fixture& f,const std::string& best,double seconds,double bv,
                               bool cutset=false) {
    return nia::prove_omt_optimality(f.native,best,seconds,f.query.string(),cutset,false,
        nullptr,0,false,false,false,false,2.,0
#ifndef NIA_BV_STRATEGY_LEGACY
        ,bv
#endif
    );
}
nia::OmtOptimalitySession session(Fixture& f,double bv) {
    return nia::OmtOptimalitySession(f.native,false,false,0,false,false,false,false,2.,0
#ifndef NIA_BV_STRATEGY_LEGACY
        ,bv
#endif
    );
}
const std::string square_body="VARIABLES 3\n0 I 78\n1 B 62\n2 I 6e61746976652d737175617265\n"
    "LITERALS 4\n1 B 1\n2 L -2 1\n1 1 0 1\n3 L -2 1\n-1 1 0 1\n"
    "4 E 0 2\n1 1 2 1\n-1 1 0 2\nCLAUSES 4\n1 1\n1 2\n1 3\n1 4\n"
    "OBJECTIVE 0 1\n1 1 2 1";
std::string square(bool minimize) {
    return "(declare-const x Int)(declare-const b Bool)(declare-const g Int)"
        "(assert b)(assert (>= x -2))(assert (<= x 2))(assert (= g (* x x)))"
        +std::string(minimize?"(minimize g)":"(maximize g)")+"(check-sat)(exit)";
}
void red(const std::filesystem::path& directory) {
    Fixture f(directory,square(true));
    const auto rng=f.native.mt; const auto assignment=f.native._solution;
    auto r=finite(f,"0",.5,.05);
    require(r.proved && r.status=="proved","strict original square proof completes");
    require(read(f.source)==f.text && f.native.mt==rng && f.native._solution==assignment,"source/RNG unchanged");
    std::cout<<"red-public-result status="<<r.status<<" proved="<<r.proved<<std::endl;
}
void models(const std::filesystem::path& directory) {
    for (bool minimize:{true,false}) for (bool persistent:{false,true}) {
        Fixture f(directory/(std::string(minimize?"min":"max")+(persistent?"-session":"-finite")),square(minimize),"g",minimize?"min":"max");
        f.load(square_body); const auto rng=f.native.mt; const auto assignment=f.native._solution;
        nia::OmtOptimalityResult candidate,proof;
        if (persistent) {
            auto s=session(f,.05); candidate=s.run(minimize?"1":"0",.5,f.query.string());
            proof=s.run(minimize?"0":"4",.5,f.query.string());
            require(s.starts()==2,"new bound starts one new worker");
        } else {
            candidate=finite(f,minimize?"1":"0",.5,.05); proof=finite(f,minimize?"0":"4",.5,.05);
        }
        require(candidate.status=="sat" && candidate.candidate_available && !candidate.proved,"complete native SAT bridge: "+candidate.reason);
        if (candidate.candidate_available) {
            const mpz_class value(candidate.candidate_value,10);
            require(candidate.candidate.size()==3 && candidate.candidate[1]==1 &&
                candidate.candidate[2]==candidate.candidate[0]*candidate.candidate[0] &&
                value==mpz_class(nia::format_omt_integer(candidate.candidate[2])) &&
                (minimize?value==0:value>0 && value<=4),"GMP square/Bool/true original objective");
        }
        require(proof.proved && proof.status=="proved","endpoint no-better original bound");
        require(read(f.source)==f.text && f.native.mt==rng && f.native._solution==assignment,"full min/max source/RNG unchanged");
        require(read(f.query).find(minimize?"(< g 0)":"(> g 4)")!=std::string::npos,"saved bound has original objective/direction");
    }
}
void guards(const std::filesystem::path& directory) {
#ifndef NIA_BV_STRATEGY_LEGACY
    Fixture f(directory,square(true)); const auto rng=f.native.mt;
    for (double bv:{-.1,.0001,1200.1,std::numeric_limits<double>::quiet_NaN(),std::numeric_limits<double>::infinity()}) {
        const auto r=finite(f,"0",.5,bv);
        require(r.status=="error" && !r.proved && !r.candidate_available && !r.queries,"finite API invalid guard");
        bool rejected=false; try { auto s=session(f,bv); } catch (const std::invalid_argument&) { rejected=true; }
        require(rejected,"session API invalid guard");
        require(!std::filesystem::exists(f.query),"invalid API has no query output");
    }
    f.query=f.source; const auto r=finite(f,"0",.5,.05);
    require(r.status=="error" && !r.proved && read(f.source)==f.text && f.native.mt==rng,"source same-path protection");
#endif
}
void query_identity(const std::filesystem::path& directory) {
    z3::context ctx; z3::optimize parser(ctx);
    parser.from_string(read(directory/"original.smt2").c_str());
    auto expected=parser.assertions(); expected.push_back(parser.objectives()[0]<0);
    const auto actual=ctx.parse_string(read(directory/"strict.smt2").c_str());
    require(actual.size()==expected.size(),"saved query has every original assertion and exactly the strict bound");
    // SMT2's negative numeral atom and printer's unary-minus numeral can have
    // different AST nodes. Normalize this exact integer-literal case only.
    auto integer=[](const z3::expr& expression,mpz_class& value) {
        if(expression.is_int() && expression.is_numeral()) {
            value=mpz_class(Z3_get_numeral_string(expression.ctx(),expression),10);return true;
        }
        if(expression.is_app() && expression.decl().decl_kind()==Z3_OP_UMINUS &&
           expression.arg(0).is_int() && expression.arg(0).is_numeral()) {
            value=-mpz_class(Z3_get_numeral_string(expression.ctx(),expression.arg(0)),10);return true;
        }
        return false;
    };
    std::function<bool(const z3::expr&,const z3::expr&)> same=[&](const auto& left,const auto& right) {
        if(z3::eq(left,right))return true;
        mpz_class a,b;if(integer(left,a)&&integer(right,b))return a==b;
        if(!left.is_app() || !right.is_app() || !z3::eq(left.decl(),right.decl()) || left.num_args()!=right.num_args())return false;
        for(unsigned i=0;i<left.num_args();++i)if(!same(left.arg(i),right.arg(i)))return false;
        return true;
    };
    for(unsigned i=0;i<actual.size() && i<expected.size();++i)
        require(same(actual[i],expected[i]),"saved query exact AST identity modulo integer literals/let expansion; actual="+
            actual[i].to_string()+" expected="+expected[i].to_string());
}
void off_and_aux(const std::filesystem::path& directory) {
    Fixture f(directory,square(true));
    const auto off=finite(f,"0",.5,0); const auto original_query=read(f.query);
    require(off.proved,"default0 old proof");
    const auto legacy=nia::prove_omt_optimality(f.native,"0",.5,f.query.string());
    require(legacy.proved && read(f.query)==original_query,"omitted and explicit0 preserve exact query");
    // Source-slice can decide the original strict query before main entry.
    const auto aux=nia::prove_omt_optimality(f.native,"0",.5,f.query.string(),false,false,nullptr,0,
        false,false,false,true,.1,0
#ifndef NIA_BV_STRATEGY_LEGACY
        ,.05
#endif
    );
    require(aux.proved,"auxiliary determinate result retains original proof");
    query_identity(directory);
}
std::string dense_source() {
    std::mt19937_64 random(20261030); std::ostringstream out;
    out<<"(declare-const g Int)"; for(unsigned j=0;j<64;++j)out<<"(declare-const x"<<j<<" Int)";
    out<<"(assert (= g (+";
    for(unsigned i=0;i<64;++i) {
        std::ostringstream form; form<<"(+";
        for(unsigned j=0;j<64;++j) {
            mpz_class coefficient=1;
            for(unsigned k=0;k<256;k+=64){coefficient<<=64;coefficient+=mpz_class(std::to_string(random()));}
            form<<" (* "<<coefficient.get_str()<<" x"<<j<<")";
        }
        form<<')'; out<<" (* "<<form.str()<<' '<<form.str()<<")";
    }
    out<<")))(minimize g)"; return out.str();
}
void timer_and_pause(const std::filesystem::path& directory) {
    Fixture f(directory,dense_source());
    // This same real generated formula has a fully explicit all-zero witness.
    z3::context ctx; z3::optimize parser(ctx); parser.from_string(f.text.c_str());
    z3::model witness(ctx); auto zero=ctx.int_val(0); auto g=ctx.int_const("g").decl(); witness.add_const_interp(g,zero);
    for(unsigned i=0;i<64;++i){auto d=ctx.int_const(("x"+std::to_string(i)).c_str()).decl();witness.add_const_interp(d,zero);}
    for(const auto& hard:parser.assertions())require(witness.eval(hard,true).is_true(),"full dense original witness");
    z3::goal goal(ctx); goal.add(parser.assertions()); goal.add(ctx.int_const("g")<0);
    bool canceled=false;
    try { (void)z3::try_for(z3::tactic(ctx,"qfnia-omt-bv"),1)(goal); }
    catch(const z3::exception& e){std::cerr<<"bv-test raw_timer_exception="<<e.msg()<<std::endl;canceled=std::string(e.msg()).find("canceled")!=std::string::npos;}
    require(canceled,"real 1ms local tactic timer actually cancels, not just width failure");
    auto s=session(f,.001);
    const auto first=s.run("0",.03,f.query.string());
    require(first.status=="budget" && s.paused() && s.starts()==1,"real short persistent worker pauses");
    std::this_thread::sleep_for(std::chrono::milliseconds(20));
    const auto resumed=s.run("0",1.5,f.query.string());
    require(resumed.proved && s.starts()==1 && s.resumes()==1,"same worker resumes to complete default proof");
    require(read(f.source)==f.text,"dense source bytes preserved");
    const auto late=finite(f,"0",.002,120);
    require(late.status=="budget" && !late.proved && !late.candidate_available,"finite outer deadline dominates long K");
}
void width_and_lia(const std::filesystem::path& directory) {
    const mpz_class base=mpz_class(1)<<65; const std::string b=base.get_str(),u=mpz_class(base+1).get_str();
    Fixture f(directory/"width","(declare-const x Int)(declare-const y Int)"
        "(assert (>= x "+b+"))(assert (<= x "+u+"))(assert (>= y "+b+"))(assert (<= y "+u+"))"
        "(assert (>= (* x y) "+mpz_class(base*base).get_str()+"))(minimize (+ x y))","(+ x y)");
    f.load("VARIABLES 2\n0 I 78\n1 I 79\nLITERALS 0\nCLAUSES 0\nOBJECTIVE 0 2\n1 1 0 1\n1 1 1 1");
    const auto r=finite(f,mpz_class(base*2+2).get_str(),.75,.1);
    require(r.status=="sat" && r.candidate_available && !r.proved,"64width failure restores goal to default SAT: "+r.reason);
    const std::string big="1267650600228229401496703205376",top="1267650600228229401496703205377";
    Fixture linear(directory/"lia","(declare-const x Int)(declare-const b Bool)(assert b)"
        "(assert (>= x "+big+"))(assert (<= x "+top+"))(maximize x)","x","max");
    linear.load("VARIABLES 2\n0 I 78\n1 B 62\nLITERALS 1\n1 B 1\nCLAUSES 1\n1 1\nOBJECTIVE 0 1\n1 1 0 1");
    const auto v=finite(linear,big,.5,.001);
    require(v.status=="sat" && v.candidate_available && v.candidate_value==top && v.candidate[1]==1,"large exact original max/Bool LIA source/native bridge");
}
}
int main(int argc,char** argv) {
    if(argc!=4)return 2;
    Dl_info info{}; require(dladdr(reinterpret_cast<void*>(&Z3_get_version),&info) && info.dli_fname &&
        std::filesystem::equivalent(info.dli_fname,argv[3]),"exact build010 runtime library");
    const std::string mode=argv[1];const std::filesystem::path out=argv[2];
    try {
        if(mode=="red")red(out); else if(mode=="models")models(out);
        else if(mode=="guards")guards(out);else if(mode=="off-aux")off_and_aux(out);
        else if(mode=="query-identity")query_identity(out);
        else if(mode=="timer-pause")timer_and_pause(out);else if(mode=="width-lia")width_and_lia(out);else return 2;
    } catch(const std::exception& e){require(false,e.what());}
    if(!failures)std::cout<<"PASS bv-strategy mode="<<mode<<std::endl;
    return failures?1:0;
}
