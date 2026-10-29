#include "nia_omt_cutset.h"
#include <gmpxx.h>
#include <algorithm>
#include <chrono>
#include <cmath>
#include <limits>
#include <map>
#include <stdexcept>
#include <utility>
#include <vector>

namespace nia {
namespace {
struct Expired {};
struct Unsupported : std::runtime_error { using std::runtime_error::runtime_error; };
using Monomial=std::map<unsigned,unsigned>;
using Polynomial=std::map<Monomial,mpz_class>;
struct Domain { bool lower=false,upper=false;mpz_class lo,hi; };
struct Row { mpz_class constant;std::map<unsigned,mpz_class> coefficients; };

class FiniteCover {
    z3::context& context;
    const z3::expr_vector& input;
    double allowance;
    std::chrono::steady_clock::time_point began=std::chrono::steady_clock::now();
    std::vector<z3::expr> variables,formula;
    std::map<unsigned,unsigned> variable_ids;
    std::map<unsigned,Polynomial> polynomial_cache;
    std::vector<Monomial> nonlinear;
    std::vector<Row> rows;
    std::vector<unsigned> cutset;
    OmtCutsetResult result;

    double remaining() const {
        return allowance-std::chrono::duration<double>(std::chrono::steady_clock::now()-began).count();
    }
    void check_time() const { if(!(remaining()>0))throw Expired{}; }
    static unsigned degree(const Monomial& monomial) {
        unsigned d=0;for(const auto& power:monomial)d+=power.second;return d;
    }
    static void add(Polynomial& to,const Polynomial& from,const mpz_class& scale=1) {
        for(const auto& term:from){auto& coefficient=to[term.first];coefficient+=scale*term.second;
            if(coefficient==0)to.erase(term.first);}
    }
    Polynomial multiply(const Polynomial& a,const Polynomial& b) const {
        Polynomial product;
        for(const auto& x:a)for(const auto& y:b){
            check_time();Monomial powers=x.first;
            for(const auto& power:y.first)powers[power.first]+=power.second;
            if(degree(powers)>32)throw Unsupported("polynomial degree exceeds finite-cover construction limit");
            product[powers]+=x.second*y.second;
            if(product.size()>8192)throw Unsupported("polynomial expansion exceeds finite-cover construction limit");
        }
        for(auto p=product.begin();p!=product.end();)if(p->second==0)p=product.erase(p);else ++p;
        return product;
    }
    Polynomial polynomial(const z3::expr& expression) {
        check_time();const unsigned id=expression.id();
        auto found=polynomial_cache.find(id);if(found!=polynomial_cache.end())return found->second;
        if(!expression.is_int())throw Unsupported("finite cover supports integer polynomial arithmetic only");
        Polynomial answer;
        if(expression.is_numeral())answer[{}]=mpz_class(Z3_get_numeral_string(context,expression),10);
        else if(expression.is_const()&&expression.decl().decl_kind()==Z3_OP_UNINTERPRETED){
            auto position=variable_ids.find(id);
            if(position==variable_ids.end()){
                unsigned v=variables.size();variables.push_back(expression);variable_ids.emplace(id,v);
                position=variable_ids.find(id);
            }
            answer[{{position->second,1}}]=1;
        }else if(expression.is_app()){
            const auto op=expression.decl().decl_kind();
            if(op==Z3_OP_ADD||op==Z3_OP_SUB||op==Z3_OP_UMINUS){
                for(unsigned i=0;i<expression.num_args();++i)
                    add(answer,polynomial(expression.arg(i)),op==Z3_OP_UMINUS||(op==Z3_OP_SUB&&i)?-1:1);
            }else if(op==Z3_OP_MUL){
                answer[{}]=1;
                for(unsigned i=0;i<expression.num_args();++i)answer=multiply(answer,polynomial(expression.arg(i)));
            }else if(op==Z3_OP_POWER&&expression.arg(1).is_numeral()){
                mpz_class exponent(Z3_get_numeral_string(context,expression.arg(1)),10);
                if(exponent<0||exponent>32)throw Unsupported("unsupported polynomial exponent");
                auto base=polynomial(expression.arg(0));answer[{}]=1;
                for(unsigned i=0;i<exponent.get_ui();++i)answer=multiply(answer,base);
            }else throw Unsupported("non-polynomial arithmetic in finite cover");
        }else throw Unsupported("unsupported arithmetic term in finite cover");
        polynomial_cache.emplace(id,answer);return answer;
    }
    void record_nonlinear(const Polynomial& p) {
        for(const auto& term:p)if(degree(term.first)>1)nonlinear.push_back(term.first);
    }
    static bool linear(const Polynomial& p) {
        for(const auto& term:p)if(degree(term.first)>1)return false;
        return true;
    }
    static Row row(const Polynomial& p,int sign,bool strict) {
        Row r;r.constant=strict?1:0;
        for(const auto& term:p){if(term.first.empty())r.constant+=sign*term.second;
            else r.coefficients[term.first.begin()->first]+=sign*term.second;}
        return r;
    }
    // A row is usable for domain propagation only when the full conjunction
    // implies it. Atoms below OR/implication/Boolean equality provide no bounds.
    void inspect(const z3::expr& e,bool unconditional,std::vector<Row>& output,bool collect=true) {
        check_time();
        if(e.is_true())return;
        if(e.is_false()){if(unconditional){Row r;r.constant=1;output.push_back(r);}return;}
        if(e.is_quantifier()||!e.is_app())throw Unsupported("non-quantifier-free formula in finite cover");
        const auto op=e.decl().decl_kind();
        if(op==Z3_OP_AND){for(const auto& child:e.args())inspect(child,unconditional,output,collect);return;}
        const bool negated=op==Z3_OP_NOT;
        const auto atom=negated?e.arg(0):e;
        const auto relation=atom.is_app()?atom.decl().decl_kind():Z3_OP_UNINTERPRETED;
        if(atom.is_app()&&atom.num_args()==2&&atom.arg(0).is_int()&&atom.arg(1).is_int()
           &&(relation==Z3_OP_LE||relation==Z3_OP_GE||relation==Z3_OP_LT||relation==Z3_OP_GT||relation==Z3_OP_EQ)){
            auto p=polynomial(atom.arg(0));add(p,polynomial(atom.arg(1)),-1);
            if(collect)record_nonlinear(p);
            if(unconditional&&linear(p)){
                if(relation==Z3_OP_EQ){if(!negated){output.push_back(row(p,1,false));output.push_back(row(p,-1,false));}}
                else {
                    bool reverse=relation==Z3_OP_GE||relation==Z3_OP_GT;
                    bool strict=relation==Z3_OP_LT||relation==Z3_OP_GT;
                    if(negated){reverse=!reverse;strict=!strict;}
                    output.push_back(row(p,reverse?-1:1,strict));
                }
            }
            return;
        }
        if(e.is_const()&&e.is_bool())return;
        if(op==Z3_OP_DISTINCT&&e.num_args()&&e.arg(0).is_int()){
            for(const auto& child:e.args()){auto p=polynomial(child);if(collect)record_nonlinear(p);}return;
        }
        if(op==Z3_OP_NOT||op==Z3_OP_OR||op==Z3_OP_IMPLIES||op==Z3_OP_IFF||op==Z3_OP_XOR||op==Z3_OP_EQ||op==Z3_OP_ITE){
            for(const auto& child:e.args()){
                if(!child.is_bool())throw Unsupported("arithmetic conditional in finite cover");
                inspect(child,false,output,collect);
            }
            return;
        }
        throw Unsupported("unsupported Boolean operator in finite cover");
    }
    bool propagate(std::vector<Domain>& domains,const std::vector<Row>& constraints) const {
        // A one-sided negative cycle can tighten forever without producing a
        // finite interval. Stopping propagation only weakens pruning, never the
        // cover: every bound obtained in these rounds is still entailed.
        bool changed=true;
        for(unsigned round=0;changed&&round<64;++round){changed=false;
            for(const auto& r:constraints){
                check_time();mpz_class minimum=r.constant;unsigned missing=0;
                for(const auto& term:r.coefficients){const auto& d=domains[term.first];
                    if(term.second>0?d.lower:d.upper)minimum+=term.second*(term.second>0?d.lo:d.hi);else ++missing;}
                if(!missing&&minimum>0)return false;
                for(const auto& term:r.coefficients){
                    if(term.second==0)continue;
                    auto& d=domains[term.first];const bool known=term.second>0?d.lower:d.upper;
                    if(missing>(known?0u:1u))continue;
                    mpz_class rest=minimum;
                    if(known)rest-=term.second*(term.second>0?d.lo:d.hi);
                    mpz_class bound,numerator=-rest;
                    if(term.second>0){
                        mpz_fdiv_q(bound.get_mpz_t(),numerator.get_mpz_t(),term.second.get_mpz_t());
                        if(!d.upper||bound<d.hi){d.upper=true;d.hi=bound;changed=true;}
                    }else{
                        mpz_cdiv_q(bound.get_mpz_t(),numerator.get_mpz_t(),term.second.get_mpz_t());
                        if(!d.lower||bound>d.lo){d.lower=true;d.lo=bound;changed=true;}
                    }
                    if(d.lower&&d.upper&&d.lo>d.hi)return false;
                }
            }
        }
        return true;
    }
    bool choose_cutset(const std::vector<Domain>& domains) {
        std::vector<bool> bounded(variables.size()),selected(variables.size());
        for(unsigned v=0;v<variables.size();++v)
            bounded[v]=domains[v].lower&&domains[v].upper&&domains[v].hi-domains[v].lo<=65535;
        for(const auto& monomial:nonlinear){
            unsigned unbounded_degree=0;
            for(const auto& power:monomial)if(!bounded[power.first])unbounded_degree+=power.second;
            if(unbounded_degree>1)return false;
        }
        for(;;){
            check_time();std::vector<double> scores(variables.size());bool covered=true;
            for(const auto& monomial:nonlinear){
                unsigned remaining_degree=0;
                for(const auto& power:monomial)if(!selected[power.first])remaining_degree+=power.second;
                if(remaining_degree<=1)continue;
                covered=false;
                for(const auto& power:monomial)if(bounded[power.first]&&!selected[power.first])
                    scores[power.first]+=power.second;
            }
            if(covered)return true;
            unsigned best=variables.size();double score=0;
            for(unsigned v=0;v<variables.size();++v)if(scores[v]>0){
                mpz_class width=domains[v].hi-domains[v].lo+1;
                const double candidate=scores[v]/std::log2(width.get_d()+1);
                if(candidate>score){score=candidate;best=v;}
            }
            if(best==variables.size())return false;
            selected[best]=true;cutset.push_back(best);
        }
    }
    bool affordable(const std::vector<Domain>& domains) const {
        constexpr uint64_t max_leaves=50000,max_nodes=200000;
        mpz_class product=1;
        for(unsigned v:cutset){product*=domains[v].hi-domains[v].lo+1;if(product>max_leaves)break;}
        if(product<=max_leaves)return true;
        uint64_t nodes=0,leaves=0;
        const auto start=std::chrono::steady_clock::now();
        const double seconds=std::min(2.0,remaining()*.1);
        auto count=[&](auto&& self,unsigned depth,std::vector<Domain> branch)->bool {
            check_time();
            if(++nodes>max_nodes||std::chrono::duration<double>(std::chrono::steady_clock::now()-start).count()>=seconds)return false;
            if(!propagate(branch,rows))return true;
            if(depth==cutset.size())return ++leaves<=max_leaves;
            const unsigned v=cutset[depth];const mpz_class upper=branch[v].hi;
            for(mpz_class value=branch[v].lo;value<=upper;++value){
                auto child=branch;child[v].lo=child[v].hi=value;
                if(!self(self,depth+1,std::move(child)))return false;
            }
            return true;
        };
        return count(count,0,domains);
    }
    z3::check_result leaf(std::vector<Domain> domains) {
        check_time();++result.branches;
        // Substituted ASTs die with each leaf; their numeric ids can be reused.
        // The cache must never associate a later leaf with an old polynomial.
        polynomial_cache.clear();
        z3::expr_vector from(context),to(context),residual(context);
        for(unsigned v=0;v<variables.size();++v)if(domains[v].lower&&domains[v].upper&&domains[v].lo==domains[v].hi){
            from.push_back(variables[v]);to.push_back(context.int_val(domains[v].lo.get_str().c_str()));}
        auto leaf_rows=rows;
        z3::goal goal(context);
        for(auto e:formula){check_time();e=e.substitute(from,to).simplify();residual.push_back(e);goal.add(e);inspect(e,true,leaf_rows,false);}
        if(!propagate(domains,leaf_rows))return z3::unsat;
        if(!z3::probe(context,"is-qflia")(goal)){
            result.reason="finite-cover residual is not QF_LIA";return z3::unknown;
        }
        z3::solver solver(context,"QF_LIA");solver.add(residual);
        for(unsigned i=0;i<from.size();++i)solver.add(from[i]==to[i]);
        const double left=remaining();
        if(std::isfinite(left)){
            const double milliseconds=std::floor(left*1000);
            if(milliseconds<1)throw Expired{};
            solver.set("timeout",unsigned(std::min(milliseconds,double(std::numeric_limits<unsigned>::max()))));
        }
        const auto answer=solver.check();check_time();
        if(answer==z3::sat)result.model=std::make_unique<z3::model>(solver.get_model());
        if(answer==z3::unknown)result.reason=solver.reason_unknown();
        return answer;
    }
    z3::check_result search(unsigned depth,std::vector<Domain> domains) {
        check_time();if(!propagate(domains,rows))return z3::unsat;
        if(depth==cutset.size())return leaf(std::move(domains));
        const unsigned v=cutset[depth];const mpz_class upper=domains[v].hi;
        for(mpz_class value=domains[v].lo;value<=upper;++value){
            check_time();auto branch=domains;branch[v].lower=branch[v].upper=true;branch[v].lo=branch[v].hi=value;
            const auto answer=search(depth+1,std::move(branch));
            if(answer!=z3::unsat)return answer;
        }
        return z3::unsat;
    }
public:
    FiniteCover(z3::context& c,const z3::expr_vector& f,double seconds):context(c),input(f),allowance(seconds){}
    OmtCutsetResult run() {
        try{
            check_time();
            for(auto e:input){check_time();e=e.simplify();formula.push_back(e);inspect(e,true,rows);}
            std::vector<Domain> domains(variables.size());
            if(!propagate(domains,rows)){result.applicable=true;result.answer=z3::unsat;}
            else if(!choose_cutset(domains))result.reason="no small finite variable cover eliminates every nonlinear monomial";
            else if(!affordable(domains))result.reason="finite cover exceeds bounded preflight effort";
            else {result.applicable=true;result.answer=search(0,std::move(domains));}
            check_time();
        }catch(const Expired&){result.answer=z3::unknown;result.model.reset();result.reason="finite-cover time allowance exhausted";}
        catch(const Unsupported& e){result.answer=z3::unknown;result.model.reset();result.reason=e.what();}
        catch(const z3::exception& e){result.answer=z3::unknown;result.model.reset();result.reason=e.msg();}
        return std::move(result);
    }
};
}

OmtCutsetResult solve_omt_finite_cutset(z3::context& context,const z3::expr_vector& formula,double seconds) {
    if(!(seconds>0)||std::isnan(seconds))return {};
    return FiniteCover(context,formula,seconds).run();
}
}
