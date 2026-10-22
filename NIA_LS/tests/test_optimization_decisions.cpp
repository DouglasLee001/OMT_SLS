// Logical decision coordinates and exact derived-variable propagation.
#include "../src/nia_omt_optimize.cpp"
#include <cstdlib>

namespace {
void check(bool condition,const char* reason){if(!condition){std::cerr<<"FAIL: "<<reason<<'\n';std::exit(1);}}
struct Formula {
 std::vector<char> sorts;std::vector<std::string> literals;std::vector<std::vector<int>> clauses;
 size_t variable(char sort='I'){sorts.push_back(sort);return sorts.size()-1;}
 int linear(char kind,long long constant,std::initializer_list<std::pair<size_t,long long>> terms){
  std::ostringstream out;out<<kind<<' '<<constant<<' '<<terms.size()<<'\n';
  for(auto term:terms)out<<term.second<<" 1 "<<term.first<<" 1\n";
  literals.push_back(out.str());return literals.size();
 }
 int boolean(size_t v){literals.push_back("B "+std::to_string(v)+"\n");return literals.size();}
 void conditional(size_t target,int guard,long long yes,long long no){
  const int when_true=linear('E',-yes,{{target,1}}),when_false=linear('E',-no,{{target,1}});
  clauses.push_back({-guard,when_true});clauses.push_back({guard,when_false});
 }
 void bound(size_t v,long long lo,long long hi){clauses.push_back({linear('L',lo,{{v,-1}})});clauses.push_back({linear('L',-hi,{{v,1}})});}
 std::string text(size_t objective)const{
  std::ostringstream out;out<<"NIA_LS_OMT 1\nSTATUS ready\nSOURCE -\nORIGINAL_OBJECTIVE 78\nDIRECTION min\nVARIABLES "<<sorts.size()<<'\n';
  for(size_t v=0;v<sorts.size();++v){std::string name="v"+std::to_string(v);out<<v<<' '<<sorts[v]<<' ';for(unsigned char c:name)out<<"0123456789abcdef"[c>>4]<<"0123456789abcdef"[c&15];out<<'\n';}
  out<<"LITERALS "<<literals.size()<<'\n';for(size_t i=0;i<literals.size();++i)out<<i+1<<' '<<literals[i];
  out<<"CLAUSES "<<clauses.size()<<'\n';for(const auto& clause:clauses){out<<clause.size();for(int id:clause)out<<' '<<id;out<<'\n';}
  out<<"OBJECTIVE 0 1\n1 1 "<<objective<<" 1\nEND\n";return out.str();
 }
};
void load(nia::ls_solver& solver,const Formula& formula,size_t objective){std::string error;std::istringstream input(formula.text(objective));check(solver.load_omt_instance(input,error),"decision fixture imports");}
void neutral_search_ignores_derived_work(){
 Formula formula;for(size_t i=0;i<64;++i)formula.variable();const size_t r=formula.variable(),p=formula.variable(),q=formula.variable();
 const int guard=formula.linear('L',0,{{p,1}});for(size_t i=0;i<64;++i)formula.conditional(i,guard,0,1);
 formula.bound(r,1,3);formula.bound(p,0,2);formula.bound(q,0,2);
 formula.clauses.push_back({formula.linear('L',1,{{p,1},{r,-1}})});formula.clauses.push_back({formula.linear('L',1,{{q,1},{r,-1}})});
 nia::ls_solver solver(1,0,true);load(solver,formula,r);nia::State state(solver);
 nia::Assignment own(67,1);own[r]=3;own[p]=own[q]=2;state.reset(own);check(state.unsat.empty(),"sparse-core plateau starts feasible");
 const auto field=state.plateau_field();const auto before=state.x;const nia::Int old_pressure=state.pressure(field);
 unsigned calls=0;nia::State::Evaluation selected;
 check(state.neutral_step(field,selected,[&](){return ++calls<=100;}),"neutral work budget reaches core coordinates instead of enumerating 64 computed auxiliaries");
 check(state.x==before&&selected.violations==0&&selected.objective==3,"neutral decision selection is feasible and transactional");
 check(state.pressure(field,&selected)<old_pressure,"core neutral move strictly lowers the fixed blocking pressure");
 state.apply(selected);check(nia::validate(solver,state.x)==3,"core neutral move and derived auxiliaries pass full exact validation");
}
void sparse_ids_and_rng(){
 Formula formula;for(size_t i=0;i<8;++i)formula.variable();const int guard=formula.linear('L',0,{{7,1}});
 for(size_t v:{size_t(0),size_t(1),size_t(2),size_t(3),size_t(4),size_t(6)})formula.conditional(v,guard,0,1);
 nia::ls_solver solver(1,0,true);load(solver,formula,5);nia::State state(solver);
 check(state.search_variables()==std::vector<size_t>({5,7}),"primary coordinates preserve sparse native variable IDs");
 check(state.search_inputs({0,1,6,7})==std::vector<size_t>({7}),"auxiliary repair expands to its actual guard inputs once");
 for(unsigned seed=0;seed<64;++seed){
  std::mt19937 rng(seed),expected(seed);const size_t index=expected()%2;
  check(state.repair_variables({},rng)==std::vector<size_t>({state.search_variables()[index]})&&rng==expected,"empty repair draws once and selects a native ID rather than a vector offset");
  const auto saved=rng;check(state.repair_variables({1,5,7,1},rng)==std::vector<size_t>({5,7})&&rng==saved,"a nonempty expanded repair set does not consume random state");
 }
}
void nested_boolean_objective(){
 Formula formula;const size_t input=formula.variable('B'),first=formula.variable(),second=formula.variable(),free=formula.variable();
 formula.conditional(first,formula.boolean(input),2,0);formula.conditional(second,formula.linear('L',-1,{{first,1}}),3,4);
 nia::ls_solver solver(1,0,true);load(solver,formula,second);nia::State state(solver);state.reset({1,2,4,9});
 check(state.search_variables()==std::vector<size_t>({input,free})&&state.search_inputs({second})==std::vector<size_t>({input}),"nested conditional inputs expand through the DAG to a Boolean decision");
 check(state.objective_search_variables==std::set<size_t>({input}),"a computed objective prioritizes its Boolean input without changing its expression");
 check(state.defined_variables.count(first)&&state.defined_variables.count(second),"spread and accepted-ray construction recognize compiled auxiliary definitions");
 const auto before=state.x;auto candidate=state.evaluate({{input,-1}});
 check(state.x==before&&candidate.violations==0&&candidate.objective==3,"Boolean decision preview computes a strict feasible objective improvement through both definitions");
 state.apply(candidate);check(nia::validate(solver,state.x)==3,"computed-objective improvement passes independent full validation");
}
void empty_and_cyclic_definitions(){
 {
  Formula formula;const size_t aux=formula.variable();formula.conditional(aux,formula.linear('L',-1,{}),7,9);
  nia::ls_solver solver(1,0,true);load(solver,formula,aux);nia::State state(solver);state.reset({7});std::mt19937 rng(8),saved=rng;
  check(state.search_variables().empty()&&state.search_inputs({aux}).empty(),"a fully constant formula has an empty decision set");
  check(state.repair_variables({},rng).empty()&&rng==saved,"an empty decision set returns without division or random draws");
  check(state.unsat.empty()&&nia::validate(solver,state.x)==7,"empty primary coordinates do not remove any hard constraint or objective value");
 }
 {
  Formula formula;formula.variable();formula.variable();formula.variable();
  formula.conditional(0,formula.linear('E',0,{{1,1}}),0,1);formula.conditional(1,formula.linear('E',0,{{0,1}}),0,1);
  nia::ls_solver solver(1,0,true);load(solver,formula,2);nia::State state(solver);
  check(state.search_variables()==std::vector<size_t>({0,1,2})&&state.search_inputs({0,1})==std::vector<size_t>({0,1}),"uncompiled cyclic targets remain genuine search coordinates");
 }
}
void no_definition_compatibility(){
 Formula formula;formula.variable();formula.variable('B');formula.variable();formula.bound(0,-2,2);
 nia::ls_solver solver(1,0,true);load(solver,formula,0);nia::State state(solver);
 check(state.search_variables()==std::vector<size_t>({0,1,2}),"without complete conditional definitions the original variable order is unchanged");
 check(state.objective_search_variables==state.objective_variables,"without definitions objective priorities stay unchanged");
 std::mt19937 rng(17),expected(17);const auto index=expected()%3;
 check(state.repair_variables({},rng)==std::vector<size_t>({index})&&rng==expected,"without definitions an empty scan uses the original single-draw fallback");
 expected=rng;check(state.repair_variables({2,0,2},rng)==std::vector<size_t>({0,2})&&rng==expected,"without definitions a repair scan keeps its old sorted unique coordinates and RNG");
}
}
int main(){neutral_search_ignores_derived_work();sparse_ids_and_rng();nested_boolean_objective();empty_and_cyclic_definitions();no_definition_compatibility();std::cout<<"optimization decision-coordinate checks passed\n";}
