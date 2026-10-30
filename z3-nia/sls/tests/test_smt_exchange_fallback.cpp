#include "../src/nia_ls.h"
#include "../src/nia_omt_optimality.h"
#include <z3++.h>
#include <chrono>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
#include <unistd.h>

namespace {
int failures=0;
void check(bool okay,const std::string& why){if(!okay){++failures;std::cerr<<"FAIL: "<<why<<'\n';}}
std::string read(const std::filesystem::path& file){std::ifstream in(file);return std::string(std::istreambuf_iterator<char>(in),{});}
struct Fixture {
    nia::ls_solver native{19,0,true};
    std::filesystem::path source;
    std::string original;
    Fixture(std::filesystem::path input,const std::string& objective):source(std::move(input)),original(read(source)){
        native.omt_loaded=true;native.omt_source=source.string();native.omt_direction="min";native.omt_original_objective=objective;
        native._num_vars=native._num_lits=native._num_clauses=0;
    }
    nia::OmtOptimalityResult run(const std::string& best,double seconds,const std::filesystem::path& output,
                                 unsigned mode,const std::vector<__int128_t>* hint=nullptr){
        auto result=nia::prove_omt_optimality(native,best,seconds,output.string(),false,false,hint,mode);
        check(read(source)==original,"source file remains immutable");return result;
    }
};
void incomplete_nia_falls_back(const std::filesystem::path& fixture,const std::filesystem::path& directory){
    Fixture f(fixture,"lam3n1");
    const auto control_file=directory/"default.smt2";
    const auto control=f.run("0",7,control_file,0);
    check(control.proved&&control.status=="proved","original default source query proves this NIA fixture: "+control.reason);
    std::cout<<"default-control proved="<<control.proved<<" seconds="<<control.seconds<<std::endl;
    const auto original_query=read(control_file);
    check(original_query.find("(< lam3n1 0)")!=std::string::npos,"strict original objective bound is retained");
    for(unsigned mode:{1u,2u}){
        const auto output=directory/(mode==1?"backend.smt2":"hints.smt2");
        const auto answer=f.run("0",7,output,mode);
        check(answer.proved&&answer.status=="proved","inconclusive SMT preflight must fall back to the preserved default solver: "+answer.reason);
        check(answer.reason.find("fallback default")!=std::string::npos,"result records a real fallback rather than silently replacing the backend: "+answer.reason);
        check(answer.seconds<7,"preflight and fallback share one complete finite allowance");
        check(read(output)==original_query,"backend/hints retain the identical full source hard query and strict bound");
        std::cout<<"fallback mode="<<mode<<" proved="<<answer.proved<<" seconds="<<answer.seconds<<" reason="<<answer.reason<<std::endl;
    }
}
void sat_preflight_keeps_native_bridge(const std::filesystem::path& directory){
    const auto source=directory/"sat-source.smt2";
    std::ofstream(source)<<"(declare-const x Int)(assert (>= x 0))(assert (<= x 10))(minimize x)(check-sat)\n";
    Fixture f(source,"x");auto& s=f.native;
    s._num_vars=1;s._vars.resize(1);s._vars[0].is_nia=true;s._vars[0].var_name="x";
    nia::term monomial{};monomial.var_epxs.emplace_back(0);s._terms.push_back(monomial);
    s.omt_objective_terms.emplace_back(0,1);s._num_lits=2;s._lits.resize(3);
    s._lits[1].is_nia_lit=true;s._lits[1].key=0;s._lits[1].coff_terms.emplace_back(0,-1);
    s._lits[2].is_nia_lit=true;s._lits[2].key=-10;s._lits[2].coff_terms.emplace_back(0,1);
    nia::clause low{},high{};low.literals={1};high.literals={2};s._clauses={low,high};s._num_clauses=2;
    const auto rng=s.mt;const auto solution=s._solution;const std::vector<__int128_t> hint{10};
    for(unsigned mode:{1u,2u}){
        const auto output=directory/("sat-"+std::to_string(mode)+".smt2");auto result=f.run("10",2,output,mode,&hint);
        check(result.status=="sat"&&!result.proved&&result.candidate_available&&result.candidate.size()==1,
              "determinate SAT preflight returns a complete source/native-validated assignment: "+result.reason);
        if(result.candidate_available)check(result.candidate[0]>=0&&result.candidate[0]<10,"returned native candidate obeys the full hard formula and improves");
        check(read(output).find("(assert (= x 10))")==std::string::npos,"incumbent hint is never a hard coordinate equality");
        const std::vector<__int128_t> zero_hint{0};
        const auto optimum=f.run("0",2,directory/("known-unsat-"+std::to_string(mode)+".smt2"),mode,&zero_hint);
        check(optimum.proved&&optimum.reason.find("fallback default")==std::string::npos,
              "determinate UNSAT preflight can return directly without running the default fallback");
    }
    check(s.mt==rng&&s._solution==solution,"preflight does not consume source RNG or mutate native assignment");
}
void persistent_worker_resumes_into_default_fallback(const std::filesystem::path& fixture,const std::filesystem::path& directory){
    Fixture f(fixture,"lam3n1");const auto output=directory/"persistent.smt2";
    nia::OmtOptimalitySession session(f.native,false,false,2);
    const auto first=session.run("0",.025,output.string());
    check(first.status=="budget"&&!first.proved&&session.paused()&&session.starts()==1,
          "short persistent slice pauses its source/SMT preflight worker");
    const auto proof=session.run("0",7,output.string());
    check(proof.proved&&proof.reason.find("fallback default")!=std::string::npos,
          "same persistent worker falls back from inconclusive SMT with an infinite internal default budget: "+proof.reason);
    check(session.starts()==1&&session.resumes()==1&&!session.paused(),"fallback resumes the original fork epoch rather than restarting its query");
    check(read(output)==read(directory/"default.smt2"),"persistent fallback preserves the complete original strict source query");
    std::cout<<"persistent-fallback starts="<<session.starts()<<" resumes="<<session.resumes()<<" proved="<<proof.proved
             <<" seconds="<<proof.seconds<<std::endl;
}
void finite_allowance_does_not_add_five_seconds(const std::filesystem::path& fixture,const std::filesystem::path& directory){
    Fixture f(fixture,"lam3n1");
    for(unsigned mode:{1u,2u}){
        const auto began=std::chrono::steady_clock::now();
        auto answer=f.run("0",.05,directory/("short-"+std::to_string(mode)+".smt2"),mode);
        const double elapsed=std::chrono::duration<double>(std::chrono::steady_clock::now()-began).count();
        check(!answer.proved&&!answer.candidate_available&&answer.status=="budget","expired shared allowance cannot publish a result");
        check(elapsed<.4,"a 50ms total budget cannot acquire an additional five-second preflight/fallback allowance");
        std::cout<<"shared-budget mode="<<mode<<" seconds="<<elapsed<<" status="<<answer.status<<std::endl;
    }
}
}
int main(int argc,char** argv){
    if(argc!=3)throw std::runtime_error("usage: regression FIXTURE DIRECTORY");
    const std::filesystem::path fixture=argv[1],directory=argv[2];std::filesystem::create_directories(directory);
    incomplete_nia_falls_back(fixture,directory);sat_preflight_keeps_native_bridge(directory);
    persistent_worker_resumes_into_default_fallback(fixture,directory);
    finite_allowance_does_not_add_five_seconds(fixture,directory);
    if(!failures)std::cout<<"PASS SMT exchange preflight fallback\n";
    return failures?1:0;
}
