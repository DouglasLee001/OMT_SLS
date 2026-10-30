#include "nia_omt_core_neighborhood.h"
#include "nia_ls.h"
#include "nia_omt_input.h"
#include <z3++.h>
#include <gmpxx.h>
#include <algorithm>
#include <chrono>
#include <climits>
#include <cmath>
#include <limits>
#include <random>
#include <stdexcept>
#include <unordered_map>

namespace nia {
namespace {
using Clock = std::chrono::steady_clock;
struct Expired {};
struct Rejected : std::runtime_error {
    std::string status;
    Rejected(const std::string& state, const std::string& reason)
        : std::runtime_error(reason), status(state) {}
};
void require(bool okay, const char* reason) {
    if (!okay) throw Rejected("unsupported", reason);
}
struct Budget {
    Clock::time_point began = Clock::now();
    double limit;
    explicit Budget(double seconds) : limit(seconds) {}
    double elapsed() const { return std::chrono::duration<double>(Clock::now()-began).count(); }
    double remaining() const { return limit-elapsed(); }
    void check() const { if (!(remaining()>0)) throw Expired{}; }
};
mpz_class integer(__int128_t value) { return mpz_class(format_omt_integer(value),10); }
bool decimal(const std::string& text, const Budget& budget) {
    size_t i = !text.empty() && (text[0]=='-' || text[0]=='+') ? 1 : 0;
    if (i==text.size()) return false;
    for (;i<text.size();++i) {
        budget.check();
        if (text[i]<'0' || text[i]>'9') return false;
    }
    return true;
}
mpz_class exact_power(const mpz_class& base, unsigned exponent) {
    if (base==0) return exponent ? mpz_class(0) : mpz_class(1);
    if (base==1) return mpz_class(1);
    if (base==-1) return mpz_class(exponent%2 ? -1 : 1);
    // Prevent one uninterruptible GMP primitive from allocating an unbounded
    // integer. This rejects a neighborhood; it never restricts a Z3 domain.
    const size_t bits=mpz_sizeinbase(base.get_mpz_t(),2);
    require(exponent<=8UL*1024*1024/bits,"exact validation exceeds arithmetic resource allowance");
    mpz_class result;
    mpz_pow_ui(result.get_mpz_t(),base.get_mpz_t(),exponent);
    return result;
}
z3::expr power(z3::expr base, unsigned exponent, const Budget& budget) {
    z3::expr value=base.ctx().int_val(1);
    while (exponent) {
        budget.check();
        if (exponent&1) value=value*base;
        exponent>>=1;
        if (exponent) base=base*base;
    }
    return value;
}
}

struct OmtCoreNeighborhood::Impl {
    enum class Stage { header, initial_free, values, terms, literals, clauses, objective,
                       context, variables, term_ast, literal_ast, clause_ast,
                       bound_ast, ready, answer, model };
    const ls_solver& native;
    std::vector<__int128_t> seed, candidate;
    std::string best_text;
    std::vector<size_t> initially_free;
    unsigned random_seed;
    std::mt19937 random;
    double max_slice;
    bool done=false, validating_candidate=false, clause_satisfied=false;
    Stage stage=Stage::header;
    std::string status="paused", reason;
    mpz_class best, exact_accumulator;
    std::vector<mpz_class> values, term_values;
    std::vector<bool> literal_truth, released, seen_core, seen_objective;
    std::vector<size_t> released_indices, core_indices, objective_candidates;
    size_t cursor=0, factor_cursor=0, coefficient_cursor=0, literal_cursor=0;
    std::unique_ptr<z3::context> context;
    std::unique_ptr<z3::solver> solver;
    std::vector<z3::expr> variables, term_asts, literal_asts, anchors;
    std::unordered_map<unsigned,size_t> anchor_ids;
    std::unique_ptr<z3::expr> expression, objective;
    std::unique_ptr<z3::model> model;
    z3::check_result answer=z3::unknown;

    Impl(const ls_solver& s, const std::vector<__int128_t>& x, const std::string& value,
         unsigned rng, const std::vector<size_t>& free, double slice)
        : native(s), seed(x), best_text(value), initially_free(free), random_seed(rng),
          random(rng), max_slice(std::min(2.0,slice)) {}

