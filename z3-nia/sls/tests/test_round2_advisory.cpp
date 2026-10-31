#include "../src/nia_ls.h"
#include "../src/nia_omt_optimality.h"
#include "../src/nia_omt_input.h"
#include <z3++.h>
#include <chrono>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <map>
#include <sstream>
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
                                 unsigned mode,const std::vector<__int128_t>* hint=nullptr,bool slice=false){
        auto result=nia::prove_omt_optimality(native,best,seconds,output.string(),false,false,hint,mode,false,true,false,slice);
        check(read(source)==original,"source file remains immutable");return result;
    }
    void load_native(const std::string& body){
        auto hex=[](const std::string& text){std::string result;const char* digits="0123456789abcdef";
            for(unsigned char ch:text){result+=digits[ch>>4];result+=digits[ch&15];}return result;};
        std::istringstream input("NIA_LS_OMT 1\nSTATUS ready\nSOURCE "+hex(source.string())
            +"\nORIGINAL_OBJECTIVE "+hex(native.omt_original_objective)+"\nDIRECTION "+native.omt_direction
            +"\n"+body+"\nEND\n");
        std::string error;native.omt_loaded=false;
        if(!native.load_omt_instance(input,error))throw std::runtime_error("native fixture: "+error);
    }
};
void original_hanoi_query_after_supplemental(const std::filesystem::path& fixtures,
        const std::filesystem::path& directory,bool supplemental){
    const auto source=fixtures/"smt_exchange_context_hanoi.smt2";
    const auto original=read(source);
    nia::ls_solver native{19,0,true};
    std::ifstream input(fixtures/"smt_exchange_context_hanoi.nia");std::string error;
    if(!native.load_omt_instance(input,error))throw std::runtime_error("Hanoi native fixture: "+error);
    // The fixture has no machine-specific path. Use this exact local source.
    native.omt_source=source.string();
    std::ifstream model(fixtures/"smt_exchange_context_hanoi.tsv");
    std::map<std::string,std::string> values;std::string name,value;
    while(model>>name>>value)values.emplace(name,value);
    std::vector<__int128_t> hint(native._num_vars);size_t integers=0;
    for(size_t i=0;i<native._num_vars;++i){
        const auto found=values.find(native._vars[i].var_name);
        if(found==values.end())throw std::runtime_error("missing real Hanoi incumbent coordinate");
        if(native._vars[i].is_nia){
            ++integers;
            if(!nia::parse_omt_integer(found->second,hint[i]))throw std::runtime_error("invalid Hanoi integer hint");
        }else hint[i]=found->second=="true"?1:-1;
    }
    check(native._num_vars==41&&integers==37&&values.at("lam9n3")=="0",
          "use all 41 validated Hanoi coordinates, including the actual 37 integer hints");
    const auto output=directory/"hanoi.smt2";
    const auto answer=nia::prove_omt_optimality(native,"0",13,output.string(),true,
        supplemental,supplemental?&hint:nullptr,supplemental?2u:0u,false,supplemental);
    check(answer.proved&&answer.status=="proved",
          "real Hanoi strict query must prove within the shared 13-second allowance: "+answer.status+" / "+answer.reason);
    check(!answer.proved||answer.seconds<13,"Hanoi proof uses the original finite total allowance");
    if(supplemental)check(answer.reason.find("fallback default")!=std::string::npos,
          "real hints exercise an inconclusive preflight followed by the original default route: "+answer.reason);
    check(read(output).find("(< lam9n3 0)")!=std::string::npos,"real original objective strict bound is retained");
    check(read(source)==original,"real Hanoi source remains immutable");
    std::cout<<"hanoi mode="<<(supplemental?"supplemental":"default")<<" integers="<<integers
        <<" status="<<answer.status<<" proved="<<answer.proved<<" seconds="<<answer.seconds
        <<" reason="<<answer.reason<<std::endl;
}
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
void supplemental_sat_preserves_large_boolean_models(const std::filesystem::path& directory){
    const std::string lower="1267650600228229401496703205376",upper="1267650600228229401496703205377";
    for(bool minimize:{true,false})for(unsigned mode:{1u,2u,3u}){
        const auto source=directory/("wide-bool-"+std::to_string(minimize)+"-"+std::to_string(mode)+".smt2");
        std::ofstream(source)<<"(declare-const x Int)(declare-const b Bool)(assert b)"
            <<"(assert (>= x "<<lower<<"))(assert (<= x "<<upper<<"))("
            <<(minimize?"minimize":"maximize")<<" x)(check-sat)\n";
        Fixture f(source,"x");f.native.omt_direction=minimize?"min":"max";
        f.load_native("VARIABLES 2\n0 I 78\n1 B 62\nLITERALS 3\n1 B 1\n2 L "+lower
            +" 1\n-1 1 0 1\n3 L -"+upper+" 1\n1 1 0 1\nCLAUSES 3\n1 1\n1 2\n1 3\nOBJECTIVE 0 1\n1 1 0 1");
        __int128_t coordinate;
        if(!nia::parse_omt_integer(minimize?upper:lower,coordinate))throw std::runtime_error("wide fixture coordinate");
        const std::vector<__int128_t> hint{coordinate,1};
        const auto result=nia::prove_omt_optimality(f.native,minimize?upper:lower,2,
            (source.string()+".query"),false,mode==3,&hint,mode==3?0u:mode,false,true);
        check(result.status=="sat"&&!result.proved&&result.candidate_available&&result.candidate.size()==2,
              "supplemental SAT preserves a complete min/max integer and Boolean source/native model: "+result.reason);
        if(mode==3)check(result.reason.find("convex_outer_approximation")!=std::string::npos,
            "SAT model is returned by the isolated convex channel: "+result.reason);
        else check(result.reason.find(mode==1?"smt_exchange backend":"smt_exchange hints")!=std::string::npos
            &&result.reason.find("fallback default")==std::string::npos,
            "SAT model is returned by the requested isolated SMT channel without default fallback: "+result.reason);
        if(result.candidate_available)check(result.candidate_value==(minimize?lower:upper)
            &&nia::format_omt_integer(result.candidate[0])==(minimize?lower:upper)&&result.candidate[1]==1,
            "translated source model keeps exact coordinates above int64, objective direction and Boolean truth");
        check(read(source)==f.original,"SAT model transfer leaves the original source immutable");
    }
}
void objective_slice_keeps_native_bridge(const std::filesystem::path& directory){
    const std::string lower="1267650600228229401496703205376",upper="1267650600228229401496703205377";
    for(bool minimize:{true,false})for(bool resume:{false,true}){
        const auto tag=std::string(minimize?"min":"max")+(resume?"-resumed":"-finite");
        const auto source=directory/("slice-"+tag+".smt2");
        {
            std::ofstream out(source);
            out<<"(declare-const x Int)(declare-const b Bool)(assert b)"
               <<"(assert (>= x "<<lower<<"))(assert (<= x "<<upper<<"))";
            for(unsigned i=0;i<24;++i)
                out<<"(declare-const unused"<<i<<" Int)(assert (>= unused"<<i<<" 0))";
            out<<"("<<(minimize?"minimize":"maximize")<<" x)(check-sat)\n";
        }
        Fixture f(source,"x");f.native.omt_direction=minimize?"min":"max";
        f.load_native("VARIABLES 2\n0 I 78\n1 B 62\nLITERALS 3\n1 B 1\n2 L "+lower
            +" 1\n-1 1 0 1\n3 L -"+upper+" 1\n1 1 0 1\nCLAUSES 3\n1 1\n1 2\n1 3\nOBJECTIVE 0 1\n1 1 0 1");
        const auto rng=f.native.mt;const auto previous=f.native._solution;
        __int128_t initial=0;
        check(nia::parse_omt_integer(minimize?upper:lower,initial),"wide incumbent parses exactly");
        const std::vector<__int128_t> hint{initial,1};
        const auto output=directory/("slice-"+tag+"-query.smt2");
        nia::OmtOptimalityResult result;
        if(resume){
            nia::OmtOptimalitySession session(f.native,false,false,0,false,false,false,true);
            result=session.run(minimize?upper:lower,2,output.string(),&hint);
        }else result=f.run(minimize?upper:lower,2,output,0,&hint,true);
        check(result.status=="sat"&&!result.proved&&result.candidate_available,
              "slice returns a complete source/native counterexample through the existing bridge: "+result.reason);
        check(result.reason.find("objective_slice")!=std::string::npos,
              "test uses the requested source-slice path rather than default fallback: "+result.reason);
        if(result.candidate_available)
            check(result.candidate.size()==2&&result.candidate_value==(minimize?lower:upper)
                &&nia::format_omt_integer(result.candidate[0])==(minimize?lower:upper)&&result.candidate[1]==1,
                "slice translation preserves exact large integer improvement and Boolean feasibility");
        check(f.native.mt==rng&&f.native._solution==previous,"slice does not consume caller RNG or mutate native assignment");
        check(read(source)==f.original,"slice leaves original source bytes immutable");
    }
}
void persistent_worker_resumes_into_default_fallback(const std::filesystem::path& fixture,const std::filesystem::path& directory){
    Fixture f(fixture,"lam3n1");const auto output=directory/"persistent.smt2";
    nia::OmtOptimalitySession session(f.native,false,false,2,false,true);
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
    if(argc==5&&std::string(argv[1])=="--context-regression"){
        const std::filesystem::path fixtures=argv[2],directory=argv[3];std::filesystem::create_directories(directory);
        original_hanoi_query_after_supplemental(fixtures,directory,std::string(argv[4])=="supplemental");
        return failures?1:0;
    }
    if(argc!=3)throw std::runtime_error("usage: regression FIXTURE DIRECTORY");
    const std::filesystem::path fixture=argv[1],directory=argv[2];std::filesystem::create_directories(directory);
    incomplete_nia_falls_back(fixture,directory);sat_preflight_keeps_native_bridge(directory);
    supplemental_sat_preserves_large_boolean_models(directory);
    objective_slice_keeps_native_bridge(directory);
    persistent_worker_resumes_into_default_fallback(fixture,directory);
    finite_allowance_does_not_add_five_seconds(fixture,directory);
    if(!failures)std::cout<<"PASS SMT exchange preflight fallback\n";
    return failures?1:0;
}
