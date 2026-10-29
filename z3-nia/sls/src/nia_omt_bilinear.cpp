#include "nia_omt_bilinear.h"
#include "nia_ls.h"
#include "nia_omt_input.h"
#include "nia_lia_sls.h"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <deque>
#include <functional>
#include <iterator>
#include <map>
#include <random>
#include <set>
#include <stdexcept>
#include <utility>

namespace nia {
namespace {
using Int=mpz_class;
using Clock=std::chrono::steady_clock;
using Values=OmtBilinearRepair::Assignment;
Int exact(__int128_t value){return Int(format_omt_integer(value),10);}
struct Monomial {Int coefficient;std::vector<std::pair<size_t,unsigned>> factors;};
struct Polynomial {Int constant=0;std::vector<Monomial> terms;};
Int evaluate(const Polynomial& p,const Values& x){
    Int result=p.constant;
    for(const auto& term:p.terms){
        Int value=term.coefficient;
        for(auto f:term.factors){Int q;mpz_pow_ui(q.get_mpz_t(),exact(x[f.first]).get_mpz_t(),f.second);value*=q;}
        result+=value;
    }
    return result;
}
struct Score {
    bool objective_bad=true;
    size_t unsat=SIZE_MAX;
    double distance=std::numeric_limits<double>::infinity();
    bool operator<(const Score& other)const{
        if(objective_bad!=other.objective_bad)return !objective_bad;
        if(unsat!=other.unsat)return unsat<other.unsat;
        return distance<other.distance-1e-9;
    }
    bool feasible()const{return !objective_bad&&unsat==0;}
};
}

std::vector<std::vector<size_t>> bilinear_detail::component_cover_portfolio(
    const std::vector<BipartiteComponent>& components,std::mt19937& rng,size_t maximum_covers){
    const size_t count=components.size();
    const size_t desired=count<6?std::min(maximum_covers,size_t(1)<<count):maximum_covers;
    std::vector<std::vector<size_t>> result;std::vector<std::vector<unsigned char>> orientations;
    auto append=[&](const std::vector<unsigned char>& orientation){
        if(result.size()>=desired||std::find(orientations.begin(),orientations.end(),orientation)!=orientations.end())return;
        std::vector<size_t> cover;
        for(size_t i=0;i<count;++i){const auto& side=components[i].colors[orientation[i]];cover.insert(cover.end(),side.begin(),side.end());}
        orientations.push_back(orientation);
        result.push_back(std::move(cover));
    };
    // Keep both global orientations before trying component-wise mixtures.
    append(std::vector<unsigned char>(count,0));append(std::vector<unsigned char>(count,1));
    std::vector<size_t> order;for(size_t i=0;i<count;++i)order.push_back(i);
    std::shuffle(order.begin(),order.end(),rng);
    for(size_t k=0;k<std::min(count,size_t(4))&&result.size()<desired;++k){
        std::vector<unsigned char> orientation(count,0);orientation[order[k]]=1;append(orientation);
        for(auto& color:orientation)color=1-color;
        append(orientation);
    }
    // A fixed cap bounds construction work even when the full orientation
    // space is exponential. Every candidate still fixes a whole color class.
    for(unsigned trial=0;trial<256&&result.size()<desired;++trial){
        std::vector<unsigned char> orientation(count);for(auto& color:orientation)color=rng()&1u;
        append(orientation);
    }
    return result;
}

std::vector<std::vector<size_t>> bilinear_detail::polynomial_cover_portfolio(
    const std::vector<MonomialFactors>& monomials,const Values& lo,const Values& hi,
    const std::vector<size_t>& objective_variables,std::mt19937& rng,size_t maximum_covers){
    const size_t count=lo.size();
    maximum_covers=std::min(maximum_covers,size_t(32));
    if(!maximum_covers||hi.size()!=count)return {};
    for(size_t v=0;v<count;++v)if(lo[v]>hi[v])return {};
    std::vector<bool> mandatory(count,false),active(count,false),objective(count,false);
    for(size_t v:objective_variables){if(v>=count)return {};objective[v]=true;}
    std::vector<std::vector<size_t>> graph(count);
    for(const auto& term:monomials){
        bool zero=false;
        for(auto f:term){
            if(f.first>=count||!f.second)return {};
            if(lo[f.first]==hi[f.first]&&lo[f.first]==0)zero=true;
        }
        if(zero)continue;
        std::map<size_t,unsigned> powers;unsigned degree=0;
        for(auto f:term)if(lo[f.first]!=hi[f.first]){
            if(f.second>64||degree>64-f.second)return {};
            degree+=f.second;powers[f.first]+=f.second;
        }
        for(auto f:powers){active[f.first]=true;mandatory[f.first]=mandatory[f.first]||f.second>1;}
        for(auto a=powers.begin();a!=powers.end();++a)for(auto b=std::next(a);b!=powers.end();++b){
            graph[a->first].push_back(b->first);graph[b->first].push_back(a->first);
        }
    }
    for(auto& adjacent:graph){std::sort(adjacent.begin(),adjacent.end());adjacent.erase(std::unique(adjacent.begin(),adjacent.end()),adjacent.end());}
    std::vector<size_t> order;
    for(size_t v=0;v<count;++v)if(active[v]&&!mandatory[v])order.push_back(v);
    const auto base=order;
    std::vector<std::vector<size_t>> result;
    for(unsigned trial=0;trial<32&&result.size()<maximum_covers;++trial){
        order=base;
        if(trial<2){
            std::sort(order.begin(),order.end(),[&](size_t a,size_t b){
                if(objective[a]!=objective[b])return objective[a]>objective[b];
                if(graph[a].size()!=graph[b].size())return trial==0?graph[a].size()<graph[b].size():graph[a].size()>graph[b].size();
                return trial==0?a<b:a>b;
            });
        }else{
            std::shuffle(order.begin(),order.end(),rng);
            if(trial%4)std::stable_partition(order.begin(),order.end(),[&](size_t v){return objective[v];});
        }
        std::vector<bool> free(count,false);
        for(size_t v:order){bool compatible=true;for(size_t w:graph[v])if(free[w]){compatible=false;break;}if(compatible)free[v]=true;}
        std::vector<size_t> cover;
        for(size_t v=0;v<count;++v)if(active[v]&&!free[v])cover.push_back(v);
        if(std::find(result.begin(),result.end(),cover)==result.end())result.push_back(std::move(cover));
        if(base.empty())break;
    }
    return result;
}

void bilinear_detail::PrivateNeutralHistory::reset(const Assignment& current){seen_.assign(1,current);used_=0;}
bool bilinear_detail::PrivateNeutralHistory::retain(const Assignment& candidate,
    bool strict_improvement,bool same_level){
    if(strict_improvement){reset(candidate);return true;}
    if(!same_level||used_>=limit_||std::find(seen_.begin(),seen_.end(),candidate)!=seen_.end())return false;
    seen_.push_back(candidate);++used_;++total_;return true;
}

Values bilinear_detail::finite_block_trial(const Values& seed,const Values& lo,
    const Values& hi,const std::vector<size_t>& finite,uint64_t variant){
    Values trial=seed;
    if(!variant||finite.empty())return trial;
    const size_t width=std::min(size_t(3),finite.size());
    std::vector<uint64_t> spans;uint64_t cycle=0;
    for(size_t start=0;start<finite.size();++start){
        uint64_t product=1;
        for(size_t k=0;k<width;++k){
            const size_t v=finite[(start+k)%finite.size()];
            if(v>=seed.size()||v>=lo.size()||v>=hi.size()||lo[v]>hi[v])return seed;
            const Int card=exact(hi[v])-exact(lo[v])+1;
            if(card>9)return seed;
            product*=card.get_ui();
        }
        const uint64_t span=product+2;
        if(cycle>UINT64_MAX-span)return seed;
        cycle+=span;spans.push_back(span);
    }
    uint64_t offset=(variant-1)%cycle;size_t start=0;
    while(offset>=spans[start])offset-=spans[start++];
    uint64_t digits=offset>=2?offset-2:0;
    for(size_t k=0;k<width;++k){
        size_t v=finite[(start+k)%finite.size()];
        if(offset<2){
            Int q=offset==0?Int(1):Int(-1);
            q=std::max(exact(lo[v]),std::min(exact(hi[v]),q));
            if(q==0){q=offset==0?Int(-1):Int(1);q=std::max(exact(lo[v]),std::min(exact(hi[v]),q));}
            parse_omt_integer(q.get_str(),trial[v]);
        }else{
            const Int card=exact(hi[v])-exact(lo[v])+1;const unsigned base=card.get_ui();
            Int q=exact(lo[v])+unsigned(digits%base);digits/=base;
            parse_omt_integer(q.get_str(),trial[v]);
        }
    }
    return trial;
}

Values bilinear_detail::anchored_trial(const Values& seed,const Values& incumbent,const Values& lo,
    const Values& hi,const std::vector<size_t>& finite,
    const std::vector<size_t>& wide,uint64_t variant){
    if(!variant)return seed;
    if(seed.size()!=lo.size()||seed.size()!=hi.size()||seed.size()!=incumbent.size())return seed;
    for(size_t v:finite)if(v>=seed.size()||lo[v]>hi[v])return seed;
    for(size_t v:wide)if(v>=seed.size()||lo[v]>hi[v])return seed;
    if(wide.empty())return finite_block_trial(seed,lo,hi,finite,variant);
    const uint64_t block=(variant-1)/11,slot=(variant-1)%11;
    const uint64_t legacy=block*8+std::min(slot,uint64_t(7))+1;
    Values trial=finite_block_trial(seed,lo,hi,finite,legacy);
    size_t v;Int q;
    if(slot<8){
        v=wide[((legacy-1)/2)%wide.size()];
        q=legacy%2?Int(1):Int(-1);
        q*=Int(1)<<std::min(uint64_t(80),(legacy-1)/2/wide.size());
    }else{
        v=wide[block%wide.size()];
        const Int radius=Int(1)<<std::min(uint64_t(80),block/wide.size());
        if(slot==8)q=0;
        else if(slot==9)q=exact(incumbent[v])+radius;
        else q=exact(incumbent[v])-radius;
    }
    q=std::max(exact(lo[v]),std::min(exact(hi[v]),q));
    if(!parse_omt_integer(q.get_str(),trial[v]))return seed;
    return trial;
}

struct OmtBilinearRepair::Impl {
    bool valid=false,minimize=true;
    size_t count=0;
    Values own_seed,working,lo,hi;
    Int own_best;
    Polynomial objective;
    std::vector<Polynomial> literals;
    std::vector<bool> arithmetic,equality,integer_variable;
    std::vector<size_t> boolean_variable;
    std::vector<std::vector<int>> clauses;
    std::vector<std::vector<size_t>> graph;
    std::vector<bilinear_detail::BipartiteComponent> components;
    std::vector<std::vector<size_t>> covers;
    std::vector<uint64_t> cover_attempts;
    std::set<size_t> objective_variables;
    std::mt19937 rng,anchor_rng;
    uint64_t rounds=0;
    Score working_score;
    bilinear_detail::PrivateNeutralHistory history;

