#include "nia_omt_optimality.h"
#include "nia_ls.h"
#include "nia_omt_input.h"
#include "nia_omt_model_bridge.h"
#include "nia_omt_cutset.h"
#include "nia_omt_convex_exchange.h"
#include "nia_omt_objective_slice.h"
#include "nia_omt_guarded_product.h"
#include <gmpxx.h>
#include <z3++.h>
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cctype>
#include <filesystem>
#include <fstream>
#include <limits>
#include <memory>
#include <map>
#include <optional>
#include <set>
#include <sstream>
#include <stdexcept>
#include <utility>
#include <vector>
#include <iomanip>
#include <iostream>
#include <cerrno>
#include <cstdint>
#include <csignal>
#include <sys/prctl.h>
#include <sys/wait.h>
#include <unistd.h>

namespace nia {
namespace {
using Clock = std::chrono::steady_clock;
enum class QueryMode { strict_improvement, feasibility };
struct Expired {};
struct Budget {
    Clock::time_point start = Clock::now();
    double allowance;
    explicit Budget(double seconds) : allowance(seconds) {}
    double elapsed() const { return std::chrono::duration<double>(Clock::now() - start).count(); }
    double remaining() const { return allowance - elapsed(); }
    void check() const { if (!(remaining() > 0)) throw Expired{}; }
};
bool valid_advisory_share(double value) {
    return value == 0 || (std::isfinite(value) && value > 0 && value < 1);
}
bool valid_bv_seconds(double value) {
    return value == 0 || (std::isfinite(value) && value >= .001 && value <= 1200);
}

std::string read_source(const std::string& path, const Budget& budget) {
    std::ifstream input(path, std::ios::binary);
    if (!input) throw std::runtime_error("cannot open original SMT source: " + path);
    std::string source;
    char buffer[65536];
    while (input) {
        budget.check();
        input.read(buffer, sizeof(buffer));
        source.append(buffer, size_t(input.gcount()));
    }
    if (!input.eof()) throw std::runtime_error("cannot read original SMT source");
    return source;
}

// Mirror the converter's SMT-LIB lexical rules, including comments, quoted
// symbols and doubled quotes in strings. No source command is executed here.
std::vector<std::string> tokens(const std::string& source, const Budget& budget) {
    std::vector<std::string> result;
    for (size_t at = 0; at < source.size();) {
        budget.check();
        const unsigned char ch = static_cast<unsigned char>(source[at]);
        if (std::isspace(ch)) { ++at; continue; }
        if (ch == ';') {
            do { ++at; if ((at & 4095) == 0) budget.check(); }
            while (at < source.size() && source[at] != '\n');
            continue;
        }
        const size_t begin = at++;
        if (ch == '(' || ch == ')') {
            result.emplace_back(1, char(ch));
            continue;
        }
        if (ch == '|' || ch == '"') {
            bool closed = false;
            while (at < source.size()) {
                if ((at & 4095) == 0) budget.check();
                if (source[at++] != char(ch)) continue;
                if (ch == '"' && at < source.size() && source[at] == '"') { ++at; continue; }
                closed = true;
                break;
            }
            if (!closed) throw std::runtime_error("unterminated SMT-LIB quoted symbol/string");
        } else {
            while (at < source.size() && !std::isspace(static_cast<unsigned char>(source[at]))
                   && source[at] != '(' && source[at] != ')' && source[at] != ';') {
                ++at;
                if ((at & 4095) == 0) budget.check();
            }
        }
        result.push_back(source.substr(begin, at - begin));
    }
    return result;
}

struct Form {
    bool list = false;
    std::string atom;
    std::vector<Form> children;
};
Form expression(const std::vector<std::string>& input, size_t& at,
                const Budget& budget, unsigned depth = 0) {
    budget.check();
    if (at >= input.size() || input[at] == ")") throw std::runtime_error("malformed SMT-LIB expression");
    if (depth > 1024) throw std::runtime_error("SMT-LIB expression nesting is too deep");
    Form result;
    if (input[at] != "(") { result.atom = input[at++]; return result; }
    result.list = true;
    ++at;
    while (at < input.size() && input[at] != ")")
        result.children.push_back(expression(input, at, budget, depth + 1));
    if (at == input.size()) throw std::runtime_error("unclosed SMT-LIB parenthesis");
    ++at;
    return result;
}
std::vector<Form> forms(const std::string& source, const Budget& budget) {
    auto input = tokens(source, budget);
    std::vector<Form> result;
    size_t at = 0;
    while (at < input.size()) result.push_back(expression(input, at, budget));
    return result;
}
void render(const Form& form, std::ostream& out, const Budget& budget) {
    budget.check();
    if (!form.list) { out << form.atom; return; }
    out << '(';
    for (size_t i = 0; i < form.children.size(); ++i) {
        if (i) out << ' ';
        render(form.children[i], out, budget);
    }
    out << ')';
}
std::string rendered(const Form& form, const Budget& budget) {
    std::ostringstream text;
    render(form, text, budget);
    return text.str();
}

std::string safe_query_source(const ls_solver& solver, const std::string& source, const Budget& budget) {
    const std::set<std::string> accepted = {"declare-const", "declare-fun", "define-fun", "define-fun-rec",
        "define-funs-rec", "declare-sort", "define-sort", "assert", "set-logic"};
    auto expected = forms(solver.omt_original_objective, budget);
    if (expected.size() != 1) throw std::runtime_error("missing or malformed original objective metadata");
    const auto expected_objective = rendered(expected[0], budget);
    std::ostringstream query;
    unsigned objectives = 0, checks = 0;
    for (const auto& form : forms(source, budget)) {
        budget.check();
        if (!form.list || form.children.empty() || form.children[0].list)
            throw std::runtime_error("expected top-level SMT-LIB commands");
        const auto& command = form.children[0].atom;
        const bool output_command = command.compare(0, 4, "get-") == 0;
        if (command == "exit") {
            if (form.children.size() != 1) throw std::runtime_error("malformed exit command");
            break;
        }
        if (checks && command != "echo" && command != "set-info" && !output_command)
            throw std::runtime_error("command changes query state after check-sat: " + command);
        if (command == "minimize" || command == "maximize") {
            if (++objectives != 1 || form.children.size() != 2)
                throw std::runtime_error("expected exactly one scalar objective");
            const std::string direction = command == "minimize" ? "min" : "max";
            if (direction != solver.omt_direction)
                throw std::runtime_error("original objective direction differs from native metadata");
            const auto objective = rendered(form.children[1], budget);
            if (objective != expected_objective)
                throw std::runtime_error("original objective differs from native metadata");
            // Normalize only the parser command. The extracted objective stays
            // in its original sign for both min and max, just as in the converter.
            query << "(minimize " << objective << ")\n";
        } else if (command == "check-sat") {
            if (++checks > 1 || form.children.size() != 1)
                throw std::runtime_error("only one nonincremental check-sat is supported");
        } else if (command == "set-option" || command == "set-info" || command == "echo" || output_command) {
            // In particular never let source :timeout/:rlimit/:opt.* options
            // override this probe, or execute source queries and output commands.
        } else if (accepted.count(command)) {
            render(form, query, budget);
            query << '\n';
        } else {
            throw std::runtime_error("unsupported SMT-LIB command: " + command);
        }
    }
    if (objectives != 1) throw std::runtime_error("expected exactly one original objective");
    return query.str();
}

void exact_integer(const std::string& value) {
    size_t at = !value.empty() && value[0] == '-' ? 1 : 0;
    if (at == value.size()) throw std::runtime_error("incumbent must be an exact decimal integer");
    for (; at < value.size(); ++at)
        if (value[at] < '0' || value[at] > '9')
            throw std::runtime_error("incumbent must be an exact decimal integer");
}
void save_query(const std::string& output, const std::string& source, const std::string& text) {
    if (output.empty()) throw std::runtime_error("proof query output path is required");
    std::error_code error;
    if (std::filesystem::equivalent(output, source, error) && !error)
        throw std::runtime_error("proof query output would overwrite original source");
    std::ofstream file(output, std::ios::binary | std::ios::trunc);
    if (!file) throw std::runtime_error("cannot write proof query: " + output);
    file << text;
    file.close();
    if (!file) throw std::runtime_error("cannot finish writing proof query: " + output);
}
}

namespace {
// The first eight bytes are progress information even when a killed worker
// cannot serialize a final answer. Remaining bytes hold a versioned result.
void report_queries(int fd, unsigned queries) {
    const uint64_t value = queries;
    if (pwrite(fd, &value, sizeof(value), 0) != sizeof(value))
        throw std::runtime_error("cannot record optimality worker progress");
}
OmtOptimalityResult run_probe(const ls_solver& solver, const std::string& best,
                             const Budget& budget, const std::string& query_output, int progress_fd,
                             QueryMode mode, bool finite_cutset = false,
                             bool convex_exchange = false,
                             const std::vector<__int128_t>* incumbent_hint = nullptr,
                             unsigned smt_exchange = 0, bool integer_convex = false,
                             bool isolate_context = false, bool integer_affine = false,
                             bool objective_slice = false, double objective_slice_budget_seconds = 2.0,
                             double advisory_share = 0, double initial_window = 0,
                             double bv_seconds = 0, bool guarded_product = false) {
    OmtOptimalityResult result;
    // Created only for an enabled new worker, before common source setup. The
    // main budget remains independent; prefix expiry must fall through to the
    // original default solver rather than terminate this proof attempt.
    std::optional<Budget> prefix;
    if (advisory_share > 0) prefix.emplace(advisory_share * std::max(0., initial_window));
    auto prefix_open = [&]() { return !prefix || prefix->remaining() > 0; };
    auto auxiliary_remaining = [&]() {
        const double remaining = budget.remaining();
        return prefix ? std::min(remaining, prefix->remaining()) : remaining;
    };
    auto received_in_time = [&](Clock::time_point began, double allowance) {
        if (!prefix) return true;
        const auto now = Clock::now();
        return std::chrono::duration<double>(now - began).count() < allowance
            && std::chrono::duration<double>(now - prefix->start).count() < prefix->allowance;
    };
    auto log_prefix = [&](const char* phase, const char* stage, double allowance = -1) {
        if (!prefix) return;
        const double elapsed = prefix->elapsed();
        std::cerr << "omt_advisory_prefix phase=" << phase << " stage=" << stage
                  << " initial_window=" << initial_window << " fraction=" << advisory_share
                  << " allowance=" << prefix->allowance << " elapsed=" << elapsed
                  << " remaining=" << std::max(0., prefix->allowance - elapsed)
                  << " main_remaining=" << budget.remaining() << " soft_wall=1";
        if (allowance >= 0) std::cerr << " stage_allowance=" << allowance;
        std::cerr << '\n';
    };
    log_prefix("start", "common_setup");
    try {
        budget.check();
        if (!solver.omt_loaded || (solver.omt_direction != "min" && solver.omt_direction != "max"))
            throw std::runtime_error("missing native optimization metadata");
        if (mode == QueryMode::strict_improvement) exact_integer(best);
        auto original = read_source(solver.omt_source, budget);
        auto filtered = safe_query_source(solver, original, budget);
        budget.check();
        {
            z3::context context;
            z3::optimize parser(context);
            parser.from_string(filtered.c_str()); // Parsing only; never Optimize.check().
            budget.check();
            const auto objectives = parser.objectives();
            if (objectives.size() != 1 || !objectives[0].is_int())
                throw std::runtime_error("original source must have one integer objective");
            unsigned bv_milliseconds = 0;
            if (mode == QueryMode::strict_improvement && bv_seconds > 0) {
                // Zero disables Z3's timer. Accepted positive values must never
                // round to it; the validated upper bound also prevents overflow.
                bv_milliseconds = unsigned(std::max(1., std::ceil(bv_seconds * 1000)));
                std::cerr << "omt_optimality_bv phase=configured seconds=" << bv_seconds
                          << " effective_ms=" << bv_milliseconds
                          << " fallback=default soft_wall=1 pauses_included=1\n";
            }
            z3::solver query = bv_milliseconds
                ? (z3::try_for(z3::tactic(context, "qfnia-omt-bv"), bv_milliseconds)
                   | z3::tactic(context, "default")).mk_solver()
                : z3::solver(context);
            auto check_source = [&]() {
                // Advisory answers can finish before this point. Configuration
                // alone is not evidence that the composed strategy was checked.
                if (bv_milliseconds)
                    std::cerr << "omt_optimality_bv phase=check seconds=" << bv_seconds
                              << " effective_ms=" << bv_milliseconds
                              << " fallback=default soft_wall=1 pauses_included=1\n";
                return query.check();
            };
            query.add(parser.assertions()); // Full original Boolean/arithmetic hard formula.
            if (mode == QueryMode::strict_improvement) {
                const auto bound = context.int_val(best.c_str());
                query.add(solver.omt_direction == "min" ? objectives[0] < bound : objectives[0] > bound);
            }
            budget.check();
            const auto smt = query.to_smt2();
            save_query(query_output, solver.omt_source, smt);
            budget.check();
            if (std::isfinite(budget.remaining())) {
                const double milliseconds = std::floor(budget.remaining() * 1000);
                if (milliseconds < 1) throw Expired{};
                query.set("timeout", unsigned(std::min(milliseconds, double(std::numeric_limits<unsigned>::max()))));
            }
            ++result.queries;
            report_queries(progress_fd, result.queries);
            std::unique_ptr<z3::model> cutset_model;
            z3::check_result answer = z3::unknown;
            bool cutset_answer = false;
            if (guarded_product && mode == QueryMode::strict_improvement && prefix_open()) {
                log_prefix("enter", "guarded_product");
                const auto began = Clock::now();
                // Auxiliary Real symbols and tactics never enter the source
                // solver, so inconclusive lifting preserves its search state.
                z3::context auxiliary;
                z3::expr_vector hard(auxiliary, parser.assertions());
                z3::expr objective(auxiliary, Z3_translate(context, objectives[0], auxiliary));
                budget.check();
                const double setup = std::chrono::duration<double>(Clock::now() - began).count();
                const double allowance = std::min(auxiliary_remaining(), 3.0 - setup);
                if (allowance > 0) {
                    const auto call_began = Clock::now();
                    const std::string stem = query_output + ".guarded." + std::to_string(getpid());
                    auto lifted = solve_omt_guarded_product(auxiliary, hard, objective,
                        solver.omt_direction == "min", best, allowance, stem);
                    const double elapsed = std::chrono::duration<double>(Clock::now() - call_began).count();
                    const bool timely = elapsed < allowance && received_in_time(call_began, allowance);
                    log_prefix(timely ? "return" : "late", "guarded_product", allowance);
                    std::cerr << "omt_guarded_product applicable=" << lifted.applicable
                              << " answer=" << lifted.answer << " queries=" << lifted.queries
                              << " cuts=" << lifted.cuts << " monomials=" << lifted.monomials
                              << " generation_seconds=" << lifted.generation_seconds
                              << " check_seconds=" << lifted.check_seconds
                              << " seconds=" << std::chrono::duration<double>(Clock::now() - began).count()
                              << " timely=" << timely << " reason=" << lifted.reason << '\n';
                    budget.check();
                    // SAT is only a Real monomial relaxation model. It must
                    // never become source SAT, a native candidate, or a flag
                    // preventing another proof attempt at this same bound.
                    if (timely && lifted.applicable && lifted.answer == z3::unsat) {
                        answer = z3::unsat;
                        cutset_answer = true;
                        result.reason = "guarded_product cuts=" + std::to_string(lifted.cuts)
                            + " certificate=" + lifted.certificate_path;
                    }
                }
            }
            if (!cutset_answer && objective_slice && mode == QueryMode::strict_improvement && prefix_open()) {
                log_prefix("enter", "source_slice");
                if (!std::isfinite(objective_slice_budget_seconds) || objective_slice_budget_seconds <= 0)
                    throw std::runtime_error("objective slice budget must be finite positive seconds");
                const auto began = Clock::now();
                const std::string certificate = query_output.empty() ? std::string() :
                    query_output + ".slice." + std::to_string(getpid()) + ".json";
                std::error_code certificate_error;
                if (!certificate.empty() &&
                    std::filesystem::equivalent(certificate, solver.omt_source, certificate_error)
                    && !certificate_error)
                    throw std::runtime_error("objective slice certificate would overwrite original source");
                // This strategy always owns an advisory context. Inconclusive
                // subset checks must not alter the default source solver.
                z3::context auxiliary;
                z3::expr_vector hard(auxiliary, parser.assertions());
                z3::expr objective(auxiliary, Z3_translate(context, objectives[0], auxiliary));
                budget.check();
                const double setup = std::chrono::duration<double>(Clock::now() - began).count();
                const double allowance = std::min(auxiliary_remaining(), objective_slice_budget_seconds - setup);
                if (allowance > 0) {
                    const auto call_began = prefix ? Clock::now() : Clock::time_point{};
                    auto sliced = solve_omt_objective_slice(auxiliary, hard, objective,
                        solver.omt_direction == "min", best, allowance, certificate);
                    const bool timely = received_in_time(call_began, allowance);
                    log_prefix(timely ? "return" : "late", "source_slice", allowance);
                    std::cerr << "omt_objective_slice applicable=" << sliced.applicable
                              << " answer=" << sliced.answer << " queries=" << sliced.queries
                              << " refinements=" << sliced.refinements
                              << " initial=" << sliced.initial_assertions
                              << " selected=" << sliced.selected_assertions
                              << " total=" << sliced.total_assertions
                              << " initial_ast=" << sliced.initial_ast_nodes
                              << " total_ast=" << sliced.total_ast_nodes
                              << " seconds=" << std::chrono::duration<double>(Clock::now() - began).count()
                              << " budget=" << objective_slice_budget_seconds
                              << " reason=" << sliced.reason << '\n';
                    budget.check();
                    if (timely && sliced.applicable && (sliced.answer == "unsat" ||
                        (sliced.answer == "sat" && sliced.source_model))) {
                        answer = sliced.answer == "unsat" ? z3::unsat : z3::sat;
                        if (sliced.source_model)
                            cutset_model = std::make_unique<z3::model>(
                                *sliced.source_model, context, z3::model::translate{});
                        cutset_answer = true;
                        result.reason = "objective_slice queries=" + std::to_string(sliced.queries)
                            + " selected=" + std::to_string(sliced.selected_assertions)
                            + " certificate=" + certificate;
                    }
                }
            }
            if (!cutset_answer && (convex_exchange||integer_convex||integer_affine)
                && mode == QueryMode::strict_improvement && prefix_open()) {
                log_prefix("enter", "convex");
                std::error_code certificate_error;
                if(std::filesystem::equivalent(query_output+".convex.json",solver.omt_source,certificate_error)
                   &&!certificate_error)
                    throw std::runtime_error("convex certificate output would overwrite original source");
                std::map<std::string,std::string> hints;
                if (incumbent_hint && incumbent_hint->size() == solver._num_vars)
                    for (size_t i=0;i<solver._num_vars;++i)
                        if (solver._vars[i].is_nia)
                            hints.emplace(solver._vars[i].var_name,format_omt_integer((*incumbent_hint)[i]));
                std::unique_ptr<z3::context> auxiliary;
                if(isolate_context)auxiliary=std::make_unique<z3::context>();
                z3::context& convex_context=auxiliary?*auxiliary:context;
                z3::expr_vector convex_hard=isolate_context
                    ?z3::expr_vector(convex_context,parser.assertions()):parser.assertions();
                z3::expr convex_objective=isolate_context
                    ?z3::expr(convex_context,Z3_translate(context,objectives[0],convex_context)):objectives[0];
                budget.check();
                const double allowance=auxiliary_remaining();
                OmtConvexExchangeResult convex;
                const auto call_began=prefix?Clock::now():Clock::time_point{};
                if(!prefix||allowance>0)convex=solve_omt_convex_exchange(convex_context,convex_hard,convex_objective,
                    solver.omt_direction=="min",best,hints,allowance,query_output+".convex.json",integer_convex,integer_affine);
                const bool timely=received_in_time(call_began,allowance);
                log_prefix(timely?"return":"late","convex",std::max(0.,allowance));
                std::cerr<<"omt_convex_exchange applicable="<<convex.applicable
                    <<" answer="<<convex.answer<<" queries="<<convex.queries<<" cuts="<<convex.cuts
                    <<" reason="<<convex.reason<<'\n';
                budget.check();
                if(timely&&convex.applicable&&(convex.answer==z3::unsat
                    ||(convex.answer==z3::sat&&convex.model))){
                    answer=convex.answer;
                    if(convex.model){
                        if(isolate_context)cutset_model=std::make_unique<z3::model>(
                            *convex.model,context,z3::model::translate{});
                        else cutset_model=std::move(convex.model);
                    }
                    cutset_answer=true;
                    result.reason="convex_outer_approximation cuts="+std::to_string(convex.cuts)
                        +" certificate="+query_output+".convex.json";
                }
            }
            if (!cutset_answer && finite_cutset && mode == QueryMode::strict_improvement && prefix_open()) {
                log_prefix("enter", "cutset");
                const double allowance=auxiliary_remaining();
                const auto call_began=prefix?Clock::now():Clock::time_point{};
                auto covered = solve_omt_finite_cutset(context, query.assertions(), allowance);
                const bool timely=received_in_time(call_began,allowance);
                log_prefix(timely?"return":"late","cutset",std::max(0.,allowance));
                std::cerr << "omt_optimality_cutset applicable=" << covered.applicable
                          << " branches=" << covered.branches << " answer=" << covered.answer
                          << " reason=" << covered.reason << '\n';
                budget.check();
                if (timely && covered.applicable && (covered.answer == z3::unsat
                    || (covered.answer == z3::sat && covered.model))) {
                    answer = covered.answer;
                    cutset_model = std::move(covered.model);
                    cutset_answer = true;
                }
            }
            // Unsupported or inconclusive covers retain the complete original
            // query and its ordinary QF_NIA path. No sampled cover proves UNSAT.
            if (!cutset_answer) {
                if(smt_exchange&&prefix_open()){
                    log_prefix("enter", "smt");
                    if(smt_exchange>2)throw std::runtime_error("invalid SMT exchange mode");
                    // Keep the original source solver: this optional backend
                    // is only a bounded advisory preflight, never a replacement
                    // for the established default nonlinear proof route.
                    const auto preflight_began=Clock::now();
                    auto preflight_remaining=[&](){
                        return std::min(auxiliary_remaining(),5.0-
                            std::chrono::duration<double>(Clock::now()-preflight_began).count());
                    };
                    // No propagator, callback or hint equality is installed.
                    std::unique_ptr<z3::context> auxiliary;
                    if(isolate_context)auxiliary=std::make_unique<z3::context>();
                    z3::context& preflight_context=auxiliary?*auxiliary:context;
                    z3::tactic strategy(preflight_context,"smt");
                    auto descriptions=strategy.get_param_descrs();
                    z3::solver selected=strategy.mk_solver();
                    selected.set("arith.solver",2u);
                    if(isolate_context)selected.add(z3::expr_vector(preflight_context,query.assertions()));
                    else selected.add(query.assertions());
                    size_t mapped=0;
                    if(smt_exchange==2&&incumbent_hint&&incumbent_hint->size()==solver._num_vars){
                        std::map<std::string,size_t> native_names;
                        for(size_t i=0;i<solver._num_vars&&preflight_remaining()>0;++i){
                            budget.check();
                            if(solver._vars[i].is_nia)native_names.emplace(solver._vars[i].var_name,i);
                        }
                        std::vector<z3::expr> pending;
                        for(const auto& assertion:selected.assertions())pending.push_back(assertion);
                        std::set<unsigned> visited;
                        while(!pending.empty()&&visited.size()<200000&&preflight_remaining()>0){
                            budget.check();auto current=pending.back();pending.pop_back();
                            if(!visited.insert(current.id()).second||!current.is_app())continue;
                            if(current.is_int()&&current.num_args()==0&&
                               current.decl().decl_kind()==Z3_OP_UNINTERPRETED){
                                const auto found=native_names.find(current.decl().name().str());
                                if(found!=native_names.end()){
                                    const auto text=format_omt_integer((*incumbent_hint)[found->second]);
                                    selected.set_initial_value(current,preflight_context.int_val(text.c_str()));++mapped;
                                }
                            }
                            for(unsigned i=0;i<current.num_args();++i)pending.push_back(current.arg(i));
                        }
                    }
                    std::cerr<<"omt_smt_exchange mode="<<(smt_exchange==2?"hints":"backend")
                        <<" mapped="<<mapped<<" arithmetic=2 preflight_limit_seconds=5\n";
                    result.reason=std::string("smt_exchange ")+(smt_exchange==2?"hints":"backend")
                        +" mapped="+std::to_string(mapped);
                    budget.check();
                    const double preflight_ms=std::floor(preflight_remaining()*1000);
                    std::string preflight_reason;
                    if(preflight_ms>=1){
                        selected.set("timeout",unsigned(preflight_ms));
                        const auto call_began=prefix?Clock::now():Clock::time_point{};
                        answer=selected.check();
                        if(answer==z3::unknown)preflight_reason=selected.reason_unknown();
                        if(prefix&&(!received_in_time(call_began,preflight_ms*.001)||!(preflight_remaining()>0))){
                            answer=z3::unknown;
                            preflight_reason="shared advisory prefix or supplied preflight allowance exhausted";
                        }
                    }else preflight_reason="preflight allowance exhausted during setup";
                    log_prefix("return","smt",std::max(0.,preflight_ms*.001));
                    std::cerr<<"omt_smt_exchange_preflight answer="<<answer
                        <<" seconds="<<std::chrono::duration<double>(Clock::now()-preflight_began).count()
                        <<" reason="<<preflight_reason<<'\n';
                    budget.check();
                    if(answer==z3::sat||answer==z3::unsat){
                        // Keep this checked solver/model only for a determinate
                        // answer; the existing exact source/native bridge follows.
                        if(isolate_context){
                            if(answer==z3::sat){
                                auto model=selected.get_model();
                                cutset_model=std::make_unique<z3::model>(model,context,z3::model::translate{});
                            }
                        }else query=selected;
                    }else{
                        result.reason+=" preflight unknown: "+preflight_reason+"; fallback default";
                        std::cerr<<"omt_smt_exchange_fallback backend=default reason="<<preflight_reason<<'\n';
                        // Setup and preflight consume this same allowance. A
                        // persistent worker has an infinite internal clock and
                        // remains governed by its parent's pause/kill deadline.
                        if(std::isfinite(budget.remaining())){
                            const double ms=std::floor(budget.remaining()*1000);
                            if(ms<1)throw Expired{};
                            query.set("timeout",unsigned(std::min(ms,double(std::numeric_limits<unsigned>::max()))));
                        }
                        budget.check();
                        log_prefix("default","original_source");
                        answer=check_source();
                    }
                }else {
                    log_prefix("default","original_source");
                    answer=check_source();
                }
            }
            budget.check();
            if (answer == z3::unsat) {
                result.proved = mode == QueryMode::strict_improvement;
                result.status = result.proved ? "proved" : "unsat";
            }
            else if (answer == z3::sat) {
                // SAT disproves optimality, but only a recovered and validated
                // complete native assignment can update the SLS incumbent.
                result.status = "sat";
                try {
                    const auto model = cutset_model ? *cutset_model : query.get_model();
                    const auto value = model.eval(objectives[0], true);
                    if (!value.is_int() || !value.is_numeral()) {
                        result.reason = "SAT original objective did not evaluate to an exact integer";
                    } else {
                        result.candidate_value = Z3_get_numeral_string(context, value);
                        exact_integer(result.candidate_value);
                        bool improves = true;
                        if (mode == QueryMode::strict_improvement) {
                            const mpz_class objective(result.candidate_value, 10), incumbent(best, 10);
                            improves = solver.omt_direction == "min"
                                ? objective < incumbent : objective > incumbent;
                        }
                        if (!improves) {
                            result.reason = "SAT original objective is not a strict improvement";
                        } else {
                            // Persistent workers have no internal slice clock:
                            // the parent pauses/kills them across this entire
                            // operation, including exact model restoration.
                            const auto bridge_budget = std::isfinite(budget.remaining())
                                ? budget.remaining() : std::numeric_limits<double>::max();
                            const auto bridge = restore_omt_native_model(solver, model, parser.assertions(),
                                                                        objectives[0], bridge_budget);
                            if (bridge.imported && bridge.assignment.size() == solver._num_vars
                                && bridge.objective == result.candidate_value) {
                                result.candidate_available = true;
                                result.candidate = bridge.assignment;
                                result.candidate_value = bridge.objective;
                            } else {
                                result.reason = "SAT native candidate recovery " + bridge.status + ": "
                                    + (bridge.reason.empty() ? "no complete objective-consistent assignment" : bridge.reason);
                            }
                        }
                    }
                } catch (const z3::exception& error) {
                    result.reason = std::string("SAT native candidate recovery error: ") + error.msg();
                } catch (const std::exception& error) {
                    result.reason = std::string("SAT native candidate recovery error: ") + error.what();
                }
            }
            else {
                result.status="unknown";
                if(smt_exchange&&!result.reason.empty())result.reason+="; default unknown: "+query.reason_unknown();
                else result.reason=query.reason_unknown();
            }
        } // Include model/context construction and destruction in the allowance.
        budget.check();
    } catch (const Expired&) {
        result.proved = false;
        result.status = "budget";
        result.reason = "finite optimality probe exhausted its time allowance";
    } catch (const z3::exception& error) {
        result.proved = false;
        result.status = "error";
        result.reason = error.msg();
    } catch (const std::exception& error) {
        result.proved = false;
        result.status = "error";
        result.reason = error.what();
    }
    result.seconds = budget.elapsed();
    if (result.proved && budget.remaining() <= 0) {
        result.proved = false;
        result.status = "budget";
        result.reason = "finite optimality proof completed after its allowance";
    }
    return result;
}

class ResultFile {
public:
    int fd = -1;
    ResultFile() {
        char path[] = "/tmp/nia-omt-probe-XXXXXX";
        fd = mkstemp(path);
        if (fd < 0) throw std::runtime_error("cannot create optimality worker result");
        unlink(path); // No persistent model files or cleanup race between probes.
        try { report_queries(fd, 0); }
        catch (...) { close(fd); fd = -1; throw; }
    }
    ~ResultFile() { if (fd >= 0) close(fd); }
};
void write_result(int fd, const OmtOptimalityResult& result) {
    std::ostringstream output;
    output << "NIA_OMT_PROBE 1\n" << result.proved << ' ' << result.queries << ' '
           << std::quoted(result.status) << ' ' << std::quoted(result.reason) << ' '
           << result.candidate_available << ' ' << std::quoted(result.candidate_value) << ' '
           << result.candidate.size() << '\n';
    for (const auto value : result.candidate) output << format_omt_integer(value) << '\n';
    const auto text = output.str();
    size_t written = 0;
    while (written < text.size()) {
        const auto count = pwrite(fd, text.data() + written, text.size() - written,
                                  sizeof(uint64_t) + written);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) throw std::runtime_error("cannot write optimality worker result");
        written += size_t(count);
    }
}
OmtOptimalityResult read_result(int fd, size_t variables, const Budget& budget) {
    std::string text;
    char buffer[65536];
    off_t offset = sizeof(uint64_t);
    while (true) {
        budget.check();
        const auto count = pread(fd, buffer, sizeof(buffer), offset);
        if (count < 0 && errno == EINTR) continue;
        if (count < 0) throw std::runtime_error("cannot read optimality worker result");
        if (!count) break;
        text.append(buffer, size_t(count)); offset += count;
    }
    std::istringstream input(text);
    OmtOptimalityResult result;
    std::string magic;
    unsigned version;
    size_t size;
    if (!(input >> magic >> version) || magic != "NIA_OMT_PROBE" || version != 1 ||
        !(input >> result.proved >> result.queries >> std::quoted(result.status)
                >> std::quoted(result.reason) >> result.candidate_available
                >> std::quoted(result.candidate_value) >> size) || size > variables)
        throw std::runtime_error("invalid optimality worker result");
    result.candidate.resize(size);
    for (auto& value : result.candidate) {
        budget.check();
        std::string decimal;
        if (!(input >> decimal) || !parse_omt_integer(decimal, value))
            throw std::runtime_error("invalid native assignment in optimality worker result");
    }
    input >> std::ws;
    if (!input.eof() || (result.candidate_available && size != variables) ||
        (!result.candidate_available && size != 0))
        throw std::runtime_error("incomplete native assignment in optimality worker result");
    budget.check();
    return result;
}
void kill_and_reap(pid_t worker) {
    // The worker has no subprocesses. SIGKILL also interrupts Z3 phases whose
    // advisory :timeout does not return promptly (and blocking source reads).
    kill(worker, SIGKILL);
    int status;
    while (waitpid(worker, &status, 0) < 0 && errno == EINTR) {}
}
}