    void release(size_t index) {
        if (!released[index]) { released[index]=true; released_indices.push_back(index); }
    }
    const term& get_term(int index) const {
        require(index>=0 && size_t(index)<native._terms.size(),"invalid native term index");
        return native._terms[size_t(index)];
    }
    void check_factor(const var_exp& factor) const {
        require(factor.var_index>=0 && size_t(factor.var_index)<native._num_vars &&
                native._vars[size_t(factor.var_index)].is_nia && factor.exponent>0,
                "invalid native integer polynomial factor");
    }
    size_t bool_index(const lit& literal) const {
        require(literal.delta>=0 && __uint128_t(literal.delta)<native._num_vars,
                "invalid native Boolean variable index");
        const size_t index=size_t(literal.delta);
        require(!native._vars[index].is_nia,"Boolean literal refers to an integer variable");
        return index;
    }
    size_t lit_index(int id) const {
        require(id!=0 && id!=INT_MIN,"invalid native signed literal");
        const size_t index=size_t(id<0 ? -id : id);
        require(index<=native._num_lits && index<native._lits.size(),"invalid native literal index");
        return index;
    }
    void reset_cursor(Stage next, size_t first=0) {
        stage=next; cursor=first; factor_cursor=coefficient_cursor=literal_cursor=0;
        clause_satisfied=false; expression.reset();
    }
    void reject(const char* why) { throw Rejected("rejected",why); }
    // This primitive is resumable between coefficients, including enormous
    // native polynomials. Monomial powers are cached separately per assignment.
    bool evaluate(__int128_t constant, const std::vector<coff_term>& coefficients,
                  const Budget& budget, bool objective_support=false) {
        if (coefficient_cursor==0) exact_accumulator=integer(constant);
        while (coefficient_cursor<coefficients.size()) {
            budget.check();
            const auto& coefficient=coefficients[coefficient_cursor];
            const auto& monomial=get_term(coefficient.term_idx);
            if (coefficient.coff!=0) {
                if (objective_support) {
                    for (;factor_cursor<monomial.var_epxs.size();++factor_cursor) {
                        budget.check();
                        const size_t index=size_t(monomial.var_epxs[factor_cursor].var_index);
                        if (!released[index] && !seen_objective[index]) {
                            seen_objective[index]=true; objective_candidates.push_back(index);
                        }
                    }
                }
                exact_accumulator+=integer(coefficient.coff)*term_values[size_t(coefficient.term_idx)];
            }
            factor_cursor=0; ++coefficient_cursor;
        }
        coefficient_cursor=0;
        return true;
    }
    void ast_polynomial(__int128_t constant, const std::vector<coff_term>& coefficients,
                        const Budget& budget) {
        if (!expression) expression=std::make_unique<z3::expr>(context->int_val(format_omt_integer(constant).c_str()));
        while (coefficient_cursor<coefficients.size()) {
            budget.check();
            const auto& coefficient=coefficients[coefficient_cursor++];
            if (coefficient.coff!=0)
                *expression=*expression+context->int_val(format_omt_integer(coefficient.coff).c_str())*
                            term_asts[size_t(coefficient.term_idx)];
        }
        coefficient_cursor=0;
    }
    void build_or_validate(const Budget& budget, Result& result) {
        while (stage!=Stage::ready && stage!=Stage::answer && stage!=Stage::model && !done) {
            budget.check();
            switch (stage) {
            case Stage::header:
                require(native.omt_loaded && native._num_vars<=native._vars.size() &&
                        native._num_clauses==native._clauses.size() && native._num_lits<native._lits.size(),
                        "missing or invalid complete native OMT model");
                require(native.omt_direction=="min" || native.omt_direction=="max","invalid native objective direction");
                require(std::isfinite(max_slice) && max_slice>0,"invalid neighborhood maximum slice");
                if (seed.size()!=native._num_vars) reject("seed must assign the complete native variable space");
                if (!decimal(best_text,budget)) reject("incumbent must be an exact decimal integer");
                best=mpz_class(best_text[0]=='+' ? best_text.substr(1) : best_text,10);
                released.assign(native._num_vars,false); seen_core.assign(native._num_vars,false);
                seen_objective.assign(native._num_vars,false);
                literal_truth.assign(native._num_lits+1,false);
                reset_cursor(Stage::initial_free); break;
            case Stage::initial_free:
                while (cursor<initially_free.size()) {
                    budget.check(); const size_t index=initially_free[cursor];
                    require(index<native._num_vars,"initially_free index is outside native variables");
                    release(index); ++cursor;
                }
                reset_cursor(Stage::values); break;
            case Stage::values: {
                const auto& assignment=validating_candidate ? candidate : seed;
                while (cursor<native._num_vars) {
                    budget.check();
                    if (!native._vars[cursor].is_nia && assignment[cursor]!=-1 && assignment[cursor]!=1)
                        reject("Boolean assignment must use -1 or 1");
                    values.push_back(integer(assignment[cursor++]));
                }
                reset_cursor(Stage::terms); break;
            }
            case Stage::terms:
                while (cursor<native._terms.size()) {
                    budget.check();
                    const auto& monomial=native._terms[cursor];
                    if (factor_cursor==0) exact_accumulator=1;
                    for (;factor_cursor<monomial.var_epxs.size();++factor_cursor) {
                        budget.check(); const auto& factor=monomial.var_epxs[factor_cursor]; check_factor(factor);
                        exact_accumulator*=exact_power(values[size_t(factor.var_index)],unsigned(factor.exponent));
                    }
                    term_values.push_back(exact_accumulator); ++cursor; factor_cursor=0;
                }
                reset_cursor(Stage::literals,1); break;
            case Stage::literals:
                while (cursor<=native._num_lits) {
                    budget.check(); const auto& literal=native._lits[cursor];
                    bool truth;
                    if (literal.is_nia_lit) {
                        evaluate(literal.key,literal.coff_terms,budget);
                        truth=literal.is_equal ? exact_accumulator==0 : exact_accumulator<=0;
                    } else truth=values[bool_index(literal)]==1;
                    literal_truth[cursor++]=truth;
                }
                reset_cursor(Stage::clauses); break;
            case Stage::clauses:
                while (cursor<native._clauses.size()) {
                    budget.check(); const auto& clause=native._clauses[cursor];
                    for (;literal_cursor<clause.literals.size();++literal_cursor) {
                        budget.check(); const int id=clause.literals[literal_cursor];
                        clause_satisfied|=literal_truth[lit_index(id)]==(id>0);
                    }
                    if (!clause_satisfied) reject(validating_candidate ? "candidate fails exact native hard CNF" : "seed fails exact native hard CNF");
                    ++cursor; literal_cursor=0; clause_satisfied=false;
                }
                reset_cursor(Stage::objective); break;
            case Stage::objective:
                evaluate(native.omt_objective_constant,native.omt_objective_terms,budget,!validating_candidate);
                if (validating_candidate) {
                    if (!(native.omt_direction=="min" ? exact_accumulator<best : exact_accumulator>best))
                        reject("candidate is not an exact strict native improvement");
                    budget.check(); result.candidate_value=exact_accumulator.get_str();
                    result.candidate=candidate; result.candidate_available=true;
                    status="sat"; done=true;
                } else {
                    if (exact_accumulator!=best) reject("incumbent objective differs from exact native seed objective");
                    // Start with one objective coordinate; cores widen this
                    // neighborhood even when a dense objective uses every var.
                    if (!objective_candidates.empty())
                        release(objective_candidates[std::uniform_int_distribution<size_t>(0,objective_candidates.size()-1)(random)]);
                    reset_cursor(Stage::context);
                }
                break;
            case Stage::context:
                context=std::make_unique<z3::context>();
                solver=std::make_unique<z3::solver>(*context);
                solver->set("unsat_core",true); solver->set("random_seed",random_seed);
                // Literal index zero is unused, but keeping it makes native
                // positive and negative CNF indices map without name lookups.
                literal_asts.push_back(context->bool_val(false));
                reset_cursor(Stage::variables); break;
            case Stage::variables:
                while (cursor<native._num_vars) {
                    budget.check();
                    auto sort=native._vars[cursor].is_nia ? context->int_sort() : context->bool_sort();
                    variables.emplace_back(*context,Z3_mk_fresh_const(*context,"omt-core-coordinate",sort));
                    z3::expr point=native._vars[cursor].is_nia ? context->int_val(format_omt_integer(seed[cursor]).c_str()) : context->bool_val(seed[cursor]==1);
                    anchors.push_back(variables.back()==point);
                    anchor_ids.emplace(anchors.back().id(),cursor++);
                }
                reset_cursor(Stage::term_ast); break;
            case Stage::term_ast:
                while (cursor<native._terms.size()) {
                    budget.check();
                    if (!expression) expression=std::make_unique<z3::expr>(context->int_val(1));
                    const auto& monomial=native._terms[cursor];
                    for (;factor_cursor<monomial.var_epxs.size();++factor_cursor) {
                        budget.check(); const auto& factor=monomial.var_epxs[factor_cursor];
                        *expression=*expression*power(variables[size_t(factor.var_index)],unsigned(factor.exponent),budget);
                    }
                    term_asts.push_back(*expression); expression.reset(); ++cursor; factor_cursor=0;
                }
                reset_cursor(Stage::literal_ast,1); break;
            case Stage::literal_ast:
                while (cursor<=native._num_lits) {
                    budget.check(); const auto& literal=native._lits[cursor];
                    if (literal.is_nia_lit) {
                        ast_polynomial(literal.key,literal.coff_terms,budget);
                        literal_asts.push_back(literal.is_equal ? *expression==0 : *expression<=0); expression.reset();
                    } else literal_asts.push_back(variables[bool_index(literal)]);
                    ++cursor;
                }
                reset_cursor(Stage::clause_ast); break;
            case Stage::clause_ast:
                while (cursor<native._clauses.size()) {
                    budget.check(); if (!expression) expression=std::make_unique<z3::expr>(context->bool_val(false));
                    const auto& clause=native._clauses[cursor];
                    for (;literal_cursor<clause.literals.size();++literal_cursor) {
                        budget.check(); const int id=clause.literals[literal_cursor];
                        const auto& literal=literal_asts[lit_index(id)];
                        *expression=*expression || (id>0 ? literal : !literal);
                    }
                    solver->add(*expression); expression.reset(); ++cursor; literal_cursor=0;
                }
                reset_cursor(Stage::bound_ast); break;
            case Stage::bound_ast: {
                ast_polynomial(native.omt_objective_constant,native.omt_objective_terms,budget);
                objective=std::move(expression);
                const auto bound=context->int_val(best.get_str().c_str());
                // Only the strict improvement is permanent. Every coordinate
                // equality is supplied solely through check(assumptions).
                solver->add(native.omt_direction=="min" ? *objective<bound : *objective>bound);
                reset_cursor(Stage::ready); break;
            }
            default: break;
            }
        }
    }
    std::vector<size_t> active() const {
        std::vector<size_t> indices;
        for (size_t i=0;i<released.size();++i) if (!released[i]) indices.push_back(i);
        return indices;
    }
    void process_answer(const Budget& budget, Result& result) {
        if (answer==z3::unknown) {
            // No conflict evidence justifies releasing another anchor. Stop
            // this incumbent's neighborhood even when check used its slice.
            done=true; status="unknown"; reason=solver->reason_unknown();
            return;
        }
        budget.check();
        if (answer==z3::sat) {
            model=std::make_unique<z3::model>(solver->get_model());
            candidate.clear(); reset_cursor(Stage::model); return;
        }
        if (answer==z3::unsat) {
            const auto core=solver->unsat_core(); ++result.cores;
            std::vector<size_t> relevant;
            for (const auto& equality:core) {
                const auto found=anchor_ids.find(equality.id());
                require(found!=anchor_ids.end() && !released[found->second],"UNSAT core contains a non-anchor expression");
                const auto index=found->second; relevant.push_back(index);
                if (!seen_core[index]) { seen_core[index]=true; core_indices.push_back(index); }
            }
            if (relevant.empty()) {
                done=true; status="exhausted";
                reason="empty-core UNSAT ends only the native neighborhood; no original-problem proof";
            } else {
                release(relevant[std::uniform_int_distribution<size_t>(0,relevant.size()-1)(random)]);
                status="relaxed"; reset_cursor(Stage::ready);
            }
        }
    }
    void extract_model(const Budget& budget) {
        while (cursor<native._num_vars) {
            budget.check(); const auto value=model->eval(variables[cursor],true); __int128_t point;
            if (native._vars[cursor].is_nia) {
                if (!value.is_int() || !value.is_numeral() ||
                    !parse_omt_integer(Z3_get_numeral_string(*context,value),point))
                    reject("candidate integer is not representable in the complete signed128 native assignment");
            } else {
                if (!value.is_true() && !value.is_false()) reject("candidate Boolean is not concrete");
                point=value.is_true() ? 1 : -1;
            }
            candidate.push_back(point); ++cursor;
        }
        // Validate independently of Z3 caches and model evaluation. Parent
        // acceptance must still replay its existing exact original/native check.
        validating_candidate=true; values.clear(); term_values.clear();
        reset_cursor(Stage::values);
    }
};

OmtCoreNeighborhood::OmtCoreNeighborhood(const ls_solver& native, const std::vector<__int128_t>& seed,
    const std::string& best, unsigned random_seed, const std::vector<size_t>& initially_free,
    double max_slice_seconds)
    : impl_(std::make_unique<Impl>(native,seed,best,random_seed,initially_free,max_slice_seconds)) {}
OmtCoreNeighborhood::~OmtCoreNeighborhood()=default;
bool OmtCoreNeighborhood::finished() const { return impl_->done; }
OmtCoreNeighborhood::Result OmtCoreNeighborhood::resume_for(double seconds) {
    Impl& state=*impl_;
    const bool valid=std::isfinite(seconds) && seconds>0;
    Budget budget(valid ? std::min(seconds,state.max_slice) : 0);
    Result result;
    if (state.done) { result.status=state.status; result.reason=state.reason; }
    else if (!valid || !(budget.remaining()>=.000001)) result.status="budget";
    else try {
        state.status="paused";
        state.build_or_validate(budget,result);
        if (!state.done && state.stage==Impl::Stage::ready) {
            budget.check(); z3::expr_vector assumptions(*state.context);
            for (size_t index:state.active()) { budget.check(); assumptions.push_back(state.anchors[index]); }
            const double milliseconds=std::floor(budget.remaining()*1000);
            if (milliseconds<1) throw Expired{};
            state.solver->set("timeout",unsigned(std::min(milliseconds,double(std::numeric_limits<unsigned>::max()))));
            ++result.queries; state.answer=state.solver->check(assumptions);
            state.stage=Impl::Stage::answer;
        }
        if (!state.done && state.stage==Impl::Stage::answer) state.process_answer(budget,result);
        if (!state.done && state.stage==Impl::Stage::model) state.extract_model(budget);
        if (!state.done && state.validating_candidate) state.build_or_validate(budget,result);
        if (!state.done || state.status!="unknown") budget.check();
        result.status=state.status; result.reason=state.reason;
    } catch (const Expired&) {
        result.status="budget"; result.reason="native neighborhood exhausted its finite slice";
        // The solver result/model remains pending and can be processed next
        // time. Never publish a solution after the caller's allowance expired.
        if (result.candidate_available) { state.done=true; state.status="budget"; }
    } catch (const Rejected& error) {
        state.done=true; state.status=error.status; state.reason=error.what();
        result.status=state.status; result.reason=state.reason;
    } catch (const z3::exception& error) {
        state.done=true; state.status="error"; state.reason=error.msg();
        result.status=state.status; result.reason=state.reason;
    } catch (const std::exception& error) {
        state.done=true; state.status="error"; state.reason=error.what();
        result.status=state.status; result.reason=state.reason;
    }
    result.released_variables=state.released_indices; result.core_variables=state.core_indices;
    result.anchors=state.released.size()-state.released_indices.size(); result.seconds=budget.elapsed();
    if (result.candidate_available && result.seconds>=budget.limit) {
        result.status="budget"; result.reason="candidate completed after its finite slice";
        state.status=result.status; state.reason=result.reason;
    }
    if (result.status!="sat" || result.seconds>=budget.limit) {
        result.candidate_available=false; result.candidate.clear(); result.candidate_value.clear();
    }
    return result;
}
}
