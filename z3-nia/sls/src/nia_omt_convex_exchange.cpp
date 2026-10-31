#include "nia_omt_convex_exchange.h"
#include <gmpxx.h>
#include <algorithm>
#include <chrono>
#include <cmath>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <limits>
#include <set>
#include <sstream>
#include <stdexcept>
#include <unordered_map>
#include <vector>

namespace nia {
namespace {
using Clock=std::chrono::steady_clock;
constexpr size_t max_dimension=64, max_cuts=10000, max_bits=65536, max_ast_nodes=200000;
struct Expired {};
struct Unsupported:std::runtime_error { using std::runtime_error::runtime_error; };
struct Budget {
    Clock::time_point began=Clock::now(); double seconds;
    explicit Budget(double value):seconds(value){}
    double elapsed()const{return std::chrono::duration<double>(Clock::now()-began).count();}
    double remaining()const{return seconds-elapsed();}
    void check()const{if(!(remaining()>0))throw Expired{};}
};
void require(bool condition,const char* reason){if(!condition)throw Unsupported(reason);}
void guard(const mpz_class& value){require(mpz_sizeinbase(value.get_mpz_t(),2)<=max_bits,"integer arithmetic bit guard exceeded");}
void guard(const mpq_class& value){guard(value.get_num());guard(value.get_den());}
mpz_class decimal(const std::string& text,const Budget& budget){
    size_t at=!text.empty()&&(text[0]=='-'||text[0]=='+')?1:0;
    require(at<text.size(),"expected an exact decimal integer");
    // Bound input allocation before invoking GMP's decimal conversion.
    require(text.size()<=max_bits,"decimal input exceeds arithmetic resource guard");
    for(;at<text.size();++at){budget.check();require(text[at]>='0'&&text[at]<='9',"expected an exact decimal integer");}
    mpz_class value(text[0]=='+'?text.substr(1):text,10);guard(value);return value;
}
mpz_class numeral(const z3::expr& expression,const Budget& budget){
    require(expression.is_int()&&expression.is_numeral(),"expected a concrete exact integer model value");
    return decimal(Z3_get_numeral_string(expression.ctx(),expression),budget);
}
bool same(const z3::expr& a,const z3::expr& b){return Z3_is_eq_ast(a.ctx(),a,b);}
bool contains(const z3::expr& root,const z3::expr& target,const Budget& budget){
    std::vector<z3::expr> stack{root};std::set<unsigned> visited;
    while(!stack.empty()){
        budget.check();auto current=stack.back();stack.pop_back();
        if(same(current,target))return true;
        if(!visited.insert(current.id()).second)continue;
        require(visited.size()<=max_ast_nodes,"source AST node guard exceeded");
        require(!current.is_quantifier(),"quantified source definition is unsupported");
        if(current.is_app())for(unsigned i=0;i<current.num_args();++i)stack.push_back(current.arg(i));
    }return false;
}
using Monomial=std::vector<size_t>;
using Polynomial=std::map<Monomial,mpz_class>;
Polynomial constant(const mpz_class& value){return value==0?Polynomial{}:Polynomial{{{},value}};}
void accumulate(Polynomial& result,const Monomial& powers,const mpz_class& coefficient){
    require(powers.size()<=2,"source objective degree exceeds two");
    auto& value=result[powers];value+=coefficient;guard(value);if(value==0)result.erase(powers);
}
Polynomial add(Polynomial left,const Polynomial& right,int sign,const Budget& budget){
    for(const auto& term:right){budget.check();mpz_class coefficient=sign*term.second;accumulate(left,term.first,coefficient);}return left;
}
Polynomial multiply(const Polynomial& left,const Polynomial& right,const Budget& budget){
    Polynomial result;
    for(const auto& a:left)for(const auto& b:right){
        budget.check();Monomial powers=a.first;powers.insert(powers.end(),b.first.begin(),b.first.end());
        require(powers.size()<=2,"source objective degree exceeds two");std::sort(powers.begin(),powers.end());
        mpz_class coefficient=a.second*b.second;guard(coefficient);accumulate(result,powers,coefficient);
    }return result;
}
struct PolynomialParser {
    const Budget& budget;std::vector<z3::expr> variables;
    std::unordered_map<unsigned,size_t> indices;
    std::unordered_map<unsigned,Polynomial> cache;
    explicit PolynomialParser(const Budget& allowance):budget(allowance){}
    Polynomial parse(const z3::expr& expression,unsigned depth=0){
        budget.check();require(depth<=256,"source objective nesting guard exceeded");
        const auto found=cache.find(expression.id());if(found!=cache.end())return found->second;
        require(cache.size()<=max_ast_nodes,"source objective AST guard exceeded");
        require(expression.is_int()&&expression.is_app(),"source objective must be an integer polynomial");
        Polynomial result;
        if(expression.is_numeral())result=constant(numeral(expression,budget));
        else {
            const auto kind=expression.decl().decl_kind();
            if(kind==Z3_OP_UNINTERPRETED && expression.num_args()==0){
                auto index=indices.find(expression.id());
                if(index==indices.end()){
                    require(variables.size()<max_dimension,"source objective dimension exceeds 64");
                    index=indices.emplace(expression.id(),variables.size()).first;variables.push_back(expression);
                }
                result[Monomial{index->second}]=1;
            }else if(kind==Z3_OP_ADD){
                for(unsigned i=0;i<expression.num_args();++i)result=add(std::move(result),parse(expression.arg(i),depth+1),1,budget);
            }else if(kind==Z3_OP_SUB || kind==Z3_OP_UMINUS){
                require(expression.num_args()>0,"malformed subtraction");result=parse(expression.arg(0),depth+1);
                if(expression.num_args()==1)result=add({},result,-1,budget);
                else for(unsigned i=1;i<expression.num_args();++i)result=add(std::move(result),parse(expression.arg(i),depth+1),-1,budget);
            }else if(kind==Z3_OP_MUL){
                result=constant(1);
                for(unsigned i=0;i<expression.num_args();++i)result=multiply(result,parse(expression.arg(i),depth+1),budget);
            }else if(kind==Z3_OP_POWER && expression.num_args()==2){
                const auto exponent=numeral(expression.arg(1),budget);require(exponent>=0&&exponent<=2,"unsupported polynomial power");
                auto base=parse(expression.arg(0),depth+1);result=constant(1);
                for(unsigned i=0;i<exponent.get_ui();++i)result=multiply(result,base,budget);
            }else throw Unsupported("unsupported source objective operator");
        }
        cache.emplace(expression.id(),result);return result;
    }
};
using Integers=std::vector<mpz_class>;
using RationalMatrix=std::vector<std::vector<mpq_class>>;
struct Quadratic {
    mpz_class c=0;Integers b;std::vector<Integers> h;
    RationalMatrix lower;std::vector<mpq_class> diagonal;
    Quadratic(const Polynomial& polynomial,size_t dimension,const Budget& budget)
        :b(dimension),h(dimension,Integers(dimension)),lower(dimension,std::vector<mpq_class>(dimension)),diagonal(dimension){
        for(const auto& term:polynomial){
            budget.check();
            if(term.first.empty())c+=term.second;
            else if(term.first.size()==1)b[term.first[0]]+=term.second;
            else {const size_t i=term.first[0],j=term.first[1];
                if(i==j)h[i][i]+=2*term.second;else {h[i][j]+=term.second;h[j][i]+=term.second;}}
        }
    }
    // Exact LDL is only a PSD decision procedure; no denominator clearing or
    // derived variable bounds enter the linear relaxation.
    bool psd(const mpz_class& mu,const Budget& budget){
        const size_t dimension=b.size();
        RationalMatrix candidate(dimension,std::vector<mpq_class>(dimension));
        std::vector<mpq_class> pivots(dimension);
        for(size_t k=0;k<dimension;++k){
            budget.check();mpq_class pivot=mpq_class(h[k][k])-2*mu;candidate[k][k]=1;
            for(size_t j=0;j<k;++j){pivot-=candidate[k][j]*candidate[k][j]*pivots[j];guard(pivot);}
            if(pivot<0)return false;
            pivots[k]=pivot;
            for(size_t i=k+1;i<dimension;++i){
                budget.check();mpq_class residual=h[i][k];
                for(size_t j=0;j<k;++j){residual-=candidate[i][j]*candidate[k][j]*pivots[j];guard(residual);}
                if(pivot==0){if(residual!=0)return false;}
                else {candidate[i][k]=residual/pivot;guard(candidate[i][k]);}
            }
        }
        lower=std::move(candidate);diagonal=std::move(pivots);return true;
    }
    mpz_class integer_diagonal(const Budget& budget){
        if(b.empty()||!psd(1,budget))return 0;
        mpz_class safe=1,failed=0;unsigned checks=1;
        // Every published value was checked exactly. Exhausting this search
        // cap keeps the last proven value, with the same global deadline.
        while(checks<32){
            mpz_class candidate=2*safe;guard(candidate);++checks;
            if(!psd(candidate,budget)){failed=candidate;break;}
            safe=candidate;
        }
        while(failed>safe+1&&checks<32){
            mpz_class candidate=(safe+failed)/2;++checks;
            if(psd(candidate,budget))safe=candidate;else failed=candidate;
        }
        return safe;
    }
    mpz_class evaluate(const Integers& point,const Budget& budget)const{
        mpz_class value=c;
        for(size_t i=0;i<b.size();++i){
            budget.check();value+=b[i]*point[i]+(h[i][i]/2)*point[i]*point[i];guard(value);
            for(size_t j=i+1;j<b.size();++j){value+=h[i][j]*point[i]*point[j];guard(value);}
        }return value;
    }
};
struct Cut { Integers point,coefficients;mpz_class constant; };
Cut tangent(const Quadratic& quadratic,const Integers& point,const Budget& budget,const mpz_class& mu=0){
    Cut cut{point,quadratic.b,quadratic.evaluate(point,budget)};
    for(size_t i=0;i<point.size();++i){
        budget.check();
        for(size_t j=0;j<point.size();++j){cut.coefficients[i]+=quadratic.h[i][j]*point[j];guard(cut.coefficients[i]);}
        cut.coefficients[i]-=2*mu*point[i];guard(cut.coefficients[i]);
        cut.constant-=mu*point[i]*point[i]+cut.coefficients[i]*point[i];guard(cut.constant);
    }return cut;
}
z3::expr cut_expression(z3::context& context,const std::vector<z3::expr>& variables,
                        const Cut& cut,const mpz_class& bound,const Budget& budget,
                        const std::vector<z3::expr>& epigraph={},const mpz_class& mu=0){
    z3::expr expression=context.int_val(cut.constant.get_str().c_str());
    for(size_t i=0;i<variables.size();++i){budget.check();if(cut.coefficients[i]!=0)
        expression=expression+context.int_val(cut.coefficients[i].get_str().c_str())*variables[i];}
    for(const auto& square:epigraph){budget.check();expression=expression+context.int_val(mu.get_str().c_str())*square;}
    return expression<=context.int_val(bound.get_str().c_str());
}
struct SquareCut { size_t coordinate;mpz_class k,coefficient,constant; };
constexpr size_t max_affine_scale_bits=512;
struct AffineFactor { Integers coefficients;mpq_class weight; };
struct AffineSquareCut { size_t form;mpz_class k; };
struct AffineRelaxation {
    std::vector<AffineFactor> forms;mpz_class scale=1,scaled_constant,scaled_bound;
    Integers scaled_linear,scaled_weights;
};
AffineRelaxation affine_relaxation(Quadratic& quadratic,const mpz_class& mu,const mpz_class& bound,const Budget& budget){
    const size_t n=quadratic.b.size();AffineRelaxation result;
    require(n>0,"integer-affine objective has no variables");
    // Recompute the selected safe residual. The diagonal search may have
    // inspected other candidates; no search work array is treated as a proof.
    require(quadratic.psd(mu,budget),"integer-affine residual is not positive semidefinite");
    if(mu>0)for(size_t i=0;i<n;++i){
        budget.check();Integers coefficients(n);coefficients[i]=1;
        result.forms.push_back({std::move(coefficients),mpq_class(mu)});
    }
    for(size_t j=0;j<n;++j){
        budget.check();const auto& pivot=quadratic.diagonal[j];
        require(pivot>=0,"integer-affine residual has a negative exact pivot");
        if(pivot==0)continue;
        mpz_class denominator=1;
        for(size_t i=0;i<n;++i){budget.check();const auto divisor=quadratic.lower[i][j].get_den();
            mpz_lcm(denominator.get_mpz_t(),denominator.get_mpz_t(),divisor.get_mpz_t());guard(denominator);}
        Integers coefficients(n);mpz_class common=0;
        for(size_t i=0;i<n;++i){
            budget.check();const auto& coefficient=quadratic.lower[i][j];
            coefficients[i]=coefficient.get_num()*(denominator/coefficient.get_den());guard(coefficients[i]);
            mpz_gcd(common.get_mpz_t(),common.get_mpz_t(),coefficients[i].get_mpz_t());
        }
        require(common>0,"integer-affine factor has no nonzero coefficient");
        bool negative=false,found=false;
        for(auto& coefficient:coefficients){budget.check();coefficient/=common;
            if(!found&&coefficient!=0){negative=coefficient<0;found=true;}}
        if(negative)for(auto& coefficient:coefficients)coefficient=-coefficient;
        mpq_class multiplier(common,denominator);multiplier.canonicalize();
        mpq_class weight=(pivot/2)*multiplier*multiplier;weight.canonicalize();guard(weight);
        require(weight>0,"integer-affine weight is not positive");
        const auto divisor=weight.get_den();
        mpz_lcm(result.scale.get_mpz_t(),result.scale.get_mpz_t(),divisor.get_mpz_t());
        require(mpz_sizeinbase(result.scale.get_mpz_t(),2)<=max_affine_scale_bits,
                "integer-affine common-scale exceeds 512-bit guard");
        result.forms.push_back({std::move(coefficients),std::move(weight)});
    }
    require(!result.forms.empty()&&result.forms.size()<=2*n,"integer-affine factor count exceeds its resource guard");
    // Reconstruct the full source Q, including the retained coordinate terms.
    // Factor generation is not itself trusted as an exact source identity.
    for(size_t i=0;i<n;++i)for(size_t j=0;j<n;++j){
        budget.check();mpq_class actual=0;
        for(const auto& factor:result.forms){
            budget.check();mpq_class term=factor.weight*factor.coefficients[i]*factor.coefficients[j];guard(term);
            actual+=term;guard(actual);
        }
        require(actual==mpq_class(quadratic.h[i][j])/2,"integer-affine decomposition differs from source quadratic");
    }
    // Preflight every scaled objective value while mode selection can still
    // fall back. A small denominator alone does not bound scaled source data.
    result.scaled_constant=result.scale*quadratic.c;guard(result.scaled_constant);
    for(const auto& coefficient:quadratic.b){budget.check();mpz_class scaled=result.scale*coefficient;guard(scaled);
        result.scaled_linear.push_back(std::move(scaled));}
    for(const auto& factor:result.forms){budget.check();mpq_class scaled=factor.weight*result.scale;guard(scaled);
        require(scaled.get_den()==1,"integer-affine objective scale is not integral");
        result.scaled_weights.push_back(scaled.get_num());}
    result.scaled_bound=result.scale*bound;guard(result.scaled_bound);budget.check();
    return result;
}
mpz_class affine_value(const AffineFactor& factor,const Integers& point,const Budget& budget){
    mpz_class value=0;
    for(size_t i=0;i<point.size();++i){budget.check();mpz_class term=factor.coefficients[i]*point[i];guard(term);value+=term;guard(value);}
    return value;
}
z3::expr affine_bound(z3::context& context,const std::vector<z3::expr>& variables,
    const AffineRelaxation& affine,const std::vector<z3::expr>& epigraph,const Budget& budget){
    // All coefficients were checked before selecting this mode. Constructing
    // the query cannot introduce a new scaled-objective arithmetic rejection.
    z3::expr expression=context.int_val(affine.scaled_constant.get_str().c_str());
    for(size_t i=0;i<variables.size();++i){budget.check();const auto& coefficient=affine.scaled_linear[i];
        if(coefficient!=0)expression=expression+context.int_val(coefficient.get_str().c_str())*variables[i];}
    for(size_t i=0;i<affine.forms.size();++i){budget.check();
        expression=expression+context.int_val(affine.scaled_weights[i].get_str().c_str())*epigraph[i];}
    return expression<=context.int_val(affine.scaled_bound.get_str().c_str());
}
std::set<std::string> source_names(const z3::expr_vector& hard,const z3::expr& objective,const Budget& budget){
    std::set<std::string> names;std::set<unsigned> visited;std::vector<z3::expr> pending{objective};
    for(const auto& constraint:hard)pending.push_back(constraint);
    while(!pending.empty()){
        budget.check();auto current=pending.back();pending.pop_back();if(!visited.insert(current.id()).second)continue;
        require(visited.size()<=max_ast_nodes,"source AST node guard exceeded");
        if(current.is_app()){
            if(current.decl().decl_kind()==Z3_OP_UNINTERPRETED)names.insert(current.decl().name().str());
            for(unsigned i=0;i<current.num_args();++i)pending.push_back(current.arg(i));
        }
    }return names;
}
std::string quoted(const std::string& text){
    std::ostringstream out;out<<'"';
    for(unsigned char ch:text){
        if(ch=='"'||ch=='\\')out<<'\\'<<char(ch);
        else if(ch<32)out<<"\\u"<<std::hex<<std::setw(4)<<std::setfill('0')<<unsigned(ch)<<std::dec;
        else out<<char(ch);
    }out<<'"';return out.str();
}
template<typename Range> void json_numbers(std::ostream& out,const Range& values){
    out<<'[';bool first=true;for(const auto& value:values){if(!first)out<<',';first=false;out<<quoted(value.get_str());}out<<']';
}
void json_expressions(std::ostream& out,const z3::expr_vector& values){
    out<<'[';for(unsigned i=0;i<values.size();++i){if(i)out<<',';out<<quoted(values[i].to_string());}out<<']';
}
void json_matrix(std::ostream& out,const RationalMatrix& matrix){
    out<<'[';for(size_t i=0;i<matrix.size();++i){if(i)out<<',';json_numbers(out,matrix[i]);}out<<']';
}
std::string certificate(z3::context& context,const z3::expr_vector& hard,const z3::expr& objective,
    bool minimize,const mpz_class& original_best,const mpz_class& normalized_best,
    int definition,const z3::expr* rhs,const z3::expr_vector& raw,const z3::expr_vector& linear,
    const z3::expr& normalized,const std::vector<z3::expr>& variables,const Quadratic& quadratic,
    const std::vector<Cut>& cuts,z3::solver& query,uint64_t queries,const Budget& budget,
    const mpz_class& mu,const std::vector<z3::expr>& epigraph,const std::vector<SquareCut>& square_cuts,
    const AffineRelaxation* affine=nullptr,const std::vector<AffineSquareCut>& affine_cuts={}){
    budget.check();std::ostringstream out;const mpz_class bound=normalized_best-1;
    out<<"{\"version\":"<<quoted(affine?"omt-convex-exchange-v3":(mu>0?"omt-convex-exchange-v2":"omt-convex-exchange-v1"))<<",\"source_identity\":{\"hard\":";json_expressions(out,hard);
    out<<",\"objective\":"<<quoted(objective.to_string())<<",\"minimize\":"<<(minimize?"true":"false")<<"},\"projection\":";
    if(definition<0)out<<"null";
    else out<<"{\"definition_index\":"<<definition<<",\"variable\":"<<quoted(objective.to_string())<<",\"rhs\":"<<quoted(rhs->to_string())<<'}';
    out<<",\"projected_hard_raw\":";json_expressions(out,raw);out<<",\"linear_hard\":";json_expressions(out,linear);
    out<<",\"normalized_objective\":"<<quoted(normalized.to_string())<<",\"variables\":[";
    for(size_t i=0;i<variables.size();++i){if(i)out<<',';out<<quoted(variables[i].to_string());}
    out<<"],\"g\":{\"constant\":"<<quoted(quadratic.c.get_str())<<",\"linear\":";json_numbers(out,quadratic.b);
    RationalMatrix h(variables.size(),std::vector<mpq_class>(variables.size())),q=h;
    for(size_t i=0;i<variables.size();++i)for(size_t j=0;j<variables.size();++j){budget.check();h[i][j]=quadratic.h[i][j];q[i][j]=h[i][j]/2;}
    out<<",\"quadratic\":";json_matrix(out,q);out<<",\"hessian\":";json_matrix(out,h);
    out<<'}';
    if(affine){
        out<<",\"integer_affine\":{\"forms\":[";
        for(size_t i=0;i<affine->forms.size();++i){budget.check();if(i)out<<',';
            out<<"{\"coefficients\":";json_numbers(out,affine->forms[i].coefficients);
            out<<",\"weight\":"<<quoted(affine->forms[i].weight.get_str())<<'}';}
        out<<"],\"epigraph\":[";
        for(size_t i=0;i<epigraph.size();++i){if(i)out<<',';out<<quoted(epigraph[i].to_string());}
        out<<"],\"square_cuts\":[";
        for(size_t i=0;i<affine_cuts.size();++i){budget.check();if(i)out<<',';
            out<<"{\"form\":"<<affine_cuts[i].form<<",\"k\":"<<quoted(affine_cuts[i].k.get_str())<<'}';}
        out<<"]}";
    }else if(mu==0){
        out<<",\"ldl\":{\"lower\":";json_matrix(out,quadratic.lower);out<<",\"diagonal\":";json_numbers(out,quadratic.diagonal);out<<'}';
    }else{
        out<<",\"integer_diagonal\":{\"mu\":"<<quoted(mu.get_str())<<",\"epigraph\":[";
        for(size_t i=0;i<epigraph.size();++i){if(i)out<<',';out<<quoted(epigraph[i].to_string());}
        out<<"],\"square_cuts\":[";
        for(size_t i=0;i<square_cuts.size();++i){budget.check();const auto& cut=square_cuts[i];if(i)out<<',';
            out<<"{\"coordinate\":"<<cut.coordinate<<",\"k\":"<<quoted(cut.k.get_str())
               <<",\"coefficient\":"<<quoted(cut.coefficient.get_str())<<",\"constant\":"<<quoted(cut.constant.get_str())<<'}';}
        out<<"]}";
    }
    out<<",\"incumbent\":{\"original_best\":"<<quoted(original_best.get_str())<<",\"normalized_best\":"<<quoted(normalized_best.get_str())
       <<",\"strict_bound\":"<<quoted(bound.get_str())<<'}';
    if(!affine){
        out<<",\"cuts\":[";
        for(size_t i=0;i<cuts.size();++i){budget.check();if(i)out<<',';out<<"{\"point\":";json_numbers(out,cuts[i].point);
            out<<",\"coefficients\":";json_numbers(out,cuts[i].coefficients);out<<",\"constant\":"<<quoted(cuts[i].constant.get_str())<<'}';}
        out<<']';
    }
    z3::solver source(context);source.add(hard);
    source.add(minimize?objective<context.int_val(original_best.get_str().c_str()):objective>context.int_val(original_best.get_str().c_str()));
    out<<",\"linear_query_smt2\":"<<quoted(query.to_smt2())<<",\"source_query_smt2\":"<<quoted(source.to_smt2())<<",\"queries\":"<<queries<<'}';
    budget.check();return out.str();
}
void save_certificate(const std::string& path,const std::string& data,const Budget& budget){
    if(path.empty())return;
    budget.check();std::ofstream out(path,std::ios::binary|std::ios::trunc);
    if(!out)throw std::runtime_error("cannot open requested convex certificate output");
    out<<data;out.close();if(!out)throw std::runtime_error("cannot finish writing convex certificate");budget.check();
}
}

OmtConvexExchangeResult solve_omt_convex_exchange(z3::context& context,const z3::expr_vector& original_hard,
    const z3::expr& original_objective,bool minimize,const std::string& exact_best,
    const std::map<std::string,std::string>& incumbent_hints,double seconds,const std::string& certificate_output,bool integer_diagonal,bool integer_affine){
    OmtConvexExchangeResult result;Budget budget(seconds);
    try{
        require(!std::isnan(seconds)&&seconds>0,"convex exchange requires a positive finite or infinite budget");budget.check();
        require(&original_hard.ctx()==&context&&&original_objective.ctx()==&context,"source ASTs must share the supplied Z3 context");
        require(original_objective.is_int(),"source objective is not integer-valued");
        const mpz_class original_best=decimal(exact_best,budget),best=minimize?original_best:mpz_class(-original_best),bound=best-1;
        z3::expr projected_objective=original_objective;
        std::unique_ptr<z3::expr> definition_rhs;int definition=-1;
        for(unsigned i=0;i<original_hard.size();++i){
            budget.check();const auto& constraint=original_hard[i];require(constraint.is_bool(),"source hard constraint is not Boolean");
            if(original_objective.is_const()&&original_objective.decl().decl_kind()==Z3_OP_UNINTERPRETED && constraint.is_eq()&&constraint.num_args()==2){
                const bool left=same(constraint.arg(0),original_objective),right=same(constraint.arg(1),original_objective);
                if(left||right){
                    require(definition<0,"source objective has multiple direct definitions");
                    const auto rhs=constraint.arg(left?1:0);
                    require(!contains(rhs,original_objective,budget),"source objective definition is self-referential");
                    definition=int(i);definition_rhs=std::make_unique<z3::expr>(rhs);projected_objective=rhs;
                }
            }
        }
        z3::expr_vector from(context),to(context),raw(context),linear(context);
        if(definition>=0){from.push_back(original_objective);to.push_back(*definition_rhs);}
        z3::goal goal(context);
        for(unsigned i=0;i<original_hard.size();++i)if(int(i)!=definition){
            budget.check();auto constraint=original_hard[i];if(definition>=0)constraint=constraint.substitute(from,to);
            raw.push_back(constraint);constraint=constraint.simplify();linear.push_back(constraint);goal.add(constraint);
        }
        budget.check();require(z3::probe(context,"is-qflia")(goal)!=0,"projected source hard formula is not QF_LIA");
        z3::expr normalized=minimize?projected_objective:-projected_objective;
        PolynomialParser parser(budget);const auto polynomial=parser.parse(normalized);
        Quadratic quadratic(polynomial,parser.variables.size(),budget);
        const mpz_class mu=(integer_diagonal||integer_affine)?quadratic.integer_diagonal(budget):mpz_class(0);
        if(mu==0)require(quadratic.psd(0,budget),"normalized source quadratic is not positive semidefinite");
        result.applicable=true;
        std::unique_ptr<AffineRelaxation> affine;std::string affine_fallback;
        if(integer_affine){
            try{affine=std::make_unique<AffineRelaxation>(affine_relaxation(quadratic,mu,bound,budget));}
            catch(const Unsupported& error){affine_fallback=std::string(" affine-fallback=")+error.what();}
        }
        std::string mode=std::string(affine?"integer-affine":(mu>0?"integer-diagonal":"aggregate-tangent"))+" mu="+mu.get_str();
        if(affine)mode+=" forms="+std::to_string(affine->forms.size())+" scale_bits="+std::to_string(mpz_sizeinbase(affine->scale.get_mpz_t(),2));
        else mode+=affine_fallback;
        result.reason=mode;
        z3::solver query(context,"QF_LIA");query.add(linear);std::vector<Cut> cuts;
        std::vector<z3::expr> epigraph,affine_forms;std::vector<SquareCut> square_cuts;
        std::vector<AffineSquareCut> affine_cuts;
        std::set<std::pair<size_t,mpz_class>> square_seen;
        if(affine||mu>0){
            auto names=source_names(original_hard,original_objective,budget);size_t serial=0;
            const size_t count=affine?affine->forms.size():parser.variables.size();
            for(size_t i=0;i<count;++i){
                std::string name;
                do {budget.check();name=std::string(affine?"__omt_affine_square!":"__omt_integer_square!")+std::to_string(serial++);}while(names.count(name));
                names.insert(name);epigraph.push_back(context.int_const(name.c_str()));query.add(epigraph.back()>=0);
                if(affine){
                    auto expression=context.int_val(0);
                    for(size_t j=0;j<parser.variables.size();++j){budget.check();const auto& coefficient=affine->forms[i].coefficients[j];
                        if(coefficient!=0)expression=expression+context.int_val(coefficient.get_str().c_str())*parser.variables[j];}
                    affine_forms.push_back(expression);
                }
            }
        }
        Integers point(parser.variables.size());
        for(size_t i=0;i<point.size();++i){budget.check();const auto hint=incumbent_hints.find(parser.variables[i].decl().name().str());
            if(hint!=incumbent_hints.end())point[i]=decimal(hint->second,budget);}
        if(affine){query.add(affine_bound(context,parser.variables,*affine,epigraph,budget));++result.cuts;}
        while(result.cuts<max_cuts){
            budget.check();
            if(affine){
                for(size_t i=0;i<affine->forms.size();++i){
                    const mpz_class value=affine_value(affine->forms[i],point,budget);
                    for(unsigned offset=0;offset<2;++offset){
                        budget.check();const mpz_class k=value-offset;
                        if(!square_seen.emplace(i,k).second)continue;
                        if(result.cuts>=max_cuts)throw Unsupported("convex exchange reached its 10000-cut resource guard");
                        mpz_class slope=2*k+1,intercept=-k*(k+1);guard(slope);guard(intercept);
                        for(const auto& coefficient:affine->forms[i].coefficients){budget.check();mpz_class expanded=slope*coefficient;guard(expanded);}
                        query.add(epigraph[i]>=context.int_val(slope.get_str().c_str())*affine_forms[i]+context.int_val(intercept.get_str().c_str()));
                        affine_cuts.push_back({i,k});++result.cuts;
                    }
                }
            }else if(mu>0)for(size_t i=0;i<point.size();++i)for(unsigned offset=0;offset<2;++offset){
                budget.check();const mpz_class k=point[i]-offset;
                if(!square_seen.emplace(i,k).second)continue;
                if(result.cuts>=max_cuts)throw Unsupported("convex exchange reached its 10000-cut resource guard");
                SquareCut cut{i,k,2*k+1,-k*(k+1)};guard(cut.coefficient);guard(cut.constant);
                query.add(epigraph[i]>=context.int_val(cut.coefficient.get_str().c_str())*parser.variables[i]+context.int_val(cut.constant.get_str().c_str()));
                square_cuts.push_back(std::move(cut));++result.cuts;
            }
            if(!affine){
                if(result.cuts>=max_cuts)throw Unsupported("convex exchange reached its 10000-cut resource guard");
                cuts.push_back(tangent(quadratic,point,budget,mu));
                query.add(cut_expression(context,parser.variables,cuts.back(),bound,budget,epigraph,mu));++result.cuts;
            }
            if(std::isfinite(seconds)){
                const double milliseconds=std::floor(budget.remaining()*1000);if(milliseconds<1)throw Expired{};
                query.set("timeout",unsigned(std::min(milliseconds,double(std::numeric_limits<unsigned>::max()))));
            }
            budget.check();++result.queries;const auto answer=query.check();budget.check();
            if(answer==z3::unknown){result.reason=mode+": "+query.reason_unknown();break;}
            if(answer==z3::unsat){
                auto proof=certificate(context,original_hard,original_objective,minimize,original_best,best,definition,definition_rhs.get(),
                    raw,linear,normalized,parser.variables,quadratic,cuts,query,result.queries,budget,mu,epigraph,square_cuts,affine.get(),affine_cuts);
                save_certificate(certificate_output,proof,budget);budget.check();
                result.certificate_json=std::move(proof);result.answer=z3::unsat;break;
            }
            auto model=std::make_unique<z3::model>(query.get_model());
            for(size_t i=0;i<point.size();++i){budget.check();point[i]=numeral(model->eval(parser.variables[i],true),budget);}
            const mpz_class value=quadratic.evaluate(point,budget);
            if(value<best){
                const mpz_class original_value=minimize?value:mpz_class(-value);
                if(definition>=0){auto declaration=original_objective.decl();auto concrete=context.int_val(original_value.get_str().c_str());model->add_const_interp(declaration,concrete);}
                for(const auto& constraint:original_hard){budget.check();require(model->eval(constraint,true).is_true(),"restored source model fails an original hard constraint");}
                require(numeral(model->eval(original_objective,true),budget)==original_value,"restored source objective differs from GMP evaluation");
                budget.check();result.model=std::move(model);result.answer=z3::sat;break;
            }
        }
        if(result.answer==z3::unknown&&result.reason==mode)result.reason=mode+": convex exchange reached its 10000-cut resource guard";
    }catch(const Expired&){result.reason+=(result.reason.empty()?"":": ")+std::string("convex exchange exhausted its time allowance");result.answer=z3::unknown;}
    catch(const Unsupported& error){result.reason+=(result.reason.empty()?"":": ")+std::string(error.what());result.answer=z3::unknown;}
    catch(const z3::exception& error){result.reason+=(result.reason.empty()?"":": ")+std::string(error.msg());result.answer=z3::unknown;}
    catch(const std::exception& error){result.reason+=(result.reason.empty()?"":": ")+std::string(error.what());result.answer=z3::unknown;}
    result.seconds=budget.elapsed();
    if(result.seconds>=seconds){result.answer=z3::unknown;result.reason+=(result.reason.empty()?"":": ")+std::string("convex exchange completed after its time allowance");}
    if(result.answer==z3::unknown){result.model.reset();result.certificate_json.clear();}
    return result;
}
}