    Impl(const ls_solver& source,const Values& seed,const std::string& own_value,
         const Values& lower,const Values& upper,unsigned random_seed)
        :own_seed(seed),working(seed),lo(lower),hi(upper),rng(random_seed){
        try{
            count=source._num_vars;
            if(seed.size()!=count||lo.size()!=count||hi.size()!=count||source._vars.size()<count)return;
            if(own_value.empty())return;
            size_t start=own_value[0]=='-'?1:0;
            if(start==own_value.size()||own_value.find_first_not_of("0123456789",start)!=std::string::npos)return;
            own_best=Int(own_value,10);
            if(source.omt_direction!="min"&&source.omt_direction!="max")return;
            minimize=source.omt_direction=="min";
            integer_variable.resize(count);
            for(size_t v=0;v<count;++v){
                integer_variable[v]=source._vars[v].is_nia;
                if(lo[v]>hi[v]||seed[v]<lo[v]||seed[v]>hi[v])return;
                if(!integer_variable[v]&&seed[v]!=1&&seed[v]!=-1)return;
            }
            bool legacy_shape=true;
            auto compile=[&](const std::vector<coff_term>& list,__int128_t constant){
                Polynomial p;p.constant=exact(constant);
                std::map<std::vector<std::pair<size_t,unsigned>>,Int> combined;
                for(const auto& ct:list){
                    if(ct.coff==0)continue;
                    if(ct.term_idx<0||size_t(ct.term_idx)>=source._terms.size())throw std::runtime_error("invalid bilinear term");
                    const auto& factors=source._terms[ct.term_idx].var_epxs;
                    bool higher=false,zero=false;unsigned raw_degree=0;
                    std::map<size_t,unsigned> powers;
                    for(auto f:factors){
                        if(f.var_index<0||size_t(f.var_index)>=count||!integer_variable[f.var_index]||f.exponent<=0)throw std::runtime_error("invalid bilinear factor");
                        if(unsigned(f.exponent)>2||raw_degree>2-unsigned(f.exponent))higher=true;
                        else raw_degree+=unsigned(f.exponent);
                        if(!higher)powers[size_t(f.var_index)]+=unsigned(f.exponent);
                        if(lo[f.var_index]==hi[f.var_index]&&lo[f.var_index]==0)zero=true;
                    }
                    Int coefficient=exact(ct.coff);
                    // Degree-two terms keep their original representation.
                    // Only previously unsupported terms need substitution.
                    if(higher){
                        legacy_shape=false;
                        // A genuinely fixed zero removes the entire term,
                        // including otherwise oversized positive powers.
                        if(zero)continue;
                        powers.clear();
                        unsigned degree=0;
                        for(auto f:factors){
                            const size_t v=size_t(f.var_index);const unsigned exponent=unsigned(f.exponent);
                            if(lo[v]==hi[v]){
                                const Int base=exact(lo[v]);
                                const size_t bits=mpz_sizeinbase(base.get_mpz_t(),2);
                                if(abs(base)>1&&(bits>65536||exponent>65536/bits))throw std::runtime_error("fixed power too large");
                                Int q;mpz_pow_ui(q.get_mpz_t(),base.get_mpz_t(),exponent);coefficient*=q;
                                if(mpz_sizeinbase(coefficient.get_mpz_t(),2)>65536)throw std::runtime_error("fixed coefficient too large");
                            }else{
                                if(exponent>64||degree>64-exponent)throw std::runtime_error("residual degree too large");
                                degree+=exponent;powers[v]+=exponent;
                            }
                        }
                    }
                    std::vector<std::pair<size_t,unsigned>> key(powers.begin(),powers.end());
                    combined[key]+=coefficient;
                }
                for(auto& term:combined){
                    if(term.second==0)continue;
                    if(term.first.empty())p.constant+=term.second;
                    else p.terms.push_back({term.second,term.first});
                }
                return p;
            };
            if(source._num_lits>=source._lits.size())return;
            const size_t length=source._num_lits+1;
            literals.resize(length);arithmetic.assign(length,false);equality.assign(length,false);boolean_variable.resize(length);
            for(size_t i=1;i<length;++i){
                const auto& lit=source._lits[i];arithmetic[i]=lit.is_nia_lit;equality[i]=lit.is_equal;
                if(lit.is_nia_lit)literals[i]=compile(lit.coff_terms,lit.key);
                else{
                    if(lit.delta<0||__uint128_t(lit.delta)>=count||integer_variable[size_t(lit.delta)])return;
                    boolean_variable[i]=size_t(lit.delta);
                }
            }
            for(const auto& clause:source._clauses){
                for(int signed_id:clause.literals){
                    const int64_t wide=signed_id;const uint64_t id=wide<0?uint64_t(-wide):uint64_t(wide);
                    if(!id||id>=length)return;
                }
                clauses.push_back(clause.literals);
            }
            objective=compile(source.omt_objective_terms,source.omt_objective_constant);
            for(const auto& term:objective.terms)for(auto f:term.factors)objective_variables.insert(f.first);
            graph.resize(count);
            auto connect=[&](const Polynomial& p){
                for(const auto& term:p.terms){
                    bool zero=false;std::vector<std::pair<size_t,unsigned>> free;
                    for(auto f:term.factors){
                        if(lo[f.first]==hi[f.first]){if(lo[f.first]==0)zero=true;}
                        else free.push_back(f);
                    }
                    if(zero)continue;
                    unsigned degree=0;for(auto f:free)degree+=f.second;
                    if(degree<=1)continue;
                    if(degree!=2||free.size()!=2||free[0].second!=1||free[1].second!=1){legacy_shape=false;continue;}
                    graph[free[0].first].push_back(free[1].first);graph[free[1].first].push_back(free[0].first);
                }
            };
            for(size_t i=1;i<length;++i)if(arithmetic[i])connect(literals[i]);
            connect(objective);
            for(auto& neighbors:graph){std::sort(neighbors.begin(),neighbors.end());neighbors.erase(std::unique(neighbors.begin(),neighbors.end()),neighbors.end());}
            std::vector<int> color(count,-1);
            for(size_t root=0;legacy_shape&&root<count;++root){
                if(graph[root].empty()||color[root]>=0)continue;
                std::vector<size_t> component[2];std::deque<size_t> pending{root};color[root]=0;
                while(!pending.empty()){
                    const size_t v=pending.front();pending.pop_front();component[color[v]].push_back(v);
                    for(size_t next:graph[v]){
                        if(color[next]<0){color[next]=1-color[v];pending.push_back(next);}
                        else if(color[next]==color[v]){legacy_shape=false;break;}
                    }
                    if(!legacy_shape)break;
                }
                if(!legacy_shape)break;
                auto cost=[&](const std::vector<size_t>& side){
                    size_t objective_anchors=0,wide=0;
                    for(size_t v:side){objective_anchors+=objective_variables.count(v);wide+=!small(v);}
                    return std::make_tuple(objective_anchors,wide,side.size());
                };
                if(cost(component[1])<cost(component[0]))component[0].swap(component[1]);
                components.push_back({{std::move(component[0]),std::move(component[1])}});
            }
            if(legacy_shape)covers=bilinear_detail::component_cover_portfolio(components,rng);
            else{
                std::vector<bilinear_detail::MonomialFactors> monomials;
                for(size_t i=1;i<length;++i)if(arithmetic[i])for(const auto& term:literals[i].terms)monomials.push_back(term.factors);
                for(const auto& term:objective.terms)monomials.push_back(term.factors);
                covers=bilinear_detail::polynomial_cover_portfolio(monomials,lo,hi,
                    std::vector<size_t>(objective_variables.begin(),objective_variables.end()),rng);
            }
            if(covers.empty())return;
            for(auto& cover:covers)std::shuffle(cover.begin(),cover.end(),rng);
            cover_attempts.assign(covers.size(),0);
            anchor_rng=rng;anchor_rng.discard(32);
            if(evaluate(objective,seed)!=own_best)return;
            const Score original=score(seed);
            if(original.unsat!=0)return;
            valid=true;
            // Try a strict coordinate probe only in private working storage.
            // This is not a published model and need not satisfy the hard CNF.
            for(size_t v:objective_variables){
                Int a=0,b=objective.constant;bool affine=true;
                for(const auto& term:objective.terms){
                    unsigned exponent=0;Int value=term.coefficient;
                    for(auto f:term.factors){if(f.first==v)exponent=f.second;else{Int q;mpz_pow_ui(q.get_mpz_t(),exact(working[f.first]).get_mpz_t(),f.second);value*=q;}}
                    if(exponent>1){affine=false;break;}if(exponent)a+=value;else b+=value;
                }
                if(!affine||a==0)continue;
                const Int target=own_best+(minimize?-1:1);Int numerator=target-b,q;
                if((a>0)==minimize)mpz_fdiv_q(q.get_mpz_t(),numerator.get_mpz_t(),a.get_mpz_t());
                else mpz_cdiv_q(q.get_mpz_t(),numerator.get_mpz_t(),a.get_mpz_t());
                q=std::max(exact(lo[v]),std::min(exact(hi[v]),q));__int128_t value;
                if(!parse_omt_integer(q.get_str(),value))continue;
                Values candidate=working;candidate[v]=value;
                if(improves(evaluate(objective,candidate))){working=std::move(candidate);break;}
            }
            working_score=score(working);
            history.reset(working);
        }catch(const std::exception&){valid=false;}
    }
    bool small(size_t v)const{return integer_variable[v]&&exact(hi[v])-exact(lo[v])<=8;}
    bool improves(const Int& value)const{return minimize?value<own_best:value>own_best;}
    Score score(const Values& values)const{
        Score result;result.unsat=0;result.distance=0;
        if(values.size()!=count)return Score{};
        for(size_t v=0;v<count;++v)if(values[v]<lo[v]||values[v]>hi[v]||(!integer_variable[v]&&values[v]!=1&&values[v]!=-1))return Score{};
        result.objective_bad=!improves(evaluate(objective,values));
        std::vector<bool> truth(literals.size());std::vector<Int> residual(literals.size());
        for(size_t l=1;l<literals.size();++l){
            if(arithmetic[l]){residual[l]=evaluate(literals[l],values);truth[l]=equality[l]?residual[l]==0:residual[l]<=0;}
            else truth[l]=values[boolean_variable[l]]>0;
        }
        for(const auto& clause:clauses){
            bool sat=false;double distance=1e100;
            for(int signed_id:clause){
                const size_t id=size_t(signed_id<0?-int64_t(signed_id):int64_t(signed_id));
                if(truth[id]==(signed_id>0)){sat=true;break;}
                Int d=1;
                if(arithmetic[id]){
                    if(signed_id>0)d=equality[id]?abs(residual[id]):std::max(Int(0),residual[id]);
                    else d=equality[id]?Int(residual[id]==0?1:0):std::max(Int(0),Int(Int(1)-residual[id]));
                }
                distance=std::min(distance,std::log1p(std::min(1e100,d.get_d())));
            }
            if(!sat){++result.unsat;result.distance+=distance;}
        }
        return result;
    }
    Values trial_seed(const std::vector<size_t>& cover,uint64_t variant){
        if(!variant)return working;
        std::vector<size_t> finite,wide;
        for(size_t v:cover){if(small(v))finite.push_back(v);else wide.push_back(v);}
        // The color cover is fixed completely; only a tiny block is perturbed.
        // No artificial domain restriction is placed on the opposite color.
        return bilinear_detail::anchored_trial(working,own_seed,lo,hi,finite,wide,variant);
    }
    bool attempt(Clock::time_point deadline,Values& result){
        if(Clock::now()>=deadline)return false;
        const size_t cover_index=rounds%covers.size();const auto& cover=covers[cover_index];
        const uint64_t variant=cover_attempts[cover_index]++;++rounds;
        Values frozen_values=trial_seed(cover,variant);
        std::vector<bool> fixed(count,false);
        for(size_t v=0;v<count;++v)if(lo[v]==hi[v]){fixed[v]=true;frozen_values[v]=lo[v];}
        for(size_t v:cover)fixed[v]=true;
        try{
            z3::context context;std::vector<z3::expr> variables;variables.reserve(count);
            for(size_t v=0;v<count;++v){const auto name="omt_bilinear_"+std::to_string(v);variables.push_back(integer_variable[v]?context.int_const(name.c_str()):context.bool_const(name.c_str()));}
            const bool supplemental=variant&&((variant-1)%11>=8)
                &&std::any_of(cover.begin(),cover.end(),[&](size_t v){return !small(v);});
            LiaSolver solver(context,supplemental?anchor_rng():rng());
            auto numeral=[&](const Int& n){return context.int_val(n.get_str().c_str());};
            auto linear=[&](const Polynomial& p){
                Int constant=p.constant;std::map<size_t,Int> coefficients;
                for(const auto& term:p.terms){
                    Int coefficient=term.coefficient;size_t free_variable=count;unsigned free_degree=0;
                    for(auto f:term.factors){
                        if(fixed[f.first]){Int q;mpz_pow_ui(q.get_mpz_t(),exact(frozen_values[f.first]).get_mpz_t(),f.second);coefficient*=q;}
                        else{free_variable=f.first;free_degree+=f.second;}
                    }
                    if(coefficient==0)continue;
                    if(free_degree>1)throw std::runtime_error("cover left a nonlinear term");
                    if(!free_degree)constant+=coefficient;else coefficients[free_variable]+=coefficient;
                }
                z3::expr expression=numeral(constant);
                for(const auto& item:coefficients)if(item.second!=0)expression=expression+numeral(item.second)*variables[item.first];
                return expression;
            };
            std::vector<z3::expr> atoms;atoms.reserve(literals.size());atoms.push_back(context.bool_val(false));
            for(size_t id=1;id<literals.size();++id){
                if(Clock::now()>=deadline)return false;
                if(arithmetic[id]){auto p=linear(literals[id]);atoms.push_back(equality[id]?p==0:p<=0);}
                else{size_t v=boolean_variable[id];atoms.push_back(fixed[v]?context.bool_val(frozen_values[v]>0):variables[v]);}
            }
            for(const auto& clause:clauses){
                if(Clock::now()>=deadline)return false;
                z3::expr_vector disjunction(context);
                for(int signed_id:clause){size_t id=size_t(signed_id<0?-int64_t(signed_id):int64_t(signed_id));disjunction.push_back(signed_id>0?atoms[id]:!atoms[id]);}
                solver.add(z3::mk_or(disjunction));
            }
            auto own_objective=linear(objective);
            solver.add(minimize?own_objective<=numeral(Int(own_best-1)):own_objective>=numeral(Int(own_best+1)));
            for(size_t v=0;v<count;++v)if(!fixed[v]){
                if(integer_variable[v]){solver.add(variables[v]>=numeral(exact(lo[v])));solver.add(variables[v]<=numeral(exact(hi[v])));solver.prefer(variables[v],numeral(exact(working[v])));}
                else solver.prefer(variables[v],context.bool_val(working[v]>0));
            }
            const double remaining=std::chrono::duration<double>(deadline-Clock::now()).count();
            if(remaining<=.002)return false;
            solver.set("timeout",std::max(1u,unsigned(1000*std::min(.025,remaining*.65))));
            const auto status=solver.check();
            if(Clock::now()>=deadline||(!solver.candidate_available()&&status!=z3::sat))return false;
            const auto model=solver.get_model();Values candidate=std::move(frozen_values);
            for(size_t v=0;v<count;++v)if(!fixed[v]){
                const auto value=model.eval(variables[v],true).simplify();
                if(integer_variable[v]){if(!value.is_numeral()||!parse_omt_integer(Z3_get_numeral_string(context,value),candidate[v]))return false;}
                else candidate[v]=value.is_true()?1:-1;
            }
            const Score candidate_score=score(candidate);
            if(Clock::now()>=deadline)return false;
            if(candidate_score.feasible()){result.swap(candidate);return true;}
            // This is an internal, possibly infeasible search state. Full GMP
            // validation and the own strict bound gate all public candidates.
            // Exact same-score novel states may cross integer-distance
            // plateaus. The bounded epoch history rejects short cycles.
            const bool same_level=candidate_score.objective_bad==working_score.objective_bad&&candidate_score.unsat==working_score.unsat&&candidate_score.distance==working_score.distance;
            if(history.retain(candidate,candidate_score<working_score,same_level)){working=std::move(candidate);working_score=candidate_score;}
        }catch(const std::exception&){}
        return false;
    }
};

OmtBilinearRepair::OmtBilinearRepair(const ls_solver& solver,const Assignment& seed,
    const std::string& own_best,const Assignment& lo,const Assignment& hi,unsigned random_seed)
    :impl_(new Impl(solver,seed,own_best,lo,hi,random_seed)){}
OmtBilinearRepair::~OmtBilinearRepair()=default;
bool OmtBilinearRepair::supported()const{return impl_->valid;}
uint64_t OmtBilinearRepair::rounds_completed()const{return impl_->rounds;}
uint64_t OmtBilinearRepair::neutral_moves_retained()const{return impl_->history.neutral_acceptances();}
bool OmtBilinearRepair::resume_steps(uint64_t limit,double slice,double global,Assignment& result){
    if(!impl_->valid||!limit||!(slice>0)||!(global>0))return false;
    const double seconds=std::min(slice,global);
    const auto now=Clock::now();
    const auto room=Clock::time_point::max()-now;
    const double maximum=std::chrono::duration<double>(room).count();
    const auto deadline=seconds<maximum?now+std::chrono::duration_cast<Clock::duration>(std::chrono::duration<double>(seconds)):Clock::time_point::max();
    for(uint64_t step=0;step<limit&&Clock::now()<deadline;++step)if(impl_->attempt(deadline,result))return true;
    return false;
}
bool OmtBilinearRepair::resume_for(double slice,double global,Assignment& result){return resume_steps(UINT64_MAX,slice,global,result);}
}
