#include "../src/nia_omt_objective_slice.h"
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
void check(bool value,const std::string& why){if(!value){++failures;std::cerr<<"FAIL: "<<why<<'\n';}}
std::string padding(unsigned count=24,bool false_by_default=false){
    std::ostringstream text;
    for(unsigned i=0;i<count;++i)text<<"(declare-const pad"<<i<<" Int)(assert ("<<(false_by_default?"=":">=")<<" pad"<<i<<' '<<(false_by_default?i+17:0)<<"))";
    return text.str();
}
std::string certificate(){return directory+"/certificate-"+std::to_string(++certificates)+".json";}
struct Fixture {
    z3::context context;
    z3::expr_vector hard{context};
    z3::expr objective{context.int_val(0)};
    std::string source;
    Fixture(std::string text):source(std::move(text)){
        z3::optimize parser(context);parser.from_string(source.c_str());hard=parser.assertions();objective=parser.objectives()[0];
    }
    nia::OmtObjectiveSliceResult run(const std::string& best,bool minimize=true,double seconds=2,const std::string& output={}){
        if(!output.empty()&&!std::filesystem::exists(output+".source.smt2")){
            auto text=source;
            if(!minimize){auto at=text.find("(minimize");if(at!=std::string::npos)text.replace(at,9,"(maximize");}
            std::ofstream(output+".source.smt2")<<text<<"\n(check-sat)\n";
        }
        return nia::solve_omt_objective_slice(context,hard,objective,minimize,best,seconds,output);
    }
    void sat(const nia::OmtObjectiveSliceResult& result,const std::string& best,bool minimize=true){
        check(result.applicable&&result.answer=="sat"&&bool(result.source_model),"full source SAT model is returned: "+result.reason);
        if(!result.source_model)return;
        for(const auto& constraint:hard)check(result.source_model->eval(constraint,true).is_true(),"returned model satisfies every original hard assertion");
        const auto strict=minimize?objective<context.int_val(best.c_str()):objective>context.int_val(best.c_str());
        check(result.source_model->eval(strict,true).is_true(),"returned model strictly improves the exact original objective");
        check(result.certificate_json.empty(),"SAT does not publish an UNSAT certificate");
    }
    void unsat(const nia::OmtObjectiveSliceResult& result,const std::string& path){
        check(result.applicable&&result.answer=="unsat"&&!result.source_model,"source subset proves global strict bound UNSAT: "+result.reason);
        check(result.queries>0&&!result.certificate_json.empty(),"UNSAT has an actual solver query and semantic certificate");
        std::ifstream file(path);const std::string saved((std::istreambuf_iterator<char>(file)),{});
        check(!saved.empty()&&saved==result.certificate_json,"UNSAT is published only after saving its complete certificate");
    }
};
void seed_unsat_and_spurious_sat(){
    Fixture seed("(declare-const x Int)(assert (>= x 0))"+padding()+"(minimize x)");
    auto path=certificate();auto proof=seed.run("0",true,2,path);seed.unsat(proof,path);
    check(proof.queries==1&&proof.refinements==0&&proof.initial_assertions==1,"seed lower bound proves UNSAT without expanding unrelated constraints");
    Fixture alias("(declare-const x Int)(declare-const cost Int)(assert (= cost x))(assert (>= x 5))"+padding()+"(minimize cost)");
    path=certificate();proof=alias.run("5",true,2,path);alias.unsat(proof,path);
    check(proof.initial_assertions==1&&proof.refinements>=1&&proof.queries>=2,"spurious subset SAT refines the actual false omitted source constraint without initial closure");
    check(proof.selected_assertions==2,"refinement does not eagerly add unrelated true components");
}
void full_source_sat_and_boolean_components(){
    Fixture f("(declare-const x Int)(declare-const cost Int)(declare-const b Bool)(declare-const hard_only Int)"
              "(assert (= cost x))(assert (or (and b (= x 2)) (and (not b) (= x 3))))"
              "(assert b)(assert (= hard_only 177))"+padding()+"(minimize cost)");
    auto result=f.run("10");f.sat(result,"10");
    check(result.refinements>0,"full SAT is validated after real false-component feedback");
    if(result.source_model)check(result.source_model->eval(f.context.int_const("hard_only"),true).get_numeral_int()==177,"hard-only declaration is present in the complete source model");
    Fixture disjunction("(declare-const x Int)(assert (or (= x 0) (= x 2)))"+padding()+"(minimize x)");
    auto disjunction_result=disjunction.run("1");disjunction.sat(disjunction_result,"1");
    check(disjunction_result.total_assertions==25&&disjunction_result.initial_assertions==1,"OR is indivisible under safe top-level flattening");
    Fixture split("(declare-const x Int)(declare-const p Bool)(declare-const q Bool)"
                  "(assert (and (>= x 0) (and (or p q) (not (and p q))) (=> p q)))"+padding()+"(minimize x)");
    auto path=certificate();auto proof=split.run("0",true,2,path);split.unsat(proof,path);
    check(proof.total_assertions==28&&proof.initial_assertions==1,"only recursive top-level ANDs split; OR, NOT and implication remain intact");
}
void maximize_and_exact_wide_best(){
    Fixture maximum("(declare-const x Int)(assert (<= x 0))"+padding()+"(minimize x)");
    auto path=certificate();auto proof=maximum.run("0",false,2,path);maximum.unsat(proof,path);
    auto result=maximum.run("-1",false);maximum.sat(result,"-1",false);
    const std::string huge="1393796574908163946345982392040522594123776";
    Fixture wide("(declare-const x Int)(assert (= x "+huge+"))"+padding()+"(minimize x)");
    path=certificate();proof=wide.run(huge,true,2,path);wide.unsat(proof,path);
    auto wide_result=wide.run("1393796574908163946345982392040522594123777");wide.sat(wide_result,"1393796574908163946345982392040522594123777");
}
void original_nonlinear_source_and_constant_objective(){
    Fixture nonlinear("(declare-const z Int)(declare-const x Int)(declare-const a Int)(declare-const b Int)"
                      "(declare-const c Int)(declare-const d Int)(assert (= z (* x x)))"
                      "(assert (and (= (* a b) 0) (= (* c d) 0)))"+padding()+"(minimize z)");
    auto path=certificate();auto proof=nonlinear.run("0",true,2,path);nonlinear.unsat(proof,path);
    check(proof.initial_assertions==1&&proof.queries==1,"original nonlinear objective alias proves UNSAT without linearizing unrelated nonlinear hard constraints");
    Fixture constant(padding()+"(minimize 7)");
    path=certificate();auto constant_proof=constant.run("7",true,2,path);constant.unsat(constant_proof,path);
    check(constant_proof.initial_assertions==0&&constant_proof.selected_assertions==0,"a constant original strict bound can prove UNSAT without additional assertions");
}
void stable_false_refinement_order(){
    Fixture f("(declare-const cost Int)(declare-const x Int)(declare-const z Int)"
              "(assert (= z 17))(assert (= cost x))(assert (>= x 5))"+padding(24,true)+"(minimize cost)");
    auto path=certificate();auto result=f.run("5",true,2,path);f.unsat(result,path);
    // x>=5 shares the seed variables and must precede earlier disconnected z=17.
    check(result.certificate_json.find("\"selected_indices\":[1,2,0,3,4,5,6,7,8]")!=std::string::npos,
          "up to eight actual false components favor active-variable overlap, then stable source order");
    check(result.refinements==1&&result.selected_assertions==9,"one counterexample adds at most eight violated assertions");
}
std::string chain_source(unsigned length){
    std::ostringstream source;
    for(unsigned i=0;i<=length;++i)source<<"(declare-const x"<<i<<" Int)";
    for(unsigned i=0;i<length;++i)source<<"(assert (= x"<<i<<" x"<<i+1<<"))";
    source<<"(assert (>= x"<<length<<" 0))(minimize x0)";
    return source.str();
}
void forty_link_chain(){
    Fixture chain(chain_source(40));const auto path=certificate();auto result=chain.run("0",true,8,path);chain.unsat(result,path);
    check(result.initial_assertions==1&&result.refinements==40&&result.queries==41&&result.selected_assertions==41,
          "40-link source chain reaches the terminal original lower bound through exactly 40 actual false refinements");
    std::cout<<"chain40 answer="<<result.answer<<" queries="<<result.queries<<" refinements="<<result.refinements
             <<" selected="<<result.selected_assertions<<" reason="<<result.reason<<'\n';
}
void generic_gates_and_limits(){
    Fixture ratio("(declare-const x Int)(assert (>= x 0))(minimize x)");
    auto result=ratio.run("0");check(!result.applicable&&result.answer=="unknown"&&result.queries==0,"initial subset exceeding one-half full AST work is skipped");
    std::ostringstream large;large<<"(declare-const x Int)(assert (or";
    for(unsigned i=0;i<4500;++i)large<<" (= x "<<i<<')';
    large<<"))"<<padding(6000)<<"(minimize x)";
    Fixture cap(large.str());result=cap.run("0",true,5);
    check(!result.applicable&&result.queries==0&&result.initial_ast_nodes>8192,"initial AST cap rejects a small-ratio but oversized seed");
    Fixture exhausted(chain_source(257));
    result=exhausted.run("0",true,20);
    check(result.applicable&&result.answer=="unknown"&&!result.source_model&&result.refinements==256&&result.queries==257,
          "256 refinement rounds conservatively stop without returning an incomplete source model");
    Fixture too_many("(declare-const x Int)(assert (>= x 0))"+padding(65537)+"(minimize x)");
    result=too_many.run("0",true,5);
    check(!result.applicable&&result.answer=="unknown"&&result.queries==0,"flattened component count has a finite guard");
}
void deadlines_inputs_and_output_protection(){
    Fixture f("(declare-const x Int)(assert (>= x 0))"+padding()+"(minimize x)");
    for(double seconds:{0.,-1.,1e-300,std::numeric_limits<double>::quiet_NaN(),std::numeric_limits<double>::infinity()}){
        auto result=f.run("0",true,seconds);check(result.answer=="unknown"&&result.queries==0&&!result.source_model&&result.certificate_json.empty(),"nonpositive, expired or nonfinite allowance cannot publish an answer");
    }
    for(const std::string best:{"","0.5","--1","nan","0) (assert false)"}){
        auto result=f.run(best);check(!result.applicable&&result.answer=="unknown"&&result.queries==0,"invalid exact decimal incumbent is rejected before querying");
    }
    auto failed=f.run("0",true,2,directory+"/missing/proof.json");
    check(failed.answer=="unknown"&&!failed.source_model&&failed.certificate_json.empty()&&failed.queries>0,"failed certificate output suppresses an UNSAT publication");
    auto once=certificate();auto first=f.run("0",true,2,once);f.unsat(first,once);
    auto second=f.run("0",true,2,once);
    std::ifstream again(once);std::string unchanged((std::istreambuf_iterator<char>(again)),{});
    check(second.answer=="unknown"&&unchanged==first.certificate_json,"second publication to the same output path refuses to overwrite the first proof");
    const auto protected_path=directory+"/protected-source.smt2";
    {std::ofstream file(protected_path);file<<f.source;}
    auto protected_result=f.run("0",true,2,protected_path);
    std::ifstream original(protected_path);std::string preserved((std::istreambuf_iterator<char>(original)),{});
    check(protected_result.answer=="unknown"&&preserved==f.source,"certificate output cannot truncate an existing source or any existing caller file");
    const auto symlink=directory+"/symlink-output.json";std::filesystem::create_symlink(protected_path,symlink);
    auto linked=f.run("0",true,2,symlink);check(linked.answer=="unknown"&&std::filesystem::file_size(protected_path)==f.source.size(),"certificate output refuses a symlink to caller source");
    const auto directory_output=directory+"/existing-directory";std::filesystem::create_directory(directory_output);
    auto directory_result=f.run("0",true,2,directory_output);
    check(directory_result.answer=="unknown"&&std::filesystem::is_directory(directory_output),"certificate output preserves an existing directory");
    z3::context other;z3::expr_vector wrong(other);
    auto cross=nia::solve_omt_objective_slice(f.context,wrong,f.objective,true,"0",2);
    check(!cross.applicable&&cross.answer=="unknown"&&cross.queries==0,"cross-context inputs reject before Z3 operations");
    Fixture quantified("(declare-const x Int)(declare-fun f (Int) Int)(assert (forall ((y Int)) (= (f y) y)))"+padding()+"(minimize x)");
    auto uncertain=quantified.run("1");
    check(uncertain.answer=="unknown"&&!uncertain.source_model,"unevaluable or unsupported quantified source does not return a source model");
}
}
int main(int argc,char** argv){
    directory=argc>1?argv[1]:"/tmp/omt-objective-slice-tests";std::filesystem::create_directories(directory);
    const std::string only=argc>2?argv[2]:"all";
    if(only=="publication")deadlines_inputs_and_output_protection();
    else if(only=="chain40")forty_link_chain();
    else {
        check(only=="all","unknown focused test selection");
        seed_unsat_and_spurious_sat();full_source_sat_and_boolean_components();maximize_and_exact_wide_best();original_nonlinear_source_and_constant_objective();stable_false_refinement_order();
        forty_link_chain();generic_gates_and_limits();deadlines_inputs_and_output_protection();
    }
    if(!failures)std::cout<<"PASS OMT objective slice\n";
    return failures?1:0;
}
