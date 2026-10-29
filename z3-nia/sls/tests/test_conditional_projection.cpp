// Exercise the actual internal projection transaction against native formulas.
// Including the translation unit keeps the heuristic out of the public API.
#include "../src/nia_omt_optimize.cpp"
#include <cstdlib>
namespace {
void check(bool ok,const char* why){if(!ok){std::cerr<<"FAIL: "<<why<<'\n';std::exit(1);}}
std::string input(const std::string& literals,const std::string& clauses){
 return "NIA_LS_OMT 1\nSTATUS ready\nSOURCE -\nORIGINAL_OBJECTIVE 79\nDIRECTION min\nVARIABLES 3\n0 I 79\n1 I 78\n2 B 62\nLITERALS "+literals+"\nCLAUSES "+clauses+"\nOBJECTIVE 0 1\n1 1 0 1\nEND\n";
}
void root_case(bool positive_guard,bool active,__int128_t moved,bool soluble){
 nia::ls_solver s(0,UINT64_MAX,true);std::string error;
 std::istringstream in(input("2\n1 B 2\n2 E -6 2\n1 2 0 1 1 1\n2 1 1 1",positive_guard?"1\n2 1 2":"1\n2 -1 2"));
 check(s.load_omt_instance(in,error),"conditional native fixture loads");
 nia::State state(s);state.reset({0,0,(__int128_t)(positive_guard?!active:active)?1:-1});
 const auto original=state.x;
 auto projected=state.project({{0,moved}});
 auto e=state.evaluate(projected,false);
 check(state.x==original,"projection and preview restore scratch assignment");
 state.apply(e);check(state.x[0]==moved,"requested variable stays fixed");
 if(!active){check(state.x[1]==0,"inactive guard leaves dependent variable alone");check(state.unsat.empty(),"inactive equality remains optional");}
 else if(soluble){check(state.x[1]==2,"active bilinear equality solves dependent root exactly");check(state.unsat.empty(),"projected candidate satisfies actual native clause");}
 else check(!state.unsat.empty(),"indivisible or zero-coefficient equation is not called feasible");
}
void constant_ite_batch(char guard_kind,bool protect_aux=false){
 const size_t count=24;std::ostringstream text;
 text<<"NIA_LS_OMT 1\nSTATUS ready\nSOURCE -\nORIGINAL_OBJECTIVE 78\nDIRECTION min\nVARIABLES "<<count+2<<"\n0 I 78\n1 "<<(guard_kind=='B'?"B":"I")<<" 79\n";
 for(size_t i=0;i<count;++i){const std::string name="aux"+std::to_string(i);text<<i+2<<" I ";for(unsigned char c:name)text<<"0123456789abcdef"[c>>4]<<"0123456789abcdef"[c&15];text<<'\n';}
 text<<"LITERALS "<<2*count+1<<"\n";
 if(guard_kind=='B')text<<"1 B 1\n";
 else text<<"1 "<<guard_kind<<" 0 2\n1 1 0 1\n-1 1 1 1\n";
 const __int128_t big=__int128_t(1)<<80;
 for(size_t i=0;i<count;++i){const __int128_t value=i==0?big:__int128_t(i+1);
  text<<2*i+2<<" E "<<nia::format_omt_integer(-value)<<" 1\n1 1 "<<i+2<<" 1\n";
  text<<2*i+3<<" E 0 1\n1 1 "<<i+2<<" 1\n";
 }
 text<<"CLAUSES "<<2*count<<'\n';
 for(size_t i=0;i<count;++i)text<<"2 -1 "<<2*i+2<<"\n2 1 "<<2*i+3<<'\n';
 text<<"OBJECTIVE 0 1\n1 1 0 1\nEND\n";
 nia::ls_solver solver(0,UINT64_MAX,true);std::string error;std::istringstream in(text.str());
 check(solver.load_omt_instance(in,error),"complete constant ITE fixture loads");
 nia::State state(solver);std::vector<__int128_t> seed(count+2,1);
 for(size_t i=0;i<count;++i)seed[i+2]=i==0?big:__int128_t(i+1);
 state.reset(seed);check(state.unsat.empty(),"constant ITE fixture has an exact own feasible seed");
 nia::Move move=guard_kind=='B'?nia::Move{{1,-1}}:nia::Move{{0,guard_kind=='E'?0:2}};
 if(protect_aux)move.push_back({2,big});
 auto candidate=state.evaluate(move);check(state.x==seed,"constant conditional preview restores all scratch variables");state.apply(candidate);
 if(protect_aux){check(state.x[2]==big&&!state.unsat.empty(),"an explicitly requested auxiliary is protected and a contradictory candidate stays infeasible");return;}
 check(state.unsat.empty(),"complete arithmetic and Boolean ITE definitions update beyond the old sixteen-write limit");
 for(size_t i=0;i<count;++i)check(state.x[i+2]==0,"selected false ITE branch is computed with exact integers");
}
void constant_ite_chain(){
 const std::string source="NIA_LS_OMT 1\nSTATUS ready\nSOURCE -\nORIGINAL_OBJECTIVE 78\nDIRECTION min\nVARIABLES 3\n0 I 78\n1 I 61\n2 I 7a\nLITERALS 6\n1 L 0 1\n1 1 0 1\n2 E 0 1\n1 1 1 1\n3 E -1 1\n1 1 1 1\n4 E 0 1\n1 1 1 1\n5 E 0 1\n1 1 2 1\n6 E -5 1\n1 1 2 1\nCLAUSES 4\n2 -1 2\n2 1 3\n2 -4 5\n2 4 6\nOBJECTIVE 0 1\n1 1 0 1\nEND\n";
 nia::ls_solver solver(0,UINT64_MAX,true);std::string error;std::istringstream in(source);
 check(solver.load_omt_instance(in,error),"conditional dependency chain loads");nia::State state(solver);state.reset({0,0,0});const auto own=state.x;
 auto candidate=state.evaluate({{0,1}});check(state.x==own,"conditional chain scratch transaction restores its inputs");state.apply(candidate);
 check(state.x==std::vector<__int128_t>({1,1,5})&&state.unsat.empty(),"an arithmetic guard reads the freshly updated upstream conditional");
}
}
int main(){
 root_case(false,true,1,true);root_case(true,true,1,true);
 root_case(false,false,1,false);root_case(true,false,1,false);
 root_case(false,true,2,false);root_case(false,true,-2,false);
 {
  nia::ls_solver solver(0,UINT64_MAX,true);std::string error;
  std::istringstream in(input("2\n1 B 2\n2 E -6 2\n1 2 0 1 1 1\n2 1 1 1","1\n2 1 2"));
  check(solver.load_omt_instance(in,error),"guard-only fixture loads");
  nia::State state(solver);state.reset({1,0,1});auto before=state.x;
  auto e=state.evaluate(state.project({{2,-1}}),false);check(state.x==before,"guard-only preview restores state");state.apply(e);
  check(state.x[2]==-1&&state.x[1]==2&&state.unsat.empty(),"Boolean-only move activates equality projection");
 }
 {
  nia::ls_solver solver(0,UINT64_MAX,true);std::string error;
  std::istringstream in("NIA_LS_OMT 1\nSTATUS ready\nSOURCE -\nORIGINAL_OBJECTIVE 78\nDIRECTION min\nVARIABLES 4\n0 I 78\n1 I 79\n2 I 7a\n3 B 62\nLITERALS 3\n1 B 3\n2 E -1 2\n1 1 2 1\n-1 1 1 1\n3 E -1 2\n1 1 1 1\n-1 1 0 1\nCLAUSES 2\n2 -1 2\n2 -1 3\nOBJECTIVE 0 1\n1 1 0 1\nEND\n");
  check(solver.load_omt_instance(in,error),"reverse chain fixture loads");nia::State state(solver);state.reset({0,0,0,1});
  auto e=state.evaluate(state.project({{0,5}}),false);state.apply(e);
  check(state.x[0]==5&&state.x[1]==6&&state.x[2]==7&&state.unsat.empty(),"reverse ordered guarded chain propagates across passes");
 }
 constant_ite_batch('E');constant_ite_batch('L');constant_ite_batch('B');constant_ite_batch('E',true);constant_ite_chain();
 std::cout<<"conditional projection checks passed\n";
}
