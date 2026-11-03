#pragma once
#include <gmpxx.h>
#include <cstdint>
#include <functional>
#include <algorithm>
#include <chrono>
#include <cmath>
#include <limits>
#include <map>
#include <random>
#include <set>
#include <sstream>
#include <stdexcept>
#include <utility>
#include <vector>

namespace nia { namespace direction_sls {
struct Row {
    std::vector<std::pair<size_t, mpz_class>> terms;
    mpz_class rhs = 0;
    bool equality = false;
};
struct Options {
    double seconds = 1;
    uint64_t seed = 1;
    std::vector<mpz_class> initial;
    std::function<void()> checkpoint;
};
struct Result {
    bool found = false;
    bool expired = false;
    std::vector<mpz_class> direction;
    std::vector<uint64_t> row_pressure;
    uint64_t steps = 0;
};
namespace detail {
using Int = mpz_class;
using Rat = mpq_class;
using Terms = std::map<size_t, Int>;
using RationalTerms = std::map<size_t, Rat>;
using Clock = std::chrono::steady_clock;
struct Expired {};
struct TooLarge {};
class Budget {
    const Options& options;
    Clock::time_point start = Clock::now();
    uint64_t ticks = 0;
public:
    explicit Budget(const Options& o): options(o) {}
    void check() {
        if (options.checkpoint) options.checkpoint();
        if (!(std::chrono::duration<double>(Clock::now()-start).count() < options.seconds)) throw Expired{};
    }
    void pulse() { if ((++ticks & 255u)==0) check(); }
};
template<class Number>
inline void add(std::map<size_t,Number>& terms,size_t variable,const Number& value) {
    if(value==0)return;
    auto it=terms.find(variable);
    if(it==terms.end())terms.emplace(variable,value);
    else {it->second+=value;if(it->second==0)terms.erase(it);}
}
inline Int floor_div(const Int& a,const Int& b) {
    Int q;mpz_fdiv_q(q.get_mpz_t(),a.get_mpz_t(),b.get_mpz_t());return q;
}
inline Int ceil_div(const Int& a,const Int& b) {
    Int q;mpz_cdiv_q(q.get_mpz_t(),a.get_mpz_t(),b.get_mpz_t());return q;
}
inline double magnitude(const Int& value) {
    if(value==0)return 0;
    Int a=value<0?-value:value;
    const size_t bits=mpz_sizeinbase(a.get_mpz_t(),2);
    if(bits<900)return std::log1p(a.get_d());
    signed long exponent=0;const double fraction=mpz_get_d_2exp(&exponent,a.get_mpz_t());
    return std::log(fraction)+double(exponent)*0.6931471805599453094;
}
inline uint64_t increment(uint64_t value) {
    return value==std::numeric_limits<uint64_t>::max()?value:value+1;
}
struct LinearRow {
    std::vector<std::pair<size_t,Int>> terms;
    Int rhs;
    bool equality;
    size_t origin;
    double scale=0;
};
struct Basis {
    // Original coordinates as integer combinations of free search coordinates.
    std::vector<std::vector<std::pair<size_t,Int>>> expressions;
    std::vector<size_t> free_variables;
    std::vector<Int> column_scale;
};
inline Basis identity(size_t n) {
    Basis basis;basis.expressions.resize(n);basis.column_scale.assign(n,1);
    for(size_t v=0;v<n;++v){basis.expressions[v].emplace_back(v,1);basis.free_variables.push_back(v);}
    return basis;
}
inline Basis nullspace(const std::vector<LinearRow>& rows,size_t n,Budget& budget) {
    // This exact preprocessing only creates joint moves satisfying homogeneous
    // equalities. Feasibility of the remaining inequalities is searched below.
    for(const auto& row:rows)if(row.equality&&row.rhs!=0)return identity(n);
    std::map<size_t,RationalTerms> echelon;
    size_t cells=0;
    auto insert_equation=[&](RationalTerms work) {
        while(!work.empty()) {
            budget.pulse();const auto p=work.begin()->first;const auto previous=echelon.find(p);
            if(previous==echelon.end())break;
            const Rat multiplier=work.begin()->second;
            for(const auto& t:previous->second){budget.pulse();add(work,t.first,Rat(-multiplier*t.second));}
        }
        if(work.empty())return false;
        const Rat pivot=work.begin()->second;
        for(auto& t:work){budget.pulse();t.second/=pivot;}
        cells+=work.size();if(cells>1000000)throw TooLarge{};
        echelon.emplace(work.begin()->first,std::move(work));
        return true;
    };
    for(const auto& row:rows)if(row.equality) {
        budget.check();RationalTerms work;
        for(const auto& t:row.terms)work.emplace(t.first,Rat(t.second));
        insert_equation(std::move(work));
    }
    // Substitution can expose additional opposite homogeneous inequalities.
    // Saturate those exact equalities before constructing moves: checking
    // only the original rows misses jointly constrained coordinates hidden
    // behind aliases or already eliminated equality chains. This operation
    // creates an equality-preserving move space; remaining inequalities are
    // still handled exclusively by the local search below.
    for(;;) {
        std::map<std::string,std::pair<unsigned,RationalTerms>> signs;
        for(const auto& row:rows)if(!row.equality&&row.rhs==0) {
            budget.check();RationalTerms work;
            for(const auto& t:row.terms)work.emplace(t.first,Rat(t.second));
            for(auto it=work.begin();it!=work.end();) {
                budget.pulse();auto previous=echelon.find(it->first);
                if(previous==echelon.end()){++it;continue;}
                const size_t pivot=it->first;const Rat multiplier=it->second;
                for(const auto& t:previous->second){budget.pulse();add(work,t.first,Rat(-multiplier*t.second));}
                it=work.upper_bound(pivot);
            }
            if(work.empty())continue;
            const bool positive=work.begin()->second>0;
            // The sign is part of the key only through the coefficients;
            // normalize its orientation so opposite rows share one bucket.
            std::ostringstream canonical;
            const Rat leading=work.begin()->second;
            for(const auto& t:work){budget.pulse();canonical<<t.first<<':'<<Rat(t.second/leading)<<';';}
            auto& item=signs[canonical.str()];item.first|=positive?1u:2u;
            if(item.second.empty())item.second=std::move(work);
        }
        bool changed=false;
        for(const auto& item:signs)if(item.second.first==3)
            changed=insert_equation(item.second.second)||changed;
        if(!changed)break;
    }
    Basis basis;basis.expressions.resize(n);
    std::vector<RationalTerms> expression(n);
    for(size_t v=0;v<n;++v)if(!echelon.count(v)) {
        expression[v].emplace(basis.free_variables.size(),Rat(1));basis.free_variables.push_back(v);
    }
    for(auto it=echelon.rbegin();it!=echelon.rend();++it) {
        budget.check();const auto v=it->first;
        for(const auto& t:it->second)if(t.first!=v)
            for(const auto& other:expression[t.first]){budget.pulse();add(expression[v],other.first,Rat(-t.second*other.second));}
        cells+=expression[v].size();if(cells>1000000)throw TooLarge{};
    }
    basis.column_scale.assign(basis.free_variables.size(),Int(1));
    for(const auto& e:expression)for(const auto& t:e) {
        budget.pulse();const Int den=t.second.get_den();
        mpz_lcm(basis.column_scale[t.first].get_mpz_t(),basis.column_scale[t.first].get_mpz_t(),den.get_mpz_t());
    }
    for(size_t v=0;v<n;++v)for(const auto& t:expression[v]) {
        budget.pulse();const Rat coefficient=t.second*basis.column_scale[t.first];
        if(coefficient.get_den()!=1)throw std::logic_error("nonintegral direction basis");
        basis.expressions[v].emplace_back(t.first,coefficient.get_num());
    }
    return basis;
}
inline std::vector<Int> restore(const Basis& basis,const std::vector<Int>& point,Budget& budget) {
    std::vector<Int> direction(basis.expressions.size());
    for(size_t v=0;v<direction.size();++v)for(const auto& t:basis.expressions[v]){
        budget.pulse();direction[v]+=t.second*point[t.first];
    }
    return direction;
}
inline bool exact(const std::vector<LinearRow>& rows,const std::vector<Int>& point,Budget& budget) {
    for(const auto& row:rows) {
        budget.pulse();Int value=0;
        for(const auto& t:row.terms){budget.pulse();value+=t.second*point[t.first];}
        if(row.equality?value!=row.rhs:value>row.rhs)return false;
    }
    return true;
}
inline Int violation(const LinearRow& row,const Int& lhs) {
    Int delta=lhs-row.rhs;
    if(row.equality)return delta<0?Int(-delta):delta;
    return delta>0?delta:Int(0);
}
inline double cost(const LinearRow& row,const Int& lhs,uint64_t weight) {
    const Int bad=violation(row,lhs);if(bad==0)return 0;
    return double(weight)*(1.+std::max(0.,magnitude(bad)-row.scale));
}
}

inline Result solve(const std::vector<Row>& source,size_t n,const Options& options) {
    using namespace detail;
    Result result;result.direction.assign(n,0);result.row_pressure.assign(source.size(),0);
    if(!std::isfinite(options.seconds)||!(options.seconds>0)){result.expired=true;return result;}
    Budget budget(options);
    try {
        budget.check();std::vector<LinearRow> original;original.reserve(source.size());
        for(size_t i=0;i<source.size();++i) {
            Terms terms;
            for(const auto& t:source[i].terms){budget.pulse();if(t.first>=n)throw std::invalid_argument("direction variable index out of range");add(terms,t.first,t.second);}
            LinearRow row;row.rhs=source[i].rhs;row.equality=source[i].equality;row.origin=i;
            row.terms.assign(terms.begin(),terms.end());original.push_back(std::move(row));
        }
        Basis basis;
        try{basis=nullspace(original,n,budget);}catch(const TooLarge&){basis=identity(n);}
        const size_t dimension=basis.free_variables.size();
        std::vector<LinearRow> rows;
        for(const auto& original_row:original) {
            budget.check();Terms terms;
            for(const auto& t:original_row.terms)for(const auto& b:basis.expressions[t.first]){
                budget.pulse();add(terms,b.first,Int(t.second*b.second));
            }
            LinearRow row;row.rhs=original_row.rhs;row.equality=original_row.equality;row.origin=original_row.origin;
            Int divisor=0;for(const auto& t:terms)mpz_gcd(divisor.get_mpz_t(),divisor.get_mpz_t(),t.second.get_mpz_t());
            if(divisor>1&&mpz_divisible_p(row.rhs.get_mpz_t(),divisor.get_mpz_t())){
                row.rhs/=divisor;for(auto& t:terms)t.second/=divisor;
            }
            row.terms.assign(terms.begin(),terms.end());
            for(const auto& t:row.terms)row.scale=std::max(row.scale,magnitude(t.second));
            if(row.terms.empty()) {
                if(violation(row,Int(0))!=0){result.row_pressure[row.origin]=1;return result;}
            }else rows.push_back(std::move(row));
        }
        if(rows.empty()) {budget.check();result.found=exact(original,result.direction,budget);return result;}
        std::vector<std::vector<std::pair<size_t,Int>>> adjacent(dimension);
        for(size_t r=0;r<rows.size();++r)for(const auto& t:rows[r].terms)adjacent[t.first].emplace_back(r,t.second);
        std::vector<Int> point(dimension),lhs(rows.size());
        std::vector<uint64_t> weights(rows.size(),1),last_move(dimension,0);
        std::mt19937_64 rng(options.seed);
        std::vector<std::vector<Int>> starts;
        starts.emplace_back(dimension,0);
        if(options.initial.size()==n) {
            std::vector<Int> hint(dimension);
            for(size_t j=0;j<dimension;++j)hint[j]=options.initial[basis.free_variables[j]]/basis.column_scale[j];
            starts.push_back(std::move(hint));
        }
        starts.emplace_back(dimension,1);starts.emplace_back(dimension,-1);
        for(const auto& row:rows)if(row.rhs<0) {
            std::vector<Int> signs(dimension);
            for(const auto& t:row.terms)signs[t.first]=t.second>0?-1:1;
            starts.push_back(std::move(signs));
            if(starts.size()>=12)break;
        }
        double best_cost=std::numeric_limits<double>::infinity();
        std::vector<Int> best_point(dimension);
        auto evaluate=[&](const std::vector<Int>& x,std::vector<Int>* values=nullptr) {
            double total=0;
            for(size_t r=0;r<rows.size();++r){
                budget.pulse();Int v=0;for(const auto& t:rows[r].terms){budget.pulse();v+=t.second*x[t.first];}
                if(values)(*values)[r]=v;
                total+=cost(rows[r],v,weights[r]);
            }
            return total;
        };
        auto publish=[&](const std::vector<Int>& x) {
            auto candidate=restore(basis,x,budget);
            if(!exact(original,candidate,budget))return false;
            budget.check();result.direction=std::move(candidate);result.found=true;return true;
        };
        for(const auto& start:starts) {
            budget.check();const double score=evaluate(start);
            if(score==0&&publish(start))return result;
            if(score<best_cost){best_cost=score;best_point=start;}
        }
        result.direction=restore(basis,best_point,budget);
        point=best_point;double current=evaluate(point,&lhs);
        uint64_t stale=0,restarts=0;
        while(true) {
            budget.check();++result.steps;
            std::vector<size_t> unsatisfied;
            for(size_t r=0;r<rows.size();++r) {
                budget.pulse();if(violation(rows[r],lhs[r])!=0){
                    unsatisfied.push_back(r);result.row_pressure[rows[r].origin]=increment(result.row_pressure[rows[r].origin]);
                }
            }
            if(unsatisfied.empty()){if(publish(point))return result;throw std::logic_error("direction exact validation mismatch");}
            const size_t chosen=unsatisfied[rng()%unsatisfied.size()];
            size_t selected=dimension;Int selected_value=0;
            double selected_cost=std::numeric_limits<double>::infinity();
            const auto& terms=rows[chosen].terms;
            const size_t offset=terms.empty()?0:rng()%terms.size();
            for(size_t at=0;at<std::min<size_t>(terms.size(),128);++at) {
                budget.pulse();const auto& term=terms[(at+offset)%terms.size()];const size_t v=term.first;
                const Int residual=rows[chosen].rhs-lhs[chosen];
                std::vector<Int> candidates;
                candidates.push_back(point[v]+floor_div(residual,term.second));
                candidates.push_back(point[v]+ceil_div(residual,term.second));
                candidates.push_back(point[v]+1);candidates.push_back(point[v]-1);candidates.push_back(0);
                for(const auto& value:candidates) {
                    budget.pulse();if(value==point[v])continue;const Int delta=value-point[v];
                    double score=current;
                    for(const auto& a:adjacent[v]){
                        budget.pulse();score-=cost(rows[a.first],lhs[a.first],weights[a.first]);
                        score+=cost(rows[a.first],Int(lhs[a.first]+a.second*delta),weights[a.first]);
                    }
                    const bool taboo=result.steps-last_move[v]<2;
                    if(taboo&&score>=current&&selected<dimension)continue;
                    if(score<selected_cost-1e-9||(std::abs(score-selected_cost)<1e-9&&(rng()&1))){
                        selected=v;selected_value=value;selected_cost=score;
                    }
                }
            }
            if(selected==dimension)break;
            const Int delta=selected_value-point[selected];point[selected]=selected_value;last_move[selected]=result.steps;
            for(const auto& a:adjacent[selected]){budget.pulse();lhs[a.first]+=a.second*delta;}
            current=selected_cost;
            if(current<best_cost-1e-9){
                best_cost=current;best_point=point;stale=0;
                // Retain a heuristic point even if a later iteration exhausts
                // its slice. Only found=true marks an exactly checked model.
                result.direction=restore(basis,best_point,budget);
            }else ++stale;
            if(stale>0&&stale%4==0) {
                for(size_t r:unsatisfied)if(weights[r]<1000000)++weights[r];
                current=evaluate(point,&lhs);best_cost=evaluate(best_point);
            }
            if(stale>=64+dimension*2) {
                ++restarts;stale=0;
                if(restarts<starts.size())point=starts[restarts];
                else {
                    point=best_point;
                    const size_t changes=std::min<size_t>(dimension,1+restarts%8);
                    for(size_t j=0;j<changes;++j)if(dimension){const size_t v=rng()%dimension;point[v]+=int(rng()%7)-3;}
                    if(restarts%8==0)for(auto& x:point)x=int(rng()%3)-1;
                }
                current=evaluate(point,&lhs);
            }
        }
        result.direction=restore(basis,best_point,budget);
    }catch(const Expired&){result.expired=true;}
    return result;
}
}}
