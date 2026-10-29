#pragma once
#include <z3++.h>
#include <gmpxx.h>
#include <map>
#include <algorithm>
#include <set>
#include <vector>
#include <functional>

namespace nia {
// Exact, equivalence-preserving preprocessing. No SMT solving is used here.
struct LiaPreprocessor {
    using Int=mpz_class;
    struct Linear { std::map<unsigned,Int> coefficients; Int constant=0; };
    z3::context& c;
    std::function<bool()> expired;
    std::vector<z3::expr> clauses;
    std::vector<std::pair<z3::expr,z3::expr>> substitutions;
    bool contradiction=false;
    static bool variable(const z3::expr& e) {
        return e.is_const() && e.decl().decl_kind()==Z3_OP_UNINTERPRETED;
    }
    static void accumulate(Linear& a,const Linear& b,const Int& scale) {
        a.constant+=b.constant*scale;
        for(const auto& p:b.coefficients){
            a.coefficients[p.first]+=p.second*scale;
            if(a.coefficients[p.first]==0)a.coefficients.erase(p.first);
        }
    }
    static bool linear(const z3::expr& e,Linear& out) {
        if(!e.is_int())return false;
        if(variable(e)){out.coefficients[e.id()]=1;return true;}
        if(e.is_numeral()){out.constant=Int(Z3_get_numeral_string(e.ctx(),e));return true;}
        auto k=e.decl().decl_kind();
        if(k==Z3_OP_ADD || k==Z3_OP_SUB || k==Z3_OP_UMINUS){
            for(unsigned i=0;i<e.num_args();++i){
                Linear a;if(!linear(e.arg(i),a))return false;
                accumulate(out,a,(k==Z3_OP_UMINUS || (k==Z3_OP_SUB && i>0))?Int(-1):Int(1));
            }return true;
        }
        if(k==Z3_OP_MUL){
            Linear product;product.constant=1;
            for(unsigned i=0;i<e.num_args();++i){
                Linear a;if(!linear(e.arg(i),a))return false;
                if(!product.coefficients.empty() && !a.coefficients.empty())return false;
                if(a.coefficients.empty()){
                    Linear next;accumulate(next,product,a.constant);product=next;
                }else {Linear next;accumulate(next,a,product.constant);product=next;}
            }out=product;return true;
        }
        // Branches are simplified after Boolean/constant substitution.
        return false;
    }
    static bool difference(const z3::expr& e,Linear& out) {
        if(e.num_args()!=2)return false;
        Linear a,b;if(!linear(e.arg(0),a) || !linear(e.arg(1),b))return false;
        out=a;accumulate(out,b,Int(-1));return true;
    }
    z3::expr numeral(const Int& n)const{return c.int_val(n.get_str().c_str());}
    void flatten(const z3::expr& e,std::vector<z3::expr>& out,std::set<unsigned>& seen) {
        if(e.is_and()){
            for(unsigned i=0;i<e.num_args();++i)flatten(e.arg(i),out,seen);
        }else if(e.is_false())contradiction=true;
        else if(!e.is_true() && seen.insert(e.id()).second)out.push_back(e);
    }
    void simplify() {
        std::vector<z3::expr> next;std::set<unsigned> seen;
        for(auto e:clauses){if(expired())return;flatten(e.simplify(),next,seen);}
        clauses.swap(next);
    }
    // Bounded RREF exposes fixed values and short equalities from coupled rows.
    // Use arbitrary precision rationals rather than utils' int64 ration_num.
    void gaussian_block(const std::vector<Linear>& equations,const std::map<unsigned,z3::expr>& vars) {
        std::set<unsigned> ids;
        for(const auto& row:equations)for(const auto& p:row.coefficients)ids.insert(p.first);
        if(equations.size()>48 || ids.size()>96)return;
        if(equations.size()<2 || ids.empty())return;
        std::vector<unsigned> columns(ids.begin(),ids.end());
        std::vector<std::vector<mpq_class>> rows(equations.size(),std::vector<mpq_class>(columns.size()+1));
        for(size_t r=0;r<rows.size();++r){
            for(size_t j=0;j<columns.size();++j){
                auto it=equations[r].coefficients.find(columns[j]);
                if(it!=equations[r].coefficients.end())rows[r][j]=it->second;
            }
            rows[r].back()=equations[r].constant;
        }
        size_t pivot=0;
        for(size_t j=0;j<columns.size() && pivot<rows.size();++j){
            if(expired())return;
            size_t r=pivot;while(r<rows.size() && rows[r][j]==0)++r;
            if(r==rows.size())continue;
            rows[r].swap(rows[pivot]);mpq_class divisor=rows[pivot][j];
            for(size_t k=j;k<=columns.size();++k)rows[pivot][k]/=divisor;
            for(r=0;r<rows.size();++r)if(r!=pivot && rows[r][j]!=0){
                if(expired())return;
                mpq_class factor=rows[r][j];
                for(size_t k=j;k<=columns.size();++k)rows[r][k]-=factor*rows[pivot][k];
            }
            ++pivot;
        }
        for(const auto& row:rows){
            if(expired())return;
            Int denominator=1;
            for(const auto& q:row)mpz_lcm(denominator.get_mpz_t(),denominator.get_mpz_t(),q.get_den().get_mpz_t());
            z3::expr sum=numeral(Int(row.back().get_num()*(denominator/row.back().get_den())));
            for(size_t j=0;j<columns.size();++j)if(row[j]!=0){
                Int coeff=row[j].get_num()*(denominator/row[j].get_den());
                sum=sum+numeral(coeff)*vars.at(columns[j]);
            }
            clauses.push_back((sum==0).simplify());
        }
    }
    void gaussian(const std::map<unsigned,z3::expr>& vars){
        std::vector<Linear> equations;std::map<unsigned,unsigned> parent;
        auto root=[&](unsigned id){
            unsigned r=id;while(parent.at(r)!=r)r=parent.at(r);
            while(parent.at(id)!=id){unsigned next=parent.at(id);parent[id]=r;id=next;}
            return r;
        };
        for(const auto& e:clauses){
            if(expired())return;
            if(!e.is_eq())continue;
            Linear a;if(!difference(e,a) || a.coefficients.empty())continue;
            unsigned first=a.coefficients.begin()->first;
            for(const auto& p:a.coefficients){
                parent.emplace(p.first,p.first);
                unsigned x=root(first),y=root(p.first);if(x!=y)parent[y]=x;
            }
            equations.push_back(a);if(equations.size()>=2048)break;
        }
        std::map<unsigned,std::vector<Linear>> components;
        for(const auto& row:equations)components[root(row.coefficients.begin()->first)].push_back(row);
        for(const auto& component:components){
            if(expired())return;
            gaussian_block(component.second,vars);
        }
    }
    struct Inequality { Linear lhs; Int rhs; }; // lhs <= rhs, zero constant
    static Int floor_div(const Int& n,const Int& d){
        Int q;mpz_fdiv_q(q.get_mpz_t(),n.get_mpz_t(),d.get_mpz_t());return q;
    }
    static Int ceil_div(const Int& n,const Int& d){
        Int q;mpz_cdiv_q(q.get_mpz_t(),n.get_mpz_t(),d.get_mpz_t());return q;
    }
    static bool inequality(const z3::expr& e,Inequality& row){
        bool negated=e.is_not();auto atom=negated?e.arg(0):e;
        auto k=atom.decl().decl_kind();
        if(negated){
            if(k==Z3_OP_LE)k=Z3_OP_GT;else if(k==Z3_OP_GE)k=Z3_OP_LT;
            else if(k==Z3_OP_LT)k=Z3_OP_GE;else if(k==Z3_OP_GT)k=Z3_OP_LE;
            else return false;
        }
        if(k!=Z3_OP_LE && k!=Z3_OP_LT && k!=Z3_OP_GE && k!=Z3_OP_GT)return false;
        if(!difference(atom,row.lhs))return false;
        if(k==Z3_OP_GE || k==Z3_OP_GT){
            Linear flipped;accumulate(flipped,row.lhs,Int(-1));row.lhs=flipped;
        }
        row.rhs=-row.lhs.constant-((k==Z3_OP_LT || k==Z3_OP_GT)?1:0);
        row.lhs.constant=0;
        Int gcd=0;for(const auto& p:row.lhs.coefficients)
            mpz_gcd(gcd.get_mpz_t(),gcd.get_mpz_t(),p.second.get_mpz_t());
        if(gcd!=0){
            row.rhs=floor_div(row.rhs,gcd);
            for(auto& p:row.lhs.coefficients)p.second/=gcd;
        }
        return true;
    }
    static std::string key(const Linear& a,bool negate=false){
        std::string result;
        for(const auto& p:a.coefficients){
            Int value=negate?Int(-p.second):p.second;
            result+=std::to_string(p.first)+":"+value.get_str()+";";
        }return result;
    }
    z3::expr expression(const Linear& a,const std::map<unsigned,z3::expr>& vars){
        auto result=numeral(a.constant);
        for(const auto& p:a.coefficients)result=result+numeral(p.second)*vars.at(p.first);
        return result;
    }
    bool reduce_disjunctions(const std::map<unsigned,Int>& low,const std::map<unsigned,Int>& high){
        std::map<unsigned,z3::expr> memo;
        std::function<z3::expr(const z3::expr&)> reduce=[&](const z3::expr& e)->z3::expr{
            if(expired())return e;
            auto cached=memo.find(e.id());if(cached!=memo.end())return cached->second;
            auto result=e;
            auto k=e.decl().decl_kind();
            Linear a;
            if((k==Z3_OP_EQ || k==Z3_OP_LE || k==Z3_OP_GE || k==Z3_OP_LT || k==Z3_OP_GT) && difference(e,a)){
                Int minimum=a.constant,maximum=a.constant,gcd=0;bool have_min=true,have_max=true;
                for(const auto& p:a.coefficients){
                    mpz_gcd(gcd.get_mpz_t(),gcd.get_mpz_t(),p.second.get_mpz_t());
                    const auto& min_domain=p.second>0?low:high;
                    const auto& max_domain=p.second>0?high:low;
                    auto l=min_domain.find(p.first),u=max_domain.find(p.first);
                    if(l==min_domain.end())have_min=false;else minimum+=p.second*l->second;
                    if(u==max_domain.end())have_max=false;else maximum+=p.second*u->second;
                }
                bool yes=false,no=false;
                switch(k){
                case Z3_OP_EQ:
                    yes=have_min && have_max && minimum==0 && maximum==0;
                    no=(have_min && minimum>0) || (have_max && maximum<0) || (gcd!=0 && a.constant%gcd!=0);break;
                case Z3_OP_LE:yes=have_max && maximum<=0;no=have_min && minimum>0;break;
                case Z3_OP_LT:yes=have_max && maximum<0;no=have_min && minimum>=0;break;
                case Z3_OP_GE:yes=have_min && minimum>=0;no=have_max && maximum<0;break;
                case Z3_OP_GT:yes=have_min && minimum>0;no=have_max && maximum<=0;break;
                default:break;
                }
                if(yes || no)result=c.bool_val(yes);
            }else if(e.num_args()){
                z3::expr_vector children(c);bool changed=false;
                for(unsigned i=0;i<e.num_args();++i){auto child=reduce(e.arg(i));children.push_back(child);changed|=child.id()!=e.arg(i).id();}
                if(changed)result=e.decl()(children).simplify();
            }
            memo.emplace(e.id(),result);return result;
        };
        std::vector<z3::expr> next;std::set<unsigned> seen;bool changed=false;
        for(const auto& e:clauses){
            if(expired())return false;
            auto result=e.is_or()?reduce(e).simplify():e;
            changed|=result.id()!=e.id();flatten(result,next,seen);
        }
        clauses.swap(next);return changed;
    }
    // Derive integer bounds and equalities from asserted linear constraints.
    // Keeping the source constraints makes partial/budget-limited inference safe.
    bool infer(const std::map<unsigned,z3::expr>& vars){
        std::vector<Inequality> rows;
        std::map<std::string,size_t> shapes;
        std::set<unsigned> seen;for(const auto& e:clauses)seen.insert(e.id());
        std::vector<z3::expr> added;
        auto append=[&](z3::expr e){
            e=e.simplify();if(e.is_false())contradiction=true;
            else if(!e.is_true() && seen.insert(e.id()).second)added.push_back(e);
        };
        for(const auto& e:clauses){
            if(expired())return false;
            Inequality row;
            if(inequality(e,row)){
                auto shape=key(row.lhs);auto it=shapes.find(shape);
                if(it==shapes.end()){shapes[shape]=rows.size();rows.push_back(row);}
                else rows[it->second].rhs=std::min(rows[it->second].rhs,row.rhs);
            }else if(e.is_eq()){
                Linear a;if(!difference(e,a))continue;
                Inequality positive{a,Int(-a.constant)};positive.lhs.constant=0;
                Linear negative;accumulate(negative,positive.lhs,Int(-1));
                rows.push_back(positive);rows.push_back({negative,Int(a.constant)});
            }
            if(rows.size()>4096)return false;
        }
        for(const auto& row:rows){
            if(expired())return false;
            auto opposite=shapes.find(key(row.lhs,true));
            if(opposite==shapes.end())continue;
            const auto& other=rows[opposite->second];
            if(row.rhs+other.rhs<0){contradiction=true;return false;}
            if(row.rhs+other.rhs==0)append(expression(row.lhs,vars)==numeral(row.rhs));
        }
        std::map<unsigned,Int> low,high;
        for(unsigned pass=0;pass<8;++pass){
            bool changed=false;
            for(const auto& row:rows){
                if(expired())goto finish;
                // Sum the minimum contributions. At most one missing bound
                // still permits inference for that variable, without O(n^2).
                Int minimum=0;unsigned missing=0,missing_id=0;
                for(const auto& p:row.lhs.coefficients){
                    const auto& domain=p.second>0?low:high;auto it=domain.find(p.first);
                    if(it==domain.end()){++missing;missing_id=p.first;}
                    else minimum+=p.second*it->second;
                }
                if(missing==0 && minimum>row.rhs){contradiction=true;goto finish;}
                if(missing>1)continue;
                for(const auto& p:row.lhs.coefficients){
                    if(missing==1 && p.first!=missing_id)continue;
                    Int rest=minimum;
                    if(missing==0)rest-=p.second*(p.second>0?low.at(p.first):high.at(p.first));
                    bool upper=p.second>0;
                    Int value=upper?floor_div(Int(row.rhs-rest),p.second):
                        ceil_div(Int(rest-row.rhs),Int(-p.second));
                    auto& domain=upper?high:low;auto it=domain.find(p.first);
                    if(it==domain.end() || (upper?value<it->second:value>it->second)){
                        domain[p.first]=value;changed=true;
                        if(low.count(p.first) && high.count(p.first) && low.at(p.first)>high.at(p.first)){
                            contradiction=true;goto finish;
                        }
                    }
                }
            }
            if(!changed)break;
        }
finish:
        for(const auto& p:low){
            if(expired())break;
            if(high.count(p.first) && p.second==high.at(p.first))append(vars.at(p.first)==numeral(p.second));
            else append(vars.at(p.first)>=numeral(p.second));
        }
        for(const auto& p:high){
            if(expired())break;
            if(!low.count(p.first) || p.second!=low.at(p.first))append(vars.at(p.first)<=numeral(p.second));
        }
        bool reduced=false;
        if(!contradiction && !expired())reduced=reduce_disjunctions(low,high);
        clauses.insert(clauses.end(),added.begin(),added.end());
        return reduced || !added.empty();
    }
    // Arithmetic atoms can be units too: A and (!A or B) entails B.
    // Preserve every source unit; only substitute within other clauses.
    bool propagate_units(){
        z3::expr_vector from(c),to(c);std::map<unsigned,bool> facts;
        for(const auto& e:clauses){
            if(expired())return false;
            if(e.is_or())continue;
            bool positive=!e.is_not();auto atom=positive?e:e.arg(0);
            auto inserted=facts.emplace(atom.id(),positive);
            if(!inserted.second && inserted.first->second!=positive){contradiction=true;return false;}
            if(inserted.second){from.push_back(atom);to.push_back(c.bool_val(positive));}
            if(from.size()>=256)break;
        }
        if(from.empty())return false;
        std::vector<z3::expr> next;std::set<unsigned> seen;bool changed=false;
        for(auto e:clauses){
            if(expired())return false;
            auto reduced=e;
            if(e.is_or())reduced=e.substitute(from,to).simplify();
            if(reduced.id()!=e.id())changed=true;
            flatten(reduced,next,seen);
        }
        clauses.swap(next);return changed;
    }
    void run(const std::vector<z3::expr>& original,const std::vector<z3::expr>& variables) {
        clauses=original;substitutions.clear();contradiction=false;
        std::map<unsigned,z3::expr> vars;for(auto v:variables)vars.emplace(v.id(),v);
        simplify();bool gaussian_done=false,inference_done=false;
        // Limit AST expansion and total preprocessing cost; partial elimination
        // is still equivalent, and all remaining constraints go to SLS.
        for(unsigned pass=0;pass<64 && !contradiction && !expired();++pass){
            bool found=false;z3::expr from(c),to(c);
            for(const auto& e:clauses){
                if(expired())return;
                if(variable(e) && e.is_bool()){from=e;to=c.bool_val(true);found=true;}
                else if(e.is_not() && variable(e.arg(0)) && e.arg(0).is_bool()){
                    from=e.arg(0);to=c.bool_val(false);found=true;
                }else if(e.is_eq()){
                    Linear a;
                    if(difference(e,a)){
                        Int gcd=0;for(const auto& p:a.coefficients)mpz_gcd(gcd.get_mpz_t(),gcd.get_mpz_t(),p.second.get_mpz_t());
                        if(gcd!=0 && a.constant%gcd!=0){contradiction=true;return;}
                        if(a.coefficients.empty()){if(a.constant!=0)contradiction=true;continue;}
                        // Normalize first: 2*x+4*y=6 also has a unit pivot.
                        if(gcd!=0){a.constant/=gcd;for(auto& p:a.coefficients)p.second/=gcd;}
                        for(const auto& p:a.coefficients)if(p.second==1 || p.second==-1){
                            // Avoid expanding large dense equalities throughout the AST.
                            if(a.coefficients.size()>8)break;
                            from=vars.at(p.first);to=numeral(Int(-a.constant/p.second));
                            for(const auto& q:a.coefficients)if(q.first!=p.first)
                                to=to+numeral(Int(-q.second/p.second))*vars.at(q.first);
                            to=to.simplify();found=true;break;
                        }
                    }else if(e.arg(0).is_bool()){
                        auto a0=e.arg(0),a1=e.arg(1);
                        if(variable(a1))std::swap(a0,a1);
                        // Only constants here; arbitrary Boolean aliases can cycle.
                        if(variable(a0) && (a1.is_true() || a1.is_false())){from=a0;to=a1;found=true;}
                    }
                }
                if(found)break;
            }
            if(!found){
                if(!inference_done){
                    inference_done=true;
                    bool changed=propagate_units();
                    if(contradiction)break;
                    changed=infer(vars) || changed;
                    if(changed){inference_done=false;simplify();continue;}
                }
                // Boolean propagation can expose new unconditional equations.
                if(!gaussian_done){gaussian_done=true;gaussian(vars);simplify();continue;}
                break;
            }
            z3::expr_vector source(c),target(c);source.push_back(from);target.push_back(to);
            std::vector<z3::expr> next;std::set<unsigned> seen;
            for(auto e:clauses){
                if(expired())return;
                flatten(e.substitute(source,target).simplify(),next,seen);
            }
            // Commit an elimination only after every clause was transformed.
            clauses.swap(next);substitutions.emplace_back(from,to);inference_done=false;
        }
    }
};
}
