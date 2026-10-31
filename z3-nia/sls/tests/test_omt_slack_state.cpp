// Real native import, optimizer projection/evaluation and exact validation.
// Unused optimizer entry points are discarded by --gc-sections when linking.
#include "../src/nia_omt_optimize.cpp"

namespace {
int failures=0;
void check(bool condition,const char* reason) {
    if(!condition){std::cerr<<"FAIL: "<<reason<<'\n';++failures;}
}
struct Product {
    long long coefficient;
    std::vector<std::pair<size_t,unsigned>> factors;
};
struct Formula {
    std::vector<std::string> literals;
    std::vector<std::vector<int>> clauses;
    int polynomial(char kind,long long constant,std::initializer_list<Product> terms) {
        std::ostringstream out;out<<kind<<' '<<constant<<' '<<terms.size()<<'\n';
        for(const auto& term:terms) {
            out<<term.coefficient<<' '<<term.factors.size();
            for(auto factor:term.factors)out<<' '<<factor.first<<' '<<factor.second;
            out<<'\n';
        }
        literals.push_back(out.str());return int(literals.size());
    }
    void bound(size_t variable,long long lo,long long hi) {
        clauses.push_back({polynomial('L',lo,{{-1,{{variable,1}}}})});
        clauses.push_back({polynomial('L',-hi,{{1,{{variable,1}}}})});
    }
    std::string text()const {
        std::ostringstream out;
        out<<"NIA_LS_OMT 1\nSTATUS ready\nSOURCE -\nORIGINAL_OBJECTIVE 636f7374\n"
              "DIRECTION min\nVARIABLES 3\n0 I 78\n1 I 79\n2 I 636f7374\n";
        out<<"LITERALS "<<literals.size()<<'\n';
        for(size_t id=0;id<literals.size();++id)out<<id+1<<' '<<literals[id];
        out<<"CLAUSES "<<clauses.size()<<'\n';
        for(const auto& clause:clauses) {
            out<<clause.size();for(int id:clause)out<<' '<<id;out<<'\n';
        }
        out<<"OBJECTIVE 0 1\n1 1 2 1\nEND\n";return out.str();
    }
};
void load(nia::ls_solver& solver,const Formula& formula) {
    std::string error;std::istringstream input(formula.text());
    if(!solver.load_omt_instance(input,error))throw std::runtime_error("native fixture: "+error);
}
Formula gram_formula(bool second,bool extra_hard=false) {
    Formula formula;
    formula.bound(0,0,second?207:29);formula.bound(1,0,second?69:30);
    const int a=second?3:20,b=second?9:19;
    formula.clauses.push_back({formula.polynomial('L',second?529:499,
        {{-a,{{0,1}}},{-b,{{1,1}}}})});
    formula.clauses.push_back({formula.polynomial('L',second?-623:-587,
        {{a,{{0,1}}},{b,{{1,1}}}})});
    formula.clauses.push_back({formula.polynomial('L',second?849:615,
        {{second?-16:-34,{{0,1}}},{second?-18:-33,{{1,1}}}})});
    formula.clauses.push_back({formula.polynomial('E',0,
        {{second?75:35,{{0,2}}},{second?36:48,{{0,1},{1,1}}},
         {second?42:70,{{1,2}}},{-1,{{2,1}}}})});
    if(extra_hard)formula.clauses.push_back({formula.polynomial('L',-576,{{1,{{0,2}}}})});
    return formula;
}
template<class Values> void dump(std::ostream& out,const Values& values) {
    out<<'[';for(const auto& value:values)out<<value<<',';out<<']';
}
std::string snapshot(const nia::State& state) {
    std::ostringstream out;out<<std::setprecision(17);
    for(auto value:state.x)out<<nia::format_omt_integer(value)<<',';
    dump(out,state.terms);dump(out,state.delta);dump(out,state.truth);
    dump(out,state.sat);dump(out,state.unsat);dump(out,state.weight);
    out<<state.objective<<' '<<state.total_cost<<' '<<state.total_distance<<' '<<state.iterations;
    const auto& solver=state.s;
    for(const auto& term:solver._terms)out<<nia::format_omt_integer(term.value)<<',';
    for(const auto& literal:solver._lits)
        out<<nia::format_omt_integer(literal.delta)<<':'<<literal.is_true<<',';
    for(const auto& clause:solver._clauses)out<<clause.sat_count<<':'<<clause.weight<<',';
    for(auto value:solver._solution)out<<nia::format_omt_integer(value)<<',';
    for(const auto& variable:solver._vars) {
        out<<variable.scoring_coffs_valid<<':';
        for(auto value:variable.scoring_coffs)out<<nia::format_omt_integer(value)<<',';
    }
    dump(out,solver._score_literal_offsets);dump(out,solver._score_clause_occurrences);
    dump(out,solver._score_clause_deltas);dump(out,solver._score_touched_clauses);
    out<<solver.mt;return out.str();
}
nia::Assignment applied(nia::Assignment values,const nia::Move& move) {
    for(auto item:move)values[item.first]=item.second;
    return values;
}
std::vector<nia::Move> proposals(const nia::State& state,const nia::Move& primary) {
#ifdef OMT_SLACK_STATE_BASELINE
    auto fixed=state.defined_variables;
    for(auto item:primary)fixed.insert(item.first);
    auto move=nia::difference_branch_move(state.s,state.x,state.lo,state.hi,primary,
                                         fixed,[](){return true;});
    return move.empty()?std::vector<nia::Move>{}:std::vector<nia::Move>{move};
#else
    return nia::omt_slack_repair_moves(state.s,state.x,state.lo,state.hi,primary,
                                      state.defined_variables,[](){return true;});
#endif
}
void gram_state(bool second) {
    nia::ls_solver solver(23,0,true);load(solver,gram_formula(second));nia::State state(solver);
    const nia::Assignment seed=second?nia::Assignment{3,58,148227}:nia::Assignment{21,5,22225};
    const nia::Assignment target=second?nia::Assignment{0,59,146202}:nia::Assignment{25,0,21875};
    const nia::Move primary={{second?size_t(0):size_t(1),0}};
    state.reset(seed);
    check(state.unsat.empty()&&nia::validate(solver,state.x)==state.objective,
          "real State starts at an exactly valid own-cost Gram incumbent");
    check(state.defined_variables.count(2)==1,"native Gram equality defines the objective cost alias");
    const auto before=snapshot(state);
    const auto raw=state.evaluate(primary);
    check(raw.violations>0&&state.better(raw.objective,state.objective),
          "real State raw primary improves projected objective but breaks hard arithmetic");
    check(snapshot(state)==before,"raw State evaluation preserves x, terms, deltas, caches and RNG");
    bool found=false,accepted=false;
    for(const auto& move:proposals(state,primary)) {
        auto unprojected=applied(seed,move);
        check(unprojected[2]==seed[2],"slack proposal leaves the old cost for real State projection");
        auto projected=state.project(move);
        check(snapshot(state)==before,"real State project is transactional across all caches and RNG");
        auto evaluation=state.evaluate(move);
        check(snapshot(state)==before,"real State evaluate is transactional across all caches and RNG");
        if(unprojected[0]!=target[0]||unprojected[1]!=target[1])continue;
        found=true;
        check(applied(seed,projected)==target&&applied(seed,evaluation.move)==target,
              "real State projects the full slack move to its newly recomputed cost alias");
        accepted=evaluation.violations==0&&evaluation.objective==nia::integer(target[2])&&
                 state.better(evaluation.objective,state.objective);
        check(accepted,"real State reports exact feasibility and true objective improvement for the Gram proposal");
        check(nia::validate(solver,applied(seed,evaluation.move))==evaluation.objective,
              "full original-formula GMP validation agrees with State candidate evaluation");
    }
    check(found,second?"real State receives the second Gram slack proposal":
                       "real State receives the first Gram slack proposal");
    check(accepted,"real State acceptance contract admits a projected strict Gram improvement");
    check(snapshot(state)==before,"all Gram proposal generation and previews leave source State unchanged");
    if(accepted)std::cout<<"State Gram "<<(second?2:1)<<": "
                         <<nia::format_omt_integer(seed[2])<<" -> "
                         <<nia::format_omt_integer(target[2])<<'\n';
}
void full_formula_rejects_another_broken_hard_clause() {
    nia::ls_solver solver(23,0,true);load(solver,gram_formula(false,true));nia::State state(solver);
    const nia::Assignment seed{21,5,22225};state.reset(seed);
    check(state.unsat.empty(),"extra nonlinear hard clause is true at the own incumbent");
    const auto before=snapshot(state);bool found=false;
    for(const auto& move:proposals(state,{{1,0}})) {
        const auto raw=applied(seed,move);if(raw[0]!=25||raw[1]!=0)continue;
        found=true;const auto evaluation=state.evaluate(move);
        check(evaluation.violations>0&&state.better(evaluation.objective,state.objective),
              "State rejects a witness repair that improves cost but breaks another hard clause");
        bool rejected=false;
        try{nia::validate(solver,applied(seed,evaluation.move));}
        catch(const std::runtime_error&){rejected=true;}
        check(rejected,"full original-formula validator independently rejects the other broken hard clause");
    }
    check(found,"pure candidate helper may propose a repair without claiming full feasibility");
    check(snapshot(state)==before,"rejected hard-clause previews preserve State, caches and RNG");
}
void compensation_can_cancel_the_objective_improvement() {
    Formula formula;formula.bound(0,0,2);formula.bound(1,0,3);
    formula.clauses.push_back({formula.polynomial('L',2,{{-1,{{0,1}}},{-1,{{1,1}}}})});
    formula.clauses.push_back({formula.polynomial('E',0,
        {{1,{{0,1}}},{1,{{1,1}}},{-1,{{2,1}}}})});
    nia::ls_solver solver(23,0,true);load(solver,formula);nia::State state(solver);
    const nia::Assignment seed{2,0,2};state.reset(seed);const auto before=snapshot(state);
    check(state.unsat.empty(),"objective cancellation fixture starts feasible");
    const auto raw=state.evaluate({{0,0}});
    check(raw.violations>0&&raw.objective==0,"raw primary alone appears to improve its correctly projected cost");
    bool found=false;
    for(const auto& move:proposals(state,{{0,0}})) {
        const auto candidate=applied(seed,move);if(candidate[0]!=0||candidate[1]!=2)continue;
        found=true;const auto evaluation=state.evaluate(move);
        check(!evaluation.violations&&evaluation.objective==state.objective&&
              !state.better(evaluation.objective,state.objective),
              "State's true objective rejects feasible compensation that cancels the primary improvement");
        check(nia::validate(solver,applied(seed,evaluation.move))==state.objective,
              "full original formula confirms feasibility without granting a strict objective improvement");
    }
    check(found,"slack helper proposes the objective-canceling raw compensation without freezing cost");
    check(snapshot(state)==before,"objective-rejected previews preserve source State and RNG");
}
}
int main(int argc,char** argv) {
    gram_state(false);gram_state(true);
    if(argc<2||std::string(argv[1])!="--gram-only") {
        full_formula_rejects_another_broken_hard_clause();
        compensation_can_cancel_the_objective_improvement();
    }
    if(failures)return 1;
    std::cout<<"slack repair real State integration checks passed\n";
}
