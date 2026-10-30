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
        for(size_t k=0;k<dimension;++k){
            budget.check();mpq_class pivot=h[k][k];lower[k][k]=1;
            for(size_t j=0;j<k;++j){pivot-=lower[k][j]*lower[k][j]*diagonal[j];guard(pivot);}
            require(pivot>=0,"normalized source quadratic is not positive semidefinite");diagonal[k]=pivot;
            for(size_t i=k+1;i<dimension;++i){
                budget.check();mpq_class residual=h[i][k];
                for(size_t j=0;j<k;++j){residual-=lower[i][j]*lower[k][j]*diagonal[j];guard(residual);}
                if(pivot==0)require(residual==0,"zero LDL pivot has a nonzero remaining row");
                else {lower[i][k]=residual/pivot;guard(lower[i][k]);}
            }
        }
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
Cut tangent(const Quadratic& quadratic,const Integers& point,const Budget& budget){
    Cut cut{point,quadratic.b,quadratic.evaluate(point,budget)};
    for(size_t i=0;i<point.size();++i){
        budget.check();
        for(size_t j=0;j<point.size();++j){cut.coefficients[i]+=quadratic.h[i][j]*point[j];guard(cut.coefficients[i]);}
        cut.constant-=cut.coefficients[i]*point[i];guard(cut.constant);
    }return cut;
}
z3::expr cut_expression(z3::context& context,const std::vector<z3::expr>& variables,
                        const Cut& cut,const mpz_class& bound,const Budget& budget){
    z3::expr expression=context.int_val(cut.constant.get_str().c_str());
    for(size_t i=0;i<variables.size();++i){budget.check();if(cut.coefficients[i]!=0)
        expression=expression+context.int_val(cut.coefficients[i].get_str().c_str())*variables[i];}
    return expression<=context.int_val(bound.get_str().c_str());
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
    const std::vector<Cut>& cuts,z3::solver& query,uint64_t queries,const Budget& budget){
    budget.check();std::ostringstream out;const mpz_class bound=normalized_best-1;
    out<<"{\"version\":\"omt-convex-exchange-v1\",\"source_identity\":{\"hard\":";json_expressions(out,hard);
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
    out<<"},\"ldl\":{\"lower\":";json_matrix(out,quadratic.lower);out<<",\"diagonal\":";json_numbers(out,quadratic.diagonal);
    out<<"},\"incumbent\":{\"original_best\":"<<quoted(original_best.get_str())<<",\"normalized_best\":"<<quoted(normalized_best.get_str())
       <<",\"strict_bound\":"<<quoted(bound.get_str())<<"},\"cuts\":[";
    for(size_t i=0;i<cuts.size();++i){budget.check();if(i)out<<',';out<<"{\"point\":";json_numbers(out,cuts[i].point);
        out<<",\"coefficients\":";json_numbers(out,cuts[i].coefficients);out<<",\"constant\":"<<quoted(cuts[i].constant.get_str())<<'}';}
    z3::solver source(context);source.add(hard);
    source.add(minimize?objective<context.int_val(original_best.get_str().c_str()):objective>context.int_val(original_best.get_str().c_str()));
    out<<"],\"linear_query_smt2\":"<<quoted(query.to_smt2())<<",\"source_query_smt2\":"<<quoted(source.to_smt2())<<",\"queries\":"<<queries<<'}';
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
    const std::map<std::string,std::string>& incumbent_hints,double seconds,const std::string& certificate_output){
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
        Quadratic quadratic(polynomial,parser.variables.size(),budget);result.applicable=true;
        z3::solver query(context,"QF_LIA");query.add(linear);std::vector<Cut> cuts;
        Integers point(parser.variables.size());
        for(size_t i=0;i<point.size();++i){budget.check();const auto hint=incumbent_hints.find(parser.variables[i].decl().name().str());
            if(hint!=incumbent_hints.end())point[i]=decimal(hint->second,budget);}
        while(cuts.size()<max_cuts){
            budget.check();cuts.push_back(tangent(quadratic,point,budget));
            query.add(cut_expression(context,parser.variables,cuts.back(),bound,budget));++result.cuts;
            if(std::isfinite(seconds)){
                const double milliseconds=std::floor(budget.remaining()*1000);if(milliseconds<1)throw Expired{};
                query.set("timeout",unsigned(std::min(milliseconds,double(std::numeric_limits<unsigned>::max()))));
            }
            budget.check();++result.queries;const auto answer=query.check();budget.check();
            if(answer==z3::unknown){result.reason=query.reason_unknown();break;}
            if(answer==z3::unsat){
                auto proof=certificate(context,original_hard,original_objective,minimize,original_best,best,definition,definition_rhs.get(),
                    raw,linear,normalized,parser.variables,quadratic,cuts,query,result.queries,budget);
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
        if(result.answer==z3::unknown&&result.reason.empty())result.reason="convex exchange reached its 10000-cut resource guard";
    }catch(const Expired&){result.reason="convex exchange exhausted its time allowance";result.answer=z3::unknown;}
    catch(const Unsupported& error){result.reason=error.what();result.answer=z3::unknown;}
    catch(const z3::exception& error){result.reason=error.msg();result.answer=z3::unknown;}
    catch(const std::exception& error){result.reason=error.what();result.answer=z3::unknown;}
    result.seconds=budget.elapsed();
    if(result.seconds>=seconds){result.answer=z3::unknown;result.reason="convex exchange completed after its time allowance";}
    if(result.answer==z3::unknown){result.model.reset();result.certificate_json.clear();}
    return result;
}
}
