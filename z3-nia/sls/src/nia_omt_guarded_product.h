#pragma once
// Standalone necessary source-bound relaxation. No model/optimality API.
#include <z3++.h>
#include <gmpxx.h>
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <functional>
#include <iomanip>
#include <map>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <vector>
#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>

namespace nia {
struct OmtGuardedProductResult {
    bool applicable=false;
    z3::check_result answer=z3::unknown;
    uint64_t queries=0,cuts=0,monomials=0,pair_scans=0;
    double setup_seconds=0,generation_seconds=0,check_seconds=0,seconds=0;
    std::string reason,actual_query_smt2,certificate_json,query_path,certificate_path;
};
namespace guarded_product_detail {
using Clock=std::chrono::steady_clock;
using Monomial=std::vector<std::string>;
using Polynomial=std::map<Monomial,mpz_class>;
using Check=std::function<void()>;
inline constexpr size_t max_variables=8192,max_monomials=20000,max_cuts=12000,max_pairs=200000,max_nodes=100000,max_bits=8192,max_artifact=64*1024*1024;
struct Unsupported:std::runtime_error{using std::runtime_error::runtime_error;};
struct Expired{};
struct GenerationExpired{};
struct IndexScanCap{};
inline void require(bool okay,const char* reason){if(!okay)throw Unsupported(reason);}
struct Budget {
    Clock::time_point began=Clock::now();double seconds;
    explicit Budget(double value):seconds(value){}
    double elapsed()const{return std::chrono::duration<double>(Clock::now()-began).count();}
    double remaining()const{return seconds-elapsed();}
    void check()const{if(!(remaining()>0))throw Expired{};}
    void optional()const{check();if(elapsed()>=seconds*.5)throw GenerationExpired{};}
};
inline void integer_guard(const mpz_class& value){require(mpz_sizeinbase(value.get_mpz_t(),2)<=max_bits,"integer coefficient bit cap");}
inline mpz_class decimal(const std::string& text,const Check& check=[](){}){
    size_t at=!text.empty()&&text[0]=='-'?1:0;require(at<text.size()&&text.size()<=2500,"expected finite decimal integer");
    for(size_t i=at;i<text.size();++i){check();require(text[i]>='0'&&text[i]<='9',"expected finite decimal integer");}
    mpz_class value(text,10);integer_guard(value);return value;
}
inline Polynomial constant(const mpz_class& value){integer_guard(value);return value==0?Polynomial{}:Polynomial{{{},value}};}
inline void accumulate(Polynomial& result,Monomial monomial,const mpz_class& coefficient){
    std::sort(monomial.begin(),monomial.end());auto& value=result[monomial];value+=coefficient;integer_guard(value);if(value==0)result.erase(monomial);
    require(result.size()<=max_monomials,"polynomial monomial cap");
}
inline Polynomial add(Polynomial result,const Polynomial& right,int sign=1,const Check& check=[](){}){
    for(const auto& term:right){check();mpz_class coefficient=sign*term.second;accumulate(result,term.first,coefficient);}return result;
}
inline Polynomial scale(const Polynomial& input,int sign,const Check& check=[](){}){return add({},input,sign,check);}
inline Polynomial multiply(const Polynomial& left,const Polynomial& right,const Check& check=[](){},size_t degree=3){
    Polynomial result;for(const auto& a:left)for(const auto& b:right){check();auto m=a.first;m.insert(m.end(),b.first.begin(),b.first.end());require(m.size()<=degree,"polynomial degree cap");mpz_class c=a.second*b.second;integer_guard(c);accumulate(result,std::move(m),c);}return result;
}
inline size_t degree(const Polynomial& poly){size_t value=0;for(const auto& term:poly)value=std::max(value,term.first.size());return value;}
inline Polynomial normalized(Z3_decl_kind relation,const Polynomial& difference,bool positive=true,const Check& check=[](){}){
    Polynomial h;
    if(relation==Z3_OP_GE)h=difference;
    else if(relation==Z3_OP_GT)h=add(difference,constant(-1),1,check);
    else if(relation==Z3_OP_LE)h=scale(difference,-1,check);
    else if(relation==Z3_OP_LT)h=add(scale(difference,-1,check),constant(-1),1,check);
    else throw Unsupported("equality has no nonnegative complementary fact");
    return positive?h:add(scale(h,-1,check),constant(-1),1,check);
}
inline Polynomial bound(const Polynomial& objective,bool minimize,const mpz_class& best,const Check& check=[](){}){
    return minimize?add(scale(objective,-1,check),constant(mpz_class(best-1)),1,check):add(objective,constant(mpz_class(-best-1)),1,check);
}
inline bool comparison(Z3_decl_kind kind){return kind==Z3_OP_EQ||kind==Z3_OP_LE||kind==Z3_OP_LT||kind==Z3_OP_GE||kind==Z3_OP_GT;}
inline std::string quoted(const std::string& value){
    std::ostringstream out;out<<'"';for(unsigned char c:value){if(c=='"'||c=='\\')out<<'\\'<<char(c);else if(c<32)out<<"\\u"<<std::hex<<std::setw(4)<<std::setfill('0')<<unsigned(c)<<std::dec;else out<<char(c);}out<<'"';return out.str();
}
struct Atom {
    z3::expr source;std::string text;Z3_decl_kind relation;Polynomial difference;
    Atom(const z3::expr& value,Polynomial poly):source(value),text(value.to_string()),relation(value.decl().decl_kind()),difference(std::move(poly)){}
};
struct Source {
    const Budget& budget;std::vector<z3::expr> order;std::unordered_map<unsigned,Polynomial> polynomials;
    std::map<std::string,z3::expr> variables;std::vector<Atom> atoms;std::unordered_map<unsigned,size_t> atom_ids;std::set<Monomial> inventory;
    explicit Source(const Budget& value):budget(value){}
    void parse(const z3::expr_vector& hard,const z3::expr& objective){
        std::vector<std::pair<z3::expr,bool>> pending;for(unsigned i=0;i<hard.size();++i){require(hard[i].is_bool(),"hard constraint must be Boolean");pending.emplace_back(hard[i],false);}pending.emplace_back(objective,false);
        std::unordered_set<unsigned> seen;std::unordered_map<unsigned,size_t> depths;
        while(!pending.empty()){
            budget.check();auto item=pending.back();pending.pop_back();const auto node=item.first;const auto id=node.id();
            if(item.second){size_t depth=1;for(unsigned i=0;i<node.num_args();++i)depth=std::max(depth,depths.at(node.arg(i).id())+1);require(depth<=256,"source AST depth cap");depths.emplace(id,depth);order.push_back(node);continue;}
            if(!seen.insert(id).second)continue;
            require(seen.size()<=max_nodes,"source DAG node cap");require(node.is_app(),"source quantifier/bound variable unsupported");
            const auto kind=node.decl().decl_kind();bool valid=false;
            if(node.is_int()&&node.is_numeral())valid=node.num_args()==0;
            else if(node.is_true()||node.is_false())valid=node.num_args()==0;
            else if(kind==Z3_OP_UNINTERPRETED)valid=node.num_args()==0&&node.is_int();
            else if(kind==Z3_OP_ADD||kind==Z3_OP_SUB||kind==Z3_OP_UMINUS||kind==Z3_OP_MUL){valid=node.is_int()&&node.num_args()>0;for(unsigned i=0;i<node.num_args();++i)valid=valid&&node.arg(i).is_int();}
            else if(comparison(kind))valid=node.is_bool()&&node.num_args()==2&&node.arg(0).is_int()&&node.arg(1).is_int();
            else if(kind==Z3_OP_AND||kind==Z3_OP_OR||kind==Z3_OP_NOT){valid=node.is_bool()&&(kind!=Z3_OP_NOT||node.num_args()==1);for(unsigned i=0;i<node.num_args();++i)valid=valid&&node.arg(i).is_bool();}
            require(valid,"unsupported original source operator/sort");pending.emplace_back(node,true);for(unsigned i=node.num_args();i>0;--i)pending.emplace_back(node.arg(i-1),false);
        }
        Check check=[this](){budget.check();};
        for(const auto& node:order){
            budget.check();const auto id=node.id();const auto kind=node.decl().decl_kind();
            if(node.is_int()){
                Polynomial p;
                if(node.is_numeral())p=constant(decimal(Z3_get_numeral_string(node.ctx(),node),check));
                else if(kind==Z3_OP_UNINTERPRETED){
                    const std::string name=node.decl().name().str();require(!name.empty()&&name.size()<=4096,"source symbol size cap");auto found=variables.find(name);
                    require(found==variables.end()||z3::eq(found->second,node),"ambiguous original source variable name");if(found==variables.end())variables.emplace(name,node);require(variables.size()<=max_variables,"source used Int variable cap");p[Monomial{name}]=1;
                }else if(kind==Z3_OP_MUL){p=constant(1);for(unsigned i=0;i<node.num_args();++i)p=multiply(p,polynomials.at(node.arg(i).id()),check,2);}
                else{p=polynomials.at(node.arg(0).id());if(node.num_args()==1&&(kind==Z3_OP_SUB||kind==Z3_OP_UMINUS))p=scale(p,-1,check);for(unsigned i=1;i<node.num_args();++i)p=add(std::move(p),polynomials.at(node.arg(i).id()),kind==Z3_OP_SUB?-1:1,check);}
                require(degree(p)<=2,"source degree exceeds two");for(const auto& term:p)if(!term.first.empty())inventory.insert(term.first);polynomials.emplace(id,std::move(p));
            }else if(comparison(kind)){
                atom_ids.emplace(id,atoms.size());atoms.emplace_back(node,add(polynomials.at(node.arg(0).id()),polynomials.at(node.arg(1).id()),-1,check));
            }
        }
        for(const auto& variable:variables)inventory.insert(Monomial{variable.first});
        require(inventory.size()<=max_monomials,"base monomial cap; never truncate base");
    }
};
struct Fact {size_t atom;bool positive;Polynomial poly;};
struct Cut {bool equality;size_t atom=0;std::string variable;size_t left=0,right=0;Polynomial product;};
struct Closure {
    std::set<Monomial> inventory;std::vector<Fact> facts;std::vector<Cut> cuts;std::vector<std::string> truncations;size_t pairs=0;
    // The 200k guard includes discovery joins, index traversal and every
    // optional polynomial term multiplication, matching the EB indexed policy.
    size_t total_scans=0,join_scans=0,index_scans=0,leading_term_visits=0,inventory_visits=0,split_visits=0,bucket_visits=0,pair_visits=0,multiply_term_visits=0,candidate_pairs=0,join_candidates=0;
    void truncate(const std::string& reason){if(std::find(truncations.begin(),truncations.end(),reason)==truncations.end())truncations.push_back(reason);}
    void charge(const Budget& budget,size_t& category){budget.optional();if(total_scans>=max_pairs)throw IndexScanCap{};++total_scans;++index_scans;++category;}
};
using EqualityCandidate=std::pair<size_t,std::string>;
inline std::set<EqualityCandidate> shared_equality_candidates(const Source& source,const Polynomial& bound_poly,Closure& closure,const Budget& budget){
    std::set<std::string> linear;std::map<std::string,std::set<std::string>> quadratic,base_neighbors;std::vector<size_t> equations;std::set<EqualityCandidate> candidates;
    auto edges=[](auto& target,const Monomial& pair){target[pair[0]].insert(pair[1]);target[pair[1]].insert(pair[0]);};
    auto support=[&](const Polynomial& poly){
        const bool affine=degree(poly)<=1;
        for(const auto& term:poly){budget.optional();if(affine&&term.first.size()==1)linear.insert(term.first[0]);if(term.first.size()==2)edges(quadratic,term.first);}
    };
    auto candidate=[&](size_t atom,const std::string& variable){
        budget.optional();if(closure.join_scans>=max_pairs){closure.truncate("join_scan_cap");return false;}
        ++closure.join_scans;++closure.total_scans;if(source.variables.count(variable))candidates.emplace(atom,variable);return true;
    };
    try{
        for(size_t at=0;at<source.atoms.size();++at){budget.optional();const auto& atom=source.atoms[at];if(atom.relation==Z3_OP_EQ){if(!atom.difference.empty())equations.push_back(at);}else support(atom.difference);}
        support(bound_poly);
        for(const auto& monomial:closure.inventory){budget.optional();if(monomial.size()==2)edges(base_neighbors,monomial);}
        for(size_t at:equations){
            budget.optional();const auto& poly=source.atoms[at].difference;
            if(degree(poly)==2){
                for(const auto& term:poly)if(term.first.size()==2){
                    const auto& m=term.first;std::set<std::pair<std::string,std::string>> orientations{{m[0],m[1]},{m[1],m[0]}};
                    for(const auto& orientation:orientations){
                        if(!linear.count(orientation.second))continue;
                        const auto found=quadratic.find(orientation.first);if(found==quadratic.end())continue;
                        for(const auto& variable:found->second)if(!candidate(at,variable))return candidates;
                    }
                }
            }else{
                for(const auto& term:poly)if(term.first.size()==1){
                    const auto found=base_neighbors.find(term.first[0]);if(found==base_neighbors.end())continue;
                    for(const auto& variable:found->second)if(!candidate(at,variable))return candidates;
                }
            }
        }
    }catch(const GenerationExpired&){closure.truncate("generation_deadline");}
    return candidates;
}
// No full-inventory copy per Eq cut. All allocations for rollback bookkeeping
// precede mutation; iterator erasure and pop_back cannot allocate or time out.
inline void commit_equality(Closure& closure,Cut cut,const std::vector<Monomial>& missing,const Check& checkpoint){
    std::vector<std::set<Monomial>::iterator> inserted;inserted.reserve(missing.size());bool appended=false;
    try{
        checkpoint();
        for(const auto& monomial:missing){auto entry=closure.inventory.insert(monomial);if(entry.second)inserted.push_back(entry.first);checkpoint();}
        closure.cuts.push_back(std::move(cut));appended=true;checkpoint();
    }catch(...){if(appended)closure.cuts.pop_back();for(auto entry:inserted)closure.inventory.erase(entry);throw;}
}
inline void append_product(Closure& closure,Cut cut,const Check& checkpoint){
    checkpoint();closure.cuts.push_back(std::move(cut));try{checkpoint();}catch(...){closure.cuts.pop_back();throw;}
}
// Greatest degree, then lexicographically smallest sorted factor tuple is a
// multiplicative graded exponent-lex order. Thus LM(h*k)=LM(h)*LM(k), even
// when other terms cancel; an inventory-covered product must pass this filter.
inline Monomial leading_monomial(const Polynomial& poly,const Check& checkpoint=[](){}){
    Monomial leading;bool found=false;
    for(const auto& term:poly){checkpoint();if(term.second==0)continue;const auto& monomial=term.first;if(!found||monomial.size()>leading.size()||(monomial.size()==leading.size()&&monomial<leading)){leading=monomial;found=true;}}
    require(found,"zero polynomial has no leading monomial");return leading;
}
inline Closure select(const Source& source,const Polynomial& bound_poly,const Budget& budget){
    Closure closure;closure.inventory=source.inventory;for(const auto& term:bound_poly)if(!term.first.empty())closure.inventory.insert(term.first);
    require(closure.inventory.size()<=max_monomials,"base monomial cap; never truncate base");
    Check checkpoint=[&](){budget.optional();};Check multiplication_checkpoint=[&](){closure.charge(budget,closure.multiply_term_visits);};
    auto room=[&](){budget.check();if(closure.cuts.size()>=max_cuts){closure.truncate("cut_cap");return false;}try{budget.optional();return true;}catch(const GenerationExpired&){closure.truncate("generation_deadline");return false;}};
    const auto candidates=shared_equality_candidates(source,bound_poly,closure,budget);closure.join_candidates=candidates.size();
    for(const auto& candidate:candidates){
        if(!room())return closure;
        try{
            auto product=multiply(source.atoms[candidate.first].difference,Polynomial{{Monomial{candidate.second},1}},multiplication_checkpoint);checkpoint();if(product.empty())continue;
            std::vector<Monomial> missing;for(const auto& term:product){checkpoint();if(!term.first.empty()&&!closure.inventory.count(term.first))missing.push_back(term.first);}
            if(closure.inventory.size()+missing.size()>max_monomials){closure.truncate("monomial_cap");continue;}if(!room())return closure;
            Cut cut;cut.equality=true;cut.atom=candidate.first;cut.variable=candidate.second;cut.product=std::move(product);commit_equality(closure,std::move(cut),missing,checkpoint);
        }catch(const GenerationExpired&){closure.truncate("generation_deadline");return closure;}catch(const IndexScanCap&){closure.truncate("index_scan_cap");return closure;}catch(const Unsupported&){closure.truncate("optional_arithmetic_cap");}
    }
    for(size_t at=0;at<=source.atoms.size();++at){
        if(!room())return closure;
        const bool own=at==source.atoms.size();if(!own&&source.atoms[at].relation==Z3_OP_EQ)continue;
        for(bool positive:{true,false}){
            try{auto p=own?(positive?bound_poly:add(scale(bound_poly,-1,checkpoint),constant(-1),1,checkpoint)):normalized(source.atoms[at].relation,source.atoms[at].difference,positive,checkpoint);checkpoint();if(degree(p)>0)closure.facts.push_back(Fact{at,positive,std::move(p)});}
            catch(const GenerationExpired&){closure.truncate("generation_deadline");return closure;}catch(const Unsupported&){closure.truncate("optional_arithmetic_cap");}
        }
    }
    try{
        std::map<Monomial,std::vector<size_t>> buckets;
        for(size_t index=0;index<closure.facts.size();++index){
            auto leading=leading_monomial(closure.facts[index].poly,[&](){closure.charge(budget,closure.leading_term_visits);});closure.charge(budget,closure.bucket_visits);
            if(!leading.empty()&&leading.size()<=2)buckets[leading].push_back(index);
        }
        std::vector<Monomial> relevant;
        for(const auto& monomial:closure.inventory){closure.charge(budget,closure.inventory_visits);if(monomial.size()==2||monomial.size()==3)relevant.push_back(monomial);}
        std::sort(relevant.begin(),relevant.end(),[](const auto& a,const auto& b){return a.size()!=b.size()?a.size()>b.size():a<b;});
        std::set<std::pair<size_t,size_t>> seen;
        for(const auto& monomial:relevant){
            const auto positions=monomial.size()==2?size_t(1):monomial.size();
            for(size_t position=0;position<positions;++position){
                if(position&&monomial[position]==monomial[position-1])continue;
                Monomial left{monomial[position]},right=monomial;right.erase(right.begin()+position);
                closure.charge(budget,closure.split_visits);closure.charge(budget,closure.bucket_visits);const auto a=buckets.find(left);
                closure.charge(budget,closure.bucket_visits);const auto b=buckets.find(right);
                if(a==buckets.end()||b==buckets.end())continue;
                for(size_t i=0;i<a->second.size();++i){
                    closure.charge(budget,closure.bucket_visits);
                    for(size_t j=left==right?i:0;j<b->second.size();++j){
                        closure.charge(budget,closure.pair_visits);const auto pair=std::minmax(a->second[i],b->second[j]);
                        if(!seen.emplace(pair.first,pair.second).second)continue;
                        ++closure.candidate_pairs;if(!room())return closure;++closure.pairs;
                        const auto& h=closure.facts[pair.first].poly;const auto& k=closure.facts[pair.second].poly;if(std::min(degree(h),degree(k))>1||degree(h)+degree(k)>3)continue;
                        try{
                            auto product=multiply(h,k,multiplication_checkpoint);checkpoint();bool covered=true;for(const auto& term:product){checkpoint();if(!term.first.empty()&&!closure.inventory.count(term.first)){covered=false;break;}}
                            if(!covered||product.empty())continue;
                            if(!room())return closure;
                            Cut cut;cut.equality=false;cut.left=pair.first;cut.right=pair.second;cut.product=std::move(product);append_product(closure,std::move(cut),checkpoint);
                        }catch(const Unsupported&){closure.truncate("optional_arithmetic_cap");}
                    }
                }
            }
        }
    }catch(const GenerationExpired&){closure.truncate("generation_deadline");}catch(const IndexScanCap&){closure.truncate("index_scan_cap");}
    return closure;
}
struct Mapping {Monomial variables;std::string name;z3::expr symbol;Mapping(Monomial m,std::string n,z3::expr s):variables(std::move(m)),name(std::move(n)),symbol(std::move(s)){};};
inline std::vector<Mapping> mapping(z3::context& context,const Source& source,const Closure& closure,const Budget& budget){
    size_t prefix_id=0;std::string prefix;
    for(;;){budget.check();prefix="nia_guarded_product_"+std::to_string(prefix_id++)+"_";bool collision=false;for(const auto& variable:source.variables)if(variable.first.find(prefix)!=std::string::npos){collision=true;break;}if(!collision)break;}
    std::vector<Monomial> inventory(closure.inventory.begin(),closure.inventory.end());std::sort(inventory.begin(),inventory.end(),[](const auto& a,const auto& b){return a.size()!=b.size()?a.size()<b.size():a<b;});
    std::vector<Mapping> result;for(const auto& m:inventory){budget.check();auto name=prefix+std::to_string(result.size());result.emplace_back(m,name,context.real_const(name.c_str()));}return result;
}
inline z3::expr lift(z3::context& context,const Polynomial& poly,const std::map<Monomial,z3::expr>& symbols,const Budget& budget){
    z3::expr_vector terms(context);for(const auto& term:poly){budget.check();auto coefficient=context.real_val(term.second.get_str().c_str());terms.push_back(term.first.empty()?coefficient:coefficient*symbols.at(term.first));}return terms.empty()?context.real_val(0):z3::sum(terms);
}
inline z3::expr_vector build(z3::context& context,const z3::expr_vector& hard,const Source& source,const Polynomial& bound_poly,const Closure& closure,const std::vector<Mapping>& mappings,const Budget& budget){
    std::map<Monomial,z3::expr> symbols;for(const auto& m:mappings)symbols.emplace(m.variables,m.symbol);
    std::vector<z3::expr> atoms;Check check=[&](){budget.check();};for(const auto& atom:source.atoms){budget.check();atoms.push_back(atom.relation==Z3_OP_EQ?lift(context,atom.difference,symbols,budget)==context.real_val(0):lift(context,normalized(atom.relation,atom.difference,true,check),symbols,budget)>=context.real_val(0));}
    std::unordered_map<unsigned,z3::expr> booleans;
    for(const auto& node:source.order)if(node.is_bool()){
        budget.check();const auto id=node.id();const auto kind=node.decl().decl_kind();auto found=source.atom_ids.find(id);
        if(found!=source.atom_ids.end())booleans.emplace(id,atoms.at(found->second));
        else if(node.is_true()||node.is_false())booleans.emplace(id,node);
        else if(kind==Z3_OP_NOT)booleans.emplace(id,!booleans.at(node.arg(0).id()));
        else{z3::expr_vector children(context);for(unsigned i=0;i<node.num_args();++i)children.push_back(booleans.at(node.arg(i).id()));booleans.emplace(id,kind==Z3_OP_AND?z3::mk_and(children):z3::mk_or(children));}
    }
    const auto own=lift(context,bound_poly,symbols,budget)>=context.real_val(0);z3::expr_vector assertions(context);for(unsigned i=0;i<hard.size();++i)assertions.push_back(booleans.at(hard[i].id()));assertions.push_back(own);
    auto guard=[&](const Fact& fact){auto value=fact.atom==source.atoms.size()?own:atoms.at(fact.atom);return fact.positive?value:!value;};
    for(const auto& cut:closure.cuts){budget.check();const auto polynomial=lift(context,cut.product,symbols,budget);assertions.push_back(cut.equality?z3::implies(atoms.at(cut.atom),polynomial==context.real_val(0)):z3::implies(guard(closure.facts.at(cut.left))&&guard(closure.facts.at(cut.right)),polynomial>=context.real_val(0)));}
    return assertions;
}
inline void json_guard(std::ostringstream& out,const Budget& budget){budget.check();require(out.tellp()>=0&&static_cast<uint64_t>(out.tellp())<=max_artifact,"native certificate byte cap");}
inline std::string certificate(const z3::expr_vector& hard,const z3::expr& objective,bool minimize,const mpz_class& best,const Source& source,const Closure& closure,const std::vector<Mapping>& mappings,const OmtGuardedProductResult& result,const Budget& budget){
    std::ostringstream out;out<<"{\"schema\":\"native-source-guarded-product-bound-v1\",\"source_hard\":[";
    for(unsigned i=0;i<hard.size();++i){json_guard(out,budget);if(i)out<<',';out<<quoted(hard[i].to_string());}
    out<<"],\"original_objective\":"<<quoted(objective.to_string())<<",\"direction\":"<<quoted(minimize?"min":"max")<<",\"own_best\":"<<quoted(best.get_str())<<",\"used_integer_names\":[";
    bool first=true;for(const auto& variable:source.variables){json_guard(out,budget);if(!first)out<<',';first=false;out<<quoted(variable.first);}out<<"],\"used_bool_names\":[],\"monomials\":[";
    for(size_t i=0;i<mappings.size();++i){json_guard(out,budget);if(i)out<<',';out<<"{\"name\":"<<quoted(mappings[i].name)<<",\"variables\":[";for(size_t j=0;j<mappings[i].variables.size();++j){if(j)out<<',';out<<quoted(mappings[i].variables[j]);}out<<"]}";}
    out<<"],\"cuts\":[";
    auto fact=[&](size_t index){const auto& value=closure.facts.at(index);out<<"{\"atom\":"<<quoted(value.atom==source.atoms.size()?"@bound":source.atoms.at(value.atom).text)<<",\"polarity\":"<<(value.positive?"true":"false")<<'}';};
    for(size_t i=0;i<closure.cuts.size();++i){json_guard(out,budget);if(i)out<<',';const auto& cut=closure.cuts[i];if(cut.equality)out<<"{\"kind\":\"equality_times_variable\",\"atom\":"<<quoted(source.atoms.at(cut.atom).text)<<",\"variable\":"<<quoted(cut.variable)<<'}';else{out<<"{\"kind\":\"nonnegative_product\",\"left\":";fact(cut.left);out<<",\"right\":";fact(cut.right);out<<'}';}}
    out<<"],\"query_path\":"<<quoted(result.query_path)<<",\"actual_query_smt2\":"<<quoted(result.actual_query_smt2)<<",\"truncations\":[";
    for(size_t i=0;i<closure.truncations.size();++i){if(i)out<<',';out<<quoted(closure.truncations[i]);}out<<"]}";json_guard(out,budget);return out.str();
}
inline void remove_owned(const std::string& path,const struct stat& owned){struct stat current{};if(::lstat(path.c_str(),&current)==0&&current.st_dev==owned.st_dev&&current.st_ino==owned.st_ino)::unlink(path.c_str());}
inline void persist(const std::string& path,const std::string& text,const Budget& budget){
    budget.check();int fd=::open(path.c_str(),O_WRONLY|O_CREAT|O_EXCL,0600);if(fd<0)throw std::runtime_error("cannot create requested guarded-product artifact without overwrite");struct stat owned{};if(::fstat(fd,&owned)!=0){::close(fd);throw std::runtime_error("cannot identify guarded-product artifact");}
    try{size_t offset=0;while(offset<text.size()){budget.check();const auto n=::write(fd,text.data()+offset,std::min<size_t>(65536,text.size()-offset));if(n<=0)throw std::runtime_error("cannot write guarded-product artifact");offset+=static_cast<size_t>(n);}if(::close(fd)!=0){fd=-1;throw std::runtime_error("cannot finish guarded-product artifact");}fd=-1;budget.check();}
    catch(...){if(fd>=0)::close(fd);remove_owned(path,owned);throw;}
}
inline void verify_file(const std::string& path,const std::string& expected,const Budget& budget){
    budget.check();require(!std::filesystem::is_symlink(path),"guarded-product artifact became symlink");std::ifstream in(path,std::ios::binary);require(bool(in),"missing guarded-product artifact");char buffer[65536];size_t offset=0;
    while(in){budget.check();in.read(buffer,sizeof(buffer));const auto count=static_cast<size_t>(in.gcount());require(offset+count<=expected.size()&&expected.compare(offset,count,buffer,count)==0,"guarded-product artifact changed");offset+=count;}require(in.eof()&&offset==expected.size(),"guarded-product artifact incomplete");budget.check();
}
inline void reject_late(OmtGuardedProductResult& result,bool late){if(late){result.answer=z3::unknown;result.reason="guarded-product deadline reached";}}
inline void run(z3::context& context,const z3::expr_vector& hard,const z3::expr& objective,bool minimize,const std::string& exact_best,const std::string& artifact_stem,OmtGuardedProductResult& result,const Budget& budget){
    budget.check();require(&hard.ctx()==&context&&&objective.ctx()==&context,"original ASTs must share supplied context");require(objective.is_int(),"original objective must be Int");
    const auto best=decimal(exact_best,[&](){budget.check();});Source source(budget);source.parse(hard,objective);const auto own=bound(source.polynomials.at(objective.id()),minimize,best,[&](){budget.check();});result.applicable=true;
    const auto generation_began=Clock::now();auto closure=select(source,own,budget);result.generation_seconds=std::chrono::duration<double>(Clock::now()-generation_began).count();result.pair_scans=closure.pairs;
    auto mappings=mapping(context,source,closure,budget);auto assertions=build(context,hard,source,own,closure,mappings,budget);z3::solver query(context,"QF_LRA");query.add(assertions);budget.check();
    result.cuts=closure.cuts.size();result.monomials=mappings.size();result.actual_query_smt2="(set-logic QF_LRA)\n"+query.to_smt2();require(result.actual_query_smt2.size()<=max_artifact,"actual query byte cap");
    if(!artifact_stem.empty()){const auto stem=std::filesystem::absolute(artifact_stem).lexically_normal().string();result.query_path=stem+".smt2";result.certificate_path=stem+".json";}
    result.certificate_json=certificate(hard,objective,minimize,best,source,closure,mappings,result,budget);
    if(!artifact_stem.empty()){persist(result.query_path,result.actual_query_smt2,budget);persist(result.certificate_path,result.certificate_json,budget);}
    budget.check();const auto milliseconds=static_cast<unsigned>(std::max(1.,std::min(4294967294.,1000.*budget.remaining())));query.set("timeout",milliseconds);result.setup_seconds=budget.elapsed();++result.queries;const auto check_began=Clock::now();
    z3::check_result answer=z3::unknown;try{answer=query.check();result.check_seconds=std::chrono::duration<double>(Clock::now()-check_began).count();}catch(...){result.check_seconds=std::chrono::duration<double>(Clock::now()-check_began).count();throw;}
    budget.check();if(!artifact_stem.empty()){verify_file(result.query_path,result.actual_query_smt2,budget);verify_file(result.certificate_path,result.certificate_json,budget);}budget.check();
    result.reason=answer==z3::unsat?"necessary full-source relaxation UNSAT; incumbent feasibility is caller responsibility":answer==z3::sat?"Real monomial relaxation SAT only; no original model":query.reason_unknown();result.answer=answer;
}
} // namespace guarded_product_detail
// Uses full unchanged original ASTs. Finite allowance only. SAT never returns a
// source model. A valid original incumbent is separately required for A2.
inline OmtGuardedProductResult solve_omt_guarded_product(z3::context& context,const z3::expr_vector& full_original_hard,const z3::expr& original_objective,bool minimize,const std::string& exact_best,double seconds,const std::string& artifact_stem={}){
    using namespace guarded_product_detail;OmtGuardedProductResult result;Budget budget(seconds);
    try{require(std::isfinite(seconds)&&seconds>0&&seconds<=1200,"guarded-product requires finite seconds in(0,1200]");run(context,full_original_hard,original_objective,minimize,exact_best,artifact_stem,result,budget);}
    catch(const Expired&){result.answer=z3::unknown;result.reason="guarded-product deadline reached";}
    catch(const Unsupported& error){result.applicable=false;result.answer=z3::unknown;result.reason=error.what();}
    catch(const std::exception& error){result.answer=z3::unknown;result.reason=std::string("guarded-product error: ")+error.what();}
    catch(...){result.answer=z3::unknown;result.reason="guarded-product unknown error";}
    // run()'s solver, parser caches and GMP work arrays are destroyed before
    // this final check, so their cleanup cannot turn a late answer into UNSAT.
    result.seconds=budget.elapsed();if(std::isfinite(seconds)&&seconds>0)reject_late(result,!(budget.remaining()>0));return result;
}
} // namespace nia
