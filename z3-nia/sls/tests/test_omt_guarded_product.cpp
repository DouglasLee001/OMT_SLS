#include "../src/nia_omt_guarded_product.h"
#include <dlfcn.h>
#include <cstdlib>
#include <new>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <limits>
#include <sstream>
#include <stdexcept>
// Enabled only inside the transactional-commit fault test, after fixture setup.
static long fail_allocation_after=-1;
void* operator new(std::size_t bytes){
    if(fail_allocation_after==0)throw std::bad_alloc();
    if(fail_allocation_after>0)--fail_allocation_after;
    if(void* memory=std::malloc(bytes?bytes:1))return memory;
    throw std::bad_alloc();
}
void operator delete(void* memory)noexcept{std::free(memory);}
void operator delete(void* memory,std::size_t)noexcept{std::free(memory);}
using namespace nia;
namespace gp=nia::guarded_product_detail;
static unsigned actual_checks=0,optimize_checks=0,model_requests=0;
extern "C" Z3_lbool __real_Z3_solver_check(Z3_context,Z3_solver);
extern "C" Z3_lbool __wrap_Z3_solver_check(Z3_context c,Z3_solver s){++actual_checks;std::cerr<<"gp actual_check_enter "<<actual_checks<<'\n';return __real_Z3_solver_check(c,s);}
extern "C" Z3_lbool __wrap_Z3_optimize_check(Z3_context,Z3_optimize,unsigned,Z3_ast const*){++optimize_checks;throw std::runtime_error("Optimize.check forbidden");}
extern "C" Z3_model __wrap_Z3_solver_get_model(Z3_context,Z3_solver){++model_requests;throw std::runtime_error("model retrieval forbidden");}
static void require_test(bool okay,const char* message){if(!okay)throw std::runtime_error(message);}
static void save(const std::filesystem::path& path,const std::string& value){std::ofstream out(path);out<<value;out.close();require_test(bool(out),"test artifact write failed");}
static std::string read(const std::filesystem::path& path){std::ifstream in(path);std::ostringstream out;out<<in.rdbuf();return out.str();}
static void pure(const std::filesystem::path& output){
    gp::Polynomial h{{{"C"},-1},{{"A","x"},-1}},e{{{"A","l"},1},{{"l0"},-1}};
    auto ring=gp::add(gp::multiply({{{"l"},1}},h),gp::multiply({{{"x"},1}},e));
    ring=gp::add(ring,gp::multiply({{{"x"},1},{{},-1}},{{{"l0"},1}}));
    ring=gp::add(ring,{{{"l0"},1},{{"C","l"},1},{{},-1}});
    require_test(ring==gp::constant(-1),"exact cubic cancellation failed");
    require_test(gp::multiply({{{"z"},2}},{{{"a"},-3}})==gp::Polynomial{{{"a","z"},-6}},"commutative monomial canonicalization failed");
    bool rejected=false;try{(void)gp::multiply({{{"a","b"},1}},{{{"c","d"},1}});}catch(const gp::Unsupported&){rejected=true;}require_test(rejected,"degree4 accepted");
    for(int v=-3;v<=3;++v)for(auto relation:{Z3_OP_LE,Z3_OP_LT,Z3_OP_GE,Z3_OP_GT})for(bool positive:{false,true}){
        const bool truth=relation==Z3_OP_LE?v<=0:relation==Z3_OP_LT?v<0:relation==Z3_OP_GE?v>=0:v>0;
        const auto poly=gp::normalized(relation,gp::constant(v),positive);auto at=poly.find({});const mpz_class value=at==poly.end()?mpz_class(0):at->second;
        require_test((value>=0)==(truth==positive),"strict/complement integer normalization failed");
    }
    const gp::Polynomial goal{{{"g"},1}};
    require_test(gp::bound(goal,true,2)==gp::Polynomial{{{"g"},-1},{{},1}},"min bound changed");
    require_test(gp::bound(goal,false,2)==gp::Polynomial{{{"g"},1},{{},-3}},"max bound changed");
    const mpz_class large=mpz_class(1)<<200;
    require_test(gp::multiply(gp::constant(large),gp::constant(large))==gp::constant(mpz_class(large*large)),"GMP narrowed multiplication");
    z3::context c;auto x=c.int_const("x");z3::expr_vector hard(c);hard.push_back(x>=0);
    for(double seconds:{0.,-1.,std::numeric_limits<double>::infinity(),std::numeric_limits<double>::quiet_NaN()}){
        auto r=solve_omt_guarded_product(c,hard,x,true,"0",seconds);require_test(r.answer==z3::unknown&&r.queries==0,"bad budget invoked solver");
    }
    auto tiny=solve_omt_guarded_product(c,hard,x,true,"0",std::numeric_limits<double>::denorm_min());require_test(tiny.answer==z3::unknown&&tiny.queries==0,"expired setup invoked solver");
    for(unsigned kind=0;kind<4;++kind){
        z3::expr_vector unsupported(c);
        if(kind==0)unsupported.push_back(c.bool_const("b"));
        else if(kind==1)unsupported.push_back(c.real_const("r")>=0);
        else if(kind==2)unsupported.push_back(x*x*x==0);
        else unsupported.push_back(z3::ite(x>0,x,x)==0);
        const auto r=solve_omt_guarded_product(c,unsupported,x,true,"0",.5);require_test(!r.applicable&&r.answer==z3::unknown&&r.queries==0,"unsupported formula silently lost");
    }
    const auto stem=(output/"protected").string();save(stem+".smt2","protected original bytes");
    auto protected_result=solve_omt_guarded_product(c,hard,x,true,"0",.5,stem);
    require_test(protected_result.answer==z3::unknown&&protected_result.queries==0,"existing query was overwritten or checked");require_test(read(stem+".smt2")=="protected original bytes","artifact/source protection failed");
    OmtGuardedProductResult late;late.answer=z3::unsat;gp::reject_late(late,true);require_test(late.answer==z3::unknown,"late UNSAT not demoted");
    require_test(actual_checks==0&&optimize_checks==0&&model_requests==0,"pure suite solved or requested model");
    save(output/"pure.json","{\"passed\":true,\"actual_checks\":0,\"model_requests\":0}\n");
}
// Selection regressions use no solver. The aggregate preserves every RED result.
static void indexed(const std::filesystem::path& output){
    unsigned failures=0;std::ostringstream evidence;
    auto test=[&](const char* name,const std::function<void()>& body){
        try{body();evidence<<name<<": PASS\n";}
        catch(const std::exception& error){++failures;evidence<<name<<": FAIL "<<error.what()<<'\n';}
    };
    test("source_used_257",[](){
        z3::context c;z3::expr_vector hard(c);
        for(unsigned i=0;i<257;++i)hard.push_back(c.int_const(("used"+std::to_string(i)).c_str())>=0);
        gp::Budget budget(2);gp::Source source(budget);source.parse(hard,c.int_val(0));
        require_test(source.variables.size()==257,"source-used variables above old 256 cap rejected");
    });
    test("shared_eq_candidate_only",[](){
        z3::context c;auto a=c.int_const("a"),b=c.int_const("b"),v=c.int_const("c"),d=c.int_const("d"),g=c.int_const("g"),u=c.int_const("unrelated");
        z3::expr_vector hard(c);hard.push_back(a*b==d);hard.push_back(a*v<=0);hard.push_back(b>=0);hard.push_back(u>=0);
        gp::Budget budget(2);gp::Source source(budget);source.parse(hard,g);auto closure=gp::select(source,gp::bound(source.polynomials.at(g.id()),true,0),budget);
        unsigned equalities=0;for(const auto& cut:closure.cuts)if(cut.equality){++equalities;require_test(cut.variable=="c","unrelated Eq multiplier selected");}
        require_test(equalities==1&&closure.inventory.count({"a","b","c"}),"shared cubic equality candidate missing");
        require_test(!closure.inventory.count({"a","b","unrelated"}),"unrelated cubic inventory expansion");
    });
    test("indexed_reaches_late_inventory_pair",[](){
        z3::context c;gp::Budget budget(2);gp::Source source(budget);
        for(unsigned i=0;i<640;++i){auto name="unrelated"+std::to_string(i);auto v=c.int_const(name.c_str());source.variables.emplace(name,v);source.inventory.insert({name});source.atoms.emplace_back(v>=0,gp::Polynomial{{{name},1}});}
        for(const auto* name:{"x","y"}){auto v=c.int_const(name);source.variables.emplace(name,v);source.inventory.insert({name});source.atoms.emplace_back(v>=0,gp::Polynomial{{{name},1}});}
        source.inventory.insert({"x","y"});auto closure=gp::select(source,gp::constant(-1),budget);bool found=false;
        for(const auto& cut:closure.cuts)if(!cut.equality){const auto& l=closure.facts.at(cut.left);const auto& r=closure.facts.at(cut.right);if(l.atom==640&&r.atom==641&&l.positive&&r.positive)found=true;}
        require_test(found,"inventory-covered late positive pair lost behind all-pairs prefix");
    });
    save(output/"indexed.txt",evidence.str());std::cout<<evidence.str();require_test(failures==0,"indexed policy regressions failed");
    require_test(actual_checks==0&&optimize_checks==0&&model_requests==0,"indexed suite solved or requested model");
}
static bool truncated(const gp::Closure& closure,const char* reason){return std::find(closure.truncations.begin(),closure.truncations.end(),reason)!=closure.truncations.end();}
static void resources(const std::filesystem::path& output){
    // A cancellation-sensitive example: the arbitrary term ab disappears,
    // whereas the admissible leading product aa cannot disappear.
    gp::Polynomial h{{{"a"},1},{{"b"},1}},k{{{"a"},1},{{"b"},-1}};
    const auto product=gp::multiply(h,k);require_test(!product.count({"a","b"}),"cancellation fixture changed");
    require_test(gp::leading_monomial(h)==gp::Monomial{"a"}&&gp::leading_monomial(product)==gp::Monomial({"a","a"}),"graded leading monomial changed");
    std::vector<gp::Polynomial> polynomials{h,k,{{{"a","b"},1},{{"b","b"},1}},{{{"a"},2},{{},3}},gp::constant(7)};
    for(const auto& left:polynomials)for(const auto& right:polynomials)if(gp::degree(left)+gp::degree(right)<=3){
        auto expected=gp::leading_monomial(left);const auto tail=gp::leading_monomial(right);expected.insert(expected.end(),tail.begin(),tail.end());std::sort(expected.begin(),expected.end());
        require_test(gp::leading_monomial(gp::multiply(left,right))==expected,"LM multiplication/cancellation invariant failed");
    }
    // Combined accounting stops in the middle of a product and publishes none.
    gp::Budget budget(4);gp::Closure charged;charged.total_scans=gp::max_pairs-1;bool capped=false;
    try{(void)gp::multiply(h,k,[&](){charged.charge(budget,charged.multiply_term_visits);});}catch(const gp::IndexScanCap&){capped=true;}
    require_test(capped&&charged.total_scans==gp::max_pairs&&charged.multiply_term_visits==1&&charged.cuts.empty(),"term multiplication escaped combined 200k cap");
    const mpz_class high=mpz_class(1)<<8191;bool bits=false;try{(void)gp::multiply(gp::constant(high),gp::constant(2));}catch(const gp::Unsupported&){bits=true;}require_test(bits,"8192 coefficient bit guard changed");
    // Every possible checkpoint interruption rolls back the current Eq cut.
    gp::Closure original;original.inventory={{"x"}};gp::Cut old;old.equality=true;old.variable="old";original.cuts.push_back(old);
    gp::Cut candidate;candidate.equality=true;candidate.variable="y";candidate.product={{{"x","y"},1},{{"y","z"},-1}};
    std::vector<gp::Monomial> missing{{"x","y"},{"y","z"}};
    for(unsigned stop=0;stop<4;++stop){
        auto closure=original;unsigned visits=0;bool expired=false;
        try{gp::commit_equality(closure,candidate,missing,[&](){if(visits++==stop)throw gp::GenerationExpired{};});}catch(const gp::GenerationExpired&){expired=true;}
        require_test(expired&&closure.inventory==original.inventory&&closure.cuts.size()==1&&closure.cuts[0].variable=="old","deadline left partial inventory or cut");
    }
    unsigned allocation_failures=0;bool allocation_success=false;
    for(long failure=0;failure<32;++failure){
        auto closure=original;auto cut=candidate;gp::Check checkpoint=[](){};
        try{fail_allocation_after=failure;gp::commit_equality(closure,std::move(cut),missing,checkpoint);fail_allocation_after=-1;allocation_success=true;}
        catch(const std::bad_alloc&){fail_allocation_after=-1;++allocation_failures;require_test(closure.inventory==original.inventory&&closure.cuts.size()==1&&closure.cuts[0].variable=="old","bad_alloc left partial inventory or cut");}
        if(allocation_success){require_test(closure.inventory.size()==3&&closure.cuts.size()==2,"successful transaction missing inventory/cut");break;}
    }
    require_test(allocation_success&&allocation_failures>=4,"allocation fault test missed commit allocations");
    {
        auto closure=original;unsigned visits=0;bool expired=false;try{gp::append_product(closure,candidate,[&](){if(visits++==1)throw gp::GenerationExpired{};});}catch(const gp::GenerationExpired&){expired=true;}
        require_test(expired&&closure.cuts.size()==1,"late optional product cut committed");
    }
    z3::context c;auto x=c.int_const("x");
    {
        gp::Budget half(2);z3::expr_vector hard(c);hard.push_back(x>=0);gp::Source source(half);source.parse(hard,x);half.began=gp::Clock::now()-std::chrono::milliseconds(1100);
        const auto closure=gp::select(source,gp::bound(source.polynomials.at(x.id()),true,0),half);
        require_test(closure.inventory==source.inventory&&closure.cuts.empty()&&truncated(closure,"generation_deadline"),"half allowance dropped base or continued selection");
        half.began=gp::Clock::now()-std::chrono::seconds(3);bool expired=false;try{(void)gp::select(source,{},half);}catch(const gp::Expired&){expired=true;}require_test(expired,"full deadline swallowed by optional closure");
    }
    {
        gp::Budget cap_budget(4);gp::Source source(cap_budget);auto a=c.int_const("a"),b=c.int_const("b"),v=c.int_const("c"),d=c.int_const("d");
        for(const auto* name:{"a","b","c","d"}){source.variables.emplace(name,c.int_const(name));source.inventory.insert({name});}
        source.atoms.emplace_back(a*b==d,gp::Polynomial{{{"a","b"},1},{{"d"},-1}});source.atoms.emplace_back(a*v>=0,gp::Polynomial{{{"a","c"},1}});source.atoms.emplace_back(b>=0,gp::Polynomial{{{"b"},1}});
        source.inventory.insert({"a","b"});source.inventory.insert({"a","c"});
        for(unsigned i=0;i<201;++i){auto name="padding"+std::to_string(i);source.variables.emplace(name,c.int_const(name.c_str()));source.inventory.insert({name});}
        for(const auto& i:source.variables)for(const auto& j:source.variables)if(i.first<=j.first&&source.inventory.size()<gp::max_monomials&&i.first.find("padding")==0&&j.first.find("padding")==0)source.inventory.insert({i.first,j.first});
        require_test(source.inventory.size()==gp::max_monomials,"monomial cap fixture incomplete");const auto closure=gp::select(source,gp::constant(-1),cap_budget);
        require_test(closure.inventory==source.inventory&&truncated(closure,"monomial_cap"),"optional monomial cap mutated full base");for(const auto& cut:closure.cuts)require_test(!cut.equality,"over-cap Eq cut committed");
        source.inventory.insert({"a","a"});bool rejected=false;try{(void)gp::select(source,{},cap_budget);}catch(const gp::Unsupported&){rejected=true;}require_test(rejected,"over-cap full base was truncated");
    }
    {
        gp::Budget cap_budget(4);gp::Source source(cap_budget);source.variables.emplace("x",x);source.inventory={{"x"},{"x","x"}};
        for(unsigned i=0;i<100;++i)source.atoms.emplace_back(x>=0,gp::Polynomial{{{"x"},1}});
        const auto closure=gp::select(source,gp::constant(-1),cap_budget);require_test(closure.cuts.size()==gp::max_cuts&&truncated(closure,"cut_cap")&&closure.inventory==source.inventory&&closure.total_scans<=gp::max_pairs,"12k cut cap/full base changed");
    }
    {
        gp::Budget cap_budget(4);z3::expr_vector hard(c);for(unsigned i=0;i<8192;++i)hard.push_back(c.int_const(("boundary"+std::to_string(i)).c_str())>=0);
        gp::Source source(cap_budget);source.parse(hard,c.int_val(0));require_test(source.variables.size()==8192,"8192 source variables rejected");
        hard.push_back(c.int_const("overflow")>=0);gp::Source excess(cap_budget);bool rejected=false;try{excess.parse(hard,c.int_val(0));}catch(const gp::Unsupported& error){rejected=std::string(error.what())=="source used Int variable cap";}require_test(rejected,"8193 source variables admitted");
    }
    require_test(actual_checks==0&&optimize_checks==0&&model_requests==0,"resource suite solved or requested model");
    save(output/"resources.json","{\"passed\":true,\"actual_checks\":0,\"allocation_failures\":"+std::to_string(allocation_failures)+",\"coefficient_bits\":8192,\"max_variables\":8192,\"max_cuts\":12000,\"max_work\":200000}\n");
}
static void monomial_json(std::ostream& out,const gp::Monomial& value){out<<'[';for(size_t i=0;i<value.size();++i){if(i)out<<',';out<<gp::quoted(value[i]);}out<<']';}
static void polynomial_json(std::ostream& out,const gp::Polynomial& value){out<<'[';bool first=true;for(const auto& term:value){if(!first)out<<',';first=false;out<<'[';monomial_json(out,term.first);out<<','<<gp::quoted(term.second.get_str())<<']';}out<<']';}
static void inventory_json(std::ostream& out,const std::set<gp::Monomial>& value){out<<'[';bool first=true;for(const auto& monomial:value){if(!first)out<<',';first=false;monomial_json(out,monomial);}out<<']';}
static void parity(const std::filesystem::path& output){
    const std::vector<std::string> fixtures{
        "(assert (= (* a b) d))(assert (<= (+ C (* a c)) 0))(assert (>= b 0))(assert (>= (+ d (* C b)) 1))",
        "(assert (= (+ (* a b) (* 2 a z)) d))(assert (>= (* a c) 0))(assert (>= b 0))",
        "(assert (= x y))(assert (>= (* x z) 0))",
        "(assert (= (* a a) d))(assert (>= (* a c) 0))(assert (>= a 0))",
        "(assert (>= (+ a b) 0))(assert (>= (- a b) 0))(assert (>= (* a a) 0))(assert (>= (* b b) 0))",
        "(assert (= 1 0))(assert (or (not (>= x 0)) (<= (+ x 1) 0)))",
        "(assert (= (+ x y) z))(assert (>= (* x x) 0))(assert (>= (* y y) 0))(assert (>= (* x y) 0))"};
    for(size_t index=0;index<fixtures.size();++index){
        z3::context c;const std::string declarations="(declare-const a Int)(declare-const b Int)(declare-const c Int)(declare-const d Int)(declare-const C Int)(declare-const g Int)(declare-const z Int)(declare-const x Int)(declare-const y Int)";
        auto hard=c.parse_string((declarations+fixtures[index]).c_str());auto objective=c.int_const("g");gp::Budget budget(3);gp::Source source(budget);source.parse(hard,objective);auto bound=gp::bound(source.polynomials.at(objective.id()),true,0);const auto closure=gp::select(source,bound,budget);
        std::ostringstream out;out<<"{\"atoms\":[";for(size_t i=0;i<source.atoms.size();++i){if(i)out<<',';const auto& atom=source.atoms[i];const auto relation=atom.relation==Z3_OP_EQ?"=":atom.relation==Z3_OP_GE?">=":atom.relation==Z3_OP_GT?">":atom.relation==Z3_OP_LE?"<=":"<";out<<"{\"atom\":"<<gp::quoted(atom.text)<<",\"relation\":"<<gp::quoted(relation)<<",\"poly\":";polynomial_json(out,atom.difference);out<<'}';}
        out<<"],\"variables\":[";bool first=true;for(const auto& variable:source.variables){if(!first)out<<',';first=false;out<<gp::quoted(variable.first);}out<<"],\"bound\":";polynomial_json(out,bound);out<<",\"base_inventory\":";inventory_json(out,source.inventory);out<<",\"inventory\":";inventory_json(out,closure.inventory);out<<",\"cuts\":[";
        auto fact=[&](size_t i){const auto& f=closure.facts.at(i);out<<"{\"atom\":"<<gp::quoted(f.atom==source.atoms.size()?"@bound":source.atoms.at(f.atom).text)<<",\"polarity\":"<<(f.positive?"true":"false")<<'}';};
        for(size_t i=0;i<closure.cuts.size();++i){if(i)out<<',';const auto& cut=closure.cuts[i];if(cut.equality)out<<"{\"kind\":\"equality_times_variable\",\"atom\":"<<gp::quoted(source.atoms.at(cut.atom).text)<<",\"variable\":"<<gp::quoted(cut.variable)<<'}';else{out<<"{\"kind\":\"nonnegative_product\",\"left\":";fact(cut.left);out<<",\"right\":";fact(cut.right);out<<'}';}}
        size_t eq=0;for(const auto& cut:closure.cuts)eq+=cut.equality;out<<"],\"stats\":{\"pair_scans\":"<<closure.pairs<<",\"equality_cuts\":"<<eq<<",\"product_cuts\":"<<closure.cuts.size()-eq;
#define GP_STAT(name) out<<",\"" #name "\":"<<closure.name
        GP_STAT(total_scans);GP_STAT(join_scans);GP_STAT(join_candidates);GP_STAT(index_scans);GP_STAT(leading_term_visits);GP_STAT(inventory_visits);GP_STAT(split_visits);GP_STAT(bucket_visits);GP_STAT(pair_visits);GP_STAT(multiply_term_visits);GP_STAT(candidate_pairs);
#undef GP_STAT
        out<<",\"truncations\":[";for(size_t i=0;i<closure.truncations.size();++i){if(i)out<<',';out<<gp::quoted(closure.truncations[i]);}out<<"]}}\n";save(output/(std::to_string(index)+".json"),out.str());
    }
    require_test(actual_checks==0&&optimize_checks==0&&model_requests==0,"parity suite solved or requested model");
}
static std::vector<std::string> generated_names(){std::vector<std::string> names{"A","C","x","l","l0","g"};for(unsigned i=0;i<300;++i)names.push_back("irrelevant_"+std::to_string(i));return names;}
static std::string declarations(){std::string text;for(const auto& name:generated_names())text+="(declare-const "+name+" Int)";return text;}
static void generated(const std::filesystem::path& output){
    const std::string branch="(or (and (>= l 0) (>= l0 0) (>= (+ l0 (* C l)) 1) (= (* A l) l0)) (>= g 0))";
    std::string irrelevant="(assert (>= (+";for(unsigned i=0;i<300;++i)irrelevant+=" irrelevant_"+std::to_string(i);irrelevant+=") 0))";
    const std::string hard=irrelevant+"(assert (>= x 1))(assert (<= (+ C (* A x)) 0))";
    for(unsigned index=0;index<3;++index){
        const auto directory=output/std::to_string(index);std::filesystem::create_directory(directory);
        const bool minimize=index!=1;std::string source,filtered;
        if(index==0){source=declarations()+hard+"(assert "+branch+")(minimize g)";filtered=source;}
        else if(index==1){source=declarations()+hard+"(assert (not (not "+branch+")))(maximize (- g))";filtered=declarations()+hard+"(assert (not (not "+branch+")))(minimize (- g))";}
        else{source="(declare-const x Int)(assert (>= x 0))(minimize x)";filtered=source;}
        save(directory/"source.smt2",source);z3::context context;z3::optimize parser(context);parser.from_string(filtered.c_str());
        auto original=parser.assertions();auto objective=parser.objectives()[0];const std::string best=index<2?"0":"1";
        auto result=solve_omt_guarded_product(context,original,objective,minimize,best,.5,(directory/"proof").string());
        require_test(result.queries==1,"generated helper did not run exactly one check");
        require_test(result.answer==(index<2?z3::unsat:z3::sat),"wrong generated relaxation answer");
        require_test(result.query_path==(directory/"proof.smt2").string()&&result.certificate_path==(directory/"proof.json").string(),"artifact suffix mismatch");
        require_test(read(result.query_path)==result.actual_query_smt2&&read(result.certificate_path)==result.certificate_json,"actual query or native certificate changed");
        if(index<2){
            require_test(result.cuts>0&&result.monomials>306,"cubic closure was not generated");
            z3::expr_vector from(context),to(context);for(const auto& name:generated_names()){from.push_back(context.int_const(name.c_str()));to.push_back(context.int_val(name=="x"?1:0));}
            for(unsigned i=0;i<original.size();++i){auto f=original[i];require_test(f.substitute(from,to).simplify().is_true(),"full source incumbent fails");}
            auto goal=objective;goal=goal.substitute(from,to).simplify();require_test(goal.is_numeral()&&std::string(Z3_get_numeral_string(context,goal))=="0","source objective reflection failed");
            std::ostringstream ground;ground<<"{\"solver_checks\":0,\"objective\":\"0\",\"assignment\":{";bool first=true;for(const auto& name:generated_names()){if(!first)ground<<',';first=false;ground<<gp::quoted(name)<<':'<<gp::quoted(name=="x"?"1":"0");}ground<<"}}\n";save(directory/"incumbent-ground.json",ground.str());
        }
        if(index==0){
            auto assertions=context.parse_string(result.actual_query_smt2.c_str());require_test(assertions.size()==original.size()+1+result.cuts,"actual query missing base or cuts");
            z3::solver bare(context,"QF_LRA");for(unsigned i=0;i<original.size()+1;++i)bare.add(assertions[i]);bare.set("timeout",100u);
            require_test(bare.check()==z3::sat,"bare relaxation must remain SAT");save(directory/"bare.smt2",bare.to_smt2());
        }
        std::ostringstream row;row<<"{\"index\":"<<index<<",\"answer\":"<<gp::quoted(index<2?"unsat":"sat")<<",\"queries\":"<<result.queries<<",\"cuts\":"<<result.cuts<<",\"monomials\":"<<result.monomials<<",\"setup_seconds\":"<<result.setup_seconds<<",\"generation_seconds\":"<<result.generation_seconds<<",\"check_seconds\":"<<result.check_seconds<<",\"seconds\":"<<result.seconds<<",\"native_is_optimal\":false,\"source_incumbent_ground_verified\":"<<(index<2?"true":"false")<<"}\n";save(directory/"result.json",row.str());
    }
    require_test(actual_checks==4&&optimize_checks==0&&model_requests==0,"unexpected generated check/model count");save(output/"checks.json","{\"actual_checks\":4,\"optimize_checks\":0,\"model_requests\":0,\"nominal_check_seconds\":1.6,\"parent_hard_seconds\":6,\"large_source_used_ints\":306}\n");
}
int main(int argc,char** argv){
    try{
        require_test(argc==4,"expected mode output library");Dl_info info{};require_test(dladdr(reinterpret_cast<void*>(&Z3_mk_context_rc),&info)&&std::filesystem::equivalent(info.dli_fname,argv[3]),"wrong loaded SDK library");
        const auto output=std::filesystem::absolute(argv[2]);require_test(std::filesystem::create_directory(output),"test output must be new");
        if(std::string(argv[1])=="pure")pure(output);else if(std::string(argv[1])=="generated")generated(output);else if(std::string(argv[1])=="indexed")indexed(output);else if(std::string(argv[1])=="resources")resources(output);else if(std::string(argv[1])=="parity")parity(output);else throw std::runtime_error("unknown test mode");
        std::cout<<"PASS "<<argv[1]<<" actual_checks="<<actual_checks<<" optimize_checks="<<optimize_checks<<" model_requests="<<model_requests<<'\n';return 0;
    }catch(const std::exception& error){std::cerr<<"FAIL "<<error.what()<<" actual_checks="<<actual_checks<<'\n';return 1;}
}