static OmtOptimalityResult run_isolated_source_query(const ls_solver& solver, const std::string& best,
                                       double seconds, const std::string& query_output, QueryMode mode,
                                       bool finite_cutset = false, bool convex_exchange = false,
                                       const std::vector<__int128_t>* incumbent_hint = nullptr,
                                       unsigned smt_exchange = 0, bool integer_convex = false,
                                       bool isolate_context = false, bool integer_affine = false,
                                       bool objective_slice = false, double objective_slice_budget_seconds = 2.0,
                                       double advisory_share = 0, double bv_seconds = 0, bool guarded_product = false) {
    OmtOptimalityResult result;
    Budget budget(seconds);
    if (!std::isfinite(seconds) || seconds <= 0) {
        result.status = "budget";
        result.seconds = budget.elapsed();
        return result;
    }
    if (!valid_advisory_share(advisory_share)) {
        result.status = "error";
        result.reason = "advisory share must be zero or a finite fraction strictly between zero and one";
        return result;
    }
    if (!valid_bv_seconds(bv_seconds)) {
        result.status = "error";
        result.reason = "BV seconds must be zero or finite in [0.001,1200]";
        return result;
    }
    pid_t worker = -1;
    bool reaped = false;
    std::unique_ptr<ResultFile> file;
    try {
        budget.check();
        file = std::make_unique<ResultFile>();
        budget.check();
        worker = fork();
        if (worker < 0) throw std::runtime_error("cannot fork optimality worker");
        if (worker == 0) {
            // A fresh context is constructed inside run_probe; no inherited Z3
            // object is reused. _exit avoids parent/global cleanup in the child.
            try {
                const double initial_window = advisory_share > 0 ? std::max(0., budget.remaining()) : 0;
                write_result(file->fd, run_probe(solver, best, budget, query_output, file->fd, mode,
                                                finite_cutset,convex_exchange,incumbent_hint,smt_exchange,integer_convex,isolate_context,integer_affine,objective_slice,objective_slice_budget_seconds,advisory_share,initial_window,bv_seconds,guarded_product));
                _exit(0);
            } catch (...) { _exit(121); }
        }
        int status = 0;
        while (true) {
            budget.check();
            const auto answer = waitpid(worker, &status, WNOHANG);
            if (answer == worker) { reaped = true; break; }
            if (answer < 0 && errno == EINTR) continue;
            if (answer < 0) throw std::runtime_error("cannot wait for optimality worker");
            const double pause = std::min(0.002, std::max(0., budget.remaining()));
            const timespec delay{0, long(pause * 1e9)};
            nanosleep(&delay, nullptr);
        }
        budget.check();
        if (!WIFEXITED(status) || WEXITSTATUS(status) != 0) {
            result.status = "error";
            result.reason = WIFSIGNALED(status)
                ? "optimality worker terminated by signal " + std::to_string(WTERMSIG(status))
                : "optimality worker exited with code " + std::to_string(WEXITSTATUS(status));
        } else result = read_result(file->fd, solver._num_vars, budget);
    } catch (const Expired&) {
        if (worker > 0 && !reaped) { kill_and_reap(worker); reaped = true; }
        result = OmtOptimalityResult{};
        result.status = "budget";
        result.reason = "finite optimality probe exhausted its hard wallclock allowance";
    } catch (const std::exception& error) {
        if (worker > 0 && !reaped) { kill_and_reap(worker); reaped = true; }
        result = OmtOptimalityResult{};
        result.status = "error";
        result.reason = error.what();
    }
    if (file) {
        uint64_t queries = 0;
        if (pread(file->fd, &queries, sizeof(queries), 0) == sizeof(queries))
            result.queries = std::max(result.queries, unsigned(std::min<uint64_t>(queries, 1)));
    }
    result.seconds = budget.elapsed();
    if (result.seconds >= seconds && (result.proved || result.candidate_available)) {
        result.proved = false;
        result.candidate_available = false;
        result.candidate.clear();
        result.candidate_value.clear();
        result.status = "budget";
        result.reason = "finite optimality result arrived after its hard wallclock allowance";
    }
    return result;
}
OmtOptimalityResult prove_omt_optimality(const ls_solver& solver, const std::string& best,
                                       double seconds, const std::string& query_output, bool finite_cutset,
                                       bool convex_exchange,const std::vector<__int128_t>* incumbent_hint,
                                       unsigned smt_exchange, bool integer_convex, bool isolate_context,
                                       bool integer_affine, bool objective_slice, double objective_slice_budget_seconds,
                                       double advisory_share, double bv_seconds, bool guarded_product) {
    return run_isolated_source_query(solver, best, seconds, query_output, QueryMode::strict_improvement,
                                    finite_cutset,convex_exchange,incumbent_hint,smt_exchange,integer_convex,isolate_context,integer_affine,objective_slice,objective_slice_budget_seconds,advisory_share,bv_seconds,guarded_product);
}
OmtOptimalityResult find_omt_feasible(const ls_solver& solver, double seconds,
                                     const std::string& query_output) {
    auto result = run_isolated_source_query(solver, {}, seconds, query_output, QueryMode::feasibility);
    result.proved = false;
    return result;
}
struct OmtOptimalitySession::Impl {
    const ls_solver& solver;
    bool finite_cutset;
    bool convex_exchange;
    unsigned smt_exchange;
    bool integer_convex, isolate_context, integer_affine, objective_slice, guarded_product;
    double objective_slice_budget_seconds, advisory_share, bv_seconds;
    std::string best, query_output;
    std::unique_ptr<ResultFile> file;
    pid_t worker = -1;
    bool stopped = false, ready = false;
    int completion_status = 0;
    unsigned starts = 0, resumes = 0;
    explicit Impl(const ls_solver& value, bool cutset, bool convex, unsigned smt, bool integer,
                           bool isolate, bool affine, bool slice, double slice_budget, double share, double bv, bool product)
        : solver(value), finite_cutset(cutset), convex_exchange(convex), smt_exchange(smt),
          integer_convex(integer), isolate_context(isolate), integer_affine(affine), objective_slice(slice), guarded_product(product), objective_slice_budget_seconds(slice_budget), advisory_share(share), bv_seconds(bv) {
        if (!valid_advisory_share(share))
            throw std::invalid_argument("advisory share must be zero or a finite fraction strictly between zero and one");
        if (!valid_bv_seconds(bv))
            throw std::invalid_argument("BV seconds must be zero or finite in [0.001,1200]");
    }
    ~Impl() { reset(); }
    void reset() {
        if (worker > 0) kill_and_reap(worker);
        worker = -1; stopped = ready = false;
        file.reset(); best.clear(); query_output.clear();
    }
    void completed(int status) {
        worker = -1; stopped = false; ready = true;
        completion_status = status;
    }
    void pause() {
        if (worker <= 0 || stopped) return;
        // Confirmation is required: merely sending SIGSTOP still permits the
        // worker to consume CPU after this API returns to local search.
        if (kill(worker, SIGSTOP) < 0 && errno != ESRCH)
            throw std::runtime_error("cannot pause optimality session worker");
        int status;
        for (;;) {
            const auto answer = waitpid(worker, &status, WUNTRACED);
            if (answer < 0 && errno == EINTR) continue;
            if (answer < 0) throw std::runtime_error("cannot confirm paused optimality session worker");
            if (WIFSTOPPED(status)) { stopped = true; return; }
            if (WIFEXITED(status) || WIFSIGNALED(status)) { completed(status); return; }
        }
    }
    OmtOptimalityResult run(const std::string& incumbent, double seconds, const std::string& output,
                            const std::vector<__int128_t>* incumbent_hint) {
        OmtOptimalityResult result;
        Budget slice(seconds);
        if (!std::isfinite(seconds) || seconds <= 0) {
            result.status = "budget"; result.seconds = slice.elapsed(); return result;
        }
        try {
            exact_integer(incumbent);
            slice.check();
            if (incumbent != best || output != query_output) reset();
            if (!file) {
                best = incumbent; query_output = output;
                file = std::make_unique<ResultFile>();
                slice.check();
                const pid_t owner = getpid();
                worker = fork();
                if (worker < 0) throw std::runtime_error("cannot fork optimality session worker");
                if (worker == 0) {
                    // The caller may be killed without running its session
                    // destructor. SIGKILL also terminates a SIGSTOP'ed worker.
                    // Check the captured parent after arming the signal to
                    // cover a parent exit between fork() and prctl().
                    if (prctl(PR_SET_PDEATHSIG, SIGKILL, 0L, 0L, 0L) != 0) _exit(122);
                    if (getppid() != owner) _exit(123);
                    try {
                        // Paused wall time must never expire an internal Z3 or
                        // parsing budget. Only the parent's run() allowances
                        // and outer per-instance deadline govern this worker.
                        Budget unlimited(std::numeric_limits<double>::infinity());
                        const double initial_window = advisory_share > 0 ? std::max(0., slice.remaining()) : 0;
                        write_result(file->fd, run_probe(solver, incumbent, unlimited, output,
                                                        file->fd, QueryMode::strict_improvement,
                                                        finite_cutset,convex_exchange,incumbent_hint,smt_exchange,integer_convex,isolate_context,integer_affine,objective_slice,objective_slice_budget_seconds,advisory_share,initial_window,bv_seconds,guarded_product));
                        _exit(0);
                    } catch (...) { _exit(121); }
                }
                ++starts;
            } else if (stopped) {
                slice.check();
                if (kill(worker, SIGCONT) < 0 && errno != ESRCH)
                    throw std::runtime_error("cannot resume optimality session worker");
                stopped = false; ++resumes;
            }
            while (!ready) {
                slice.check();
                int status;
                const auto answer = waitpid(worker, &status, WNOHANG);
                if (answer == worker) { completed(status); break; }
                if (answer < 0 && errno == EINTR) continue;
                if (answer < 0) throw std::runtime_error("cannot wait for optimality session worker");
                const double pause = std::min(.002, std::max(0., slice.remaining()));
                const timespec delay{0, long(pause * 1e9)};
                nanosleep(&delay, nullptr);
            }
            slice.check();
            if (!WIFEXITED(completion_status) || WEXITSTATUS(completion_status) != 0) {
                result.status = "error";
                result.reason = WIFSIGNALED(completion_status)
                    ? "optimality session worker terminated by signal " + std::to_string(WTERMSIG(completion_status))
                    : "optimality session worker exited with code " + std::to_string(WEXITSTATUS(completion_status));
            } else result = read_result(file->fd, solver._num_vars, slice);
            slice.check();
        } catch (const Expired&) {
            result = OmtOptimalityResult{};
            result.status = "budget";
            result.reason = "optimality session paused at its wallclock slice boundary";
            try { pause(); }
            catch (const std::exception& error) { reset(); result.status = "error"; result.reason = error.what(); }
        } catch (const std::exception& error) {
            reset(); result = OmtOptimalityResult{};
            result.status = "error"; result.reason = error.what();
        }
        if (file) {
            uint64_t queries = 0;
            if (pread(file->fd, &queries, sizeof(queries), 0) == sizeof(queries))
                result.queries = std::max(result.queries, unsigned(std::min<uint64_t>(queries, 1)));
        }
        result.seconds = slice.elapsed();
        if (result.seconds >= seconds && (result.proved || result.candidate_available)) {
            // The result file remains ready for the next slice. Do not accept
            // an answer outside this allowance or silently discard its state.
            result = OmtOptimalityResult{};
            result.status = "budget";
            result.reason = "optimality session result arrived after its wallclock slice";
            result.seconds = slice.elapsed();
        }
        return result;
    }
};
OmtOptimalitySession::OmtOptimalitySession(const ls_solver& solver, bool cutset, bool convex, unsigned smt,
                                             bool integer, bool isolate, bool affine, bool slice, double slice_budget,
                                             double advisory_share, double bv_seconds, bool guarded_product)
    : impl_(std::make_unique<Impl>(solver, cutset, convex, smt,integer,isolate,affine,slice,slice_budget,advisory_share,bv_seconds,guarded_product)) {}
OmtOptimalitySession::~OmtOptimalitySession() = default;
OmtOptimalityResult OmtOptimalitySession::run(const std::string& best, double seconds,
                                             const std::string& output,
                                             const std::vector<__int128_t>* incumbent_hint) {
    return impl_->run(best, seconds, output,incumbent_hint);
}
void OmtOptimalitySession::reset() { impl_->reset(); }
unsigned OmtOptimalitySession::starts() const { return impl_->starts; }
unsigned OmtOptimalitySession::resumes() const { return impl_->resumes; }
bool OmtOptimalitySession::paused() const { return impl_->stopped; }
}
