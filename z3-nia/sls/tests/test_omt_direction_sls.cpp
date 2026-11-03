#include "../src/nia_omt_direction_sls.h"
#include <iostream>
#include <stdexcept>
#include <string>

using namespace nia::direction_sls;
namespace {
int failures = 0;
void check(bool ok, const std::string& what) {
    if (!ok) { ++failures; std::cerr << "FAIL: " << what << '\n'; }
}
Row row(std::initializer_list<std::pair<size_t, mpz_class>> terms,
        mpz_class rhs = 0, bool equality = false) {
    return Row{terms, std::move(rhs), equality};
}
bool valid(const std::vector<Row>& rows, const Result& result, size_t n) {
    if (!result.found || result.direction.size() != n) return false;
    for (const auto& r : rows) {
        mpz_class lhs = 0;
        for (const auto& t : r.terms) lhs += t.second * result.direction[t.first];
        if (r.equality ? lhs != r.rhs : lhs > r.rhs) return false;
    }
    return true;
}
Result run(const std::vector<Row>& rows, size_t n) {
    Options options; options.seconds = .25; options.seed = 7;
    return solve(rows, n, options);
}
void exact_direction_examples() {
    std::vector<Row> one{row({{0,-1000}},-1)};
    check(valid(one, run(one,1),1), "find a strict improving integer direction");
    std::vector<Row> coupled{row({{0,1},{1,-1000}},0,true),row({{0,-1}},-1),row({{1,-1}})};
    auto result = run(coupled,2);
    check(valid(coupled,result,2), "joint direction preserves coupled equality");
    mpz_class large("100000000000000000000000000000000000000000000000000000000000000000000000000000003");
    std::vector<Row> wide{row({{0,1},{1,-large}},0,true),row({{1,1},{2,-7}},0,true),row({{2,-1}},-1)};
    result=run(wide,3);
    check(valid(wide,result,3), "arbitrary precision coupled direction");
    check(result.direction.size()==3 && result.direction[0]>mpz_class("170141183460469231731687303715884105727"), "direction exceeds int128 without truncation");
    std::vector<Row> opposite{row({{0,2},{1,-3}}),row({{0,-2},{1,3}}),row({{0,-1}},-1)};
    check(valid(opposite,run(opposite,2),2), "opposite inequalities permit joint rational-ratio direction");
    std::vector<Row> cycle{row({{0,1},{1,-1}}),row({{1,1},{2,-1}}),row({{2,1},{0,-1}}),row({{0,-1}},-1)};
    check(valid(cycle,run(cycle,3),3), "coupled inequality cycle preserves a common improving direction");
    // The opposite inequalities become visible only after substituting an
    // existing equality. Their integer ratio requires a joint move rather
    // than a long succession of single-coordinate boundary repairs.
    mpz_class a("100000000000000000000003"), b("100000000000000000000033");
    std::vector<Row> hidden{row({{0,-1},{2,1}},0,true),
        row({{2,a},{1,-b}}),row({{0,-a},{1,b}}),row({{0,-1}},-1)};
    check(valid(hidden,run(hidden,3),3), "equality substitution exposes an implied joint rational-ratio move");
    mpz_class c("100000000000000000000039");
    std::vector<Row> hidden_chain{row({{0,-1},{3,1}},0,true),
        row({{3,a},{1,-b}}),row({{0,-a},{1,b}}),
        row({{1,b},{2,-c}}),row({{0,-a},{2,c}}),row({{0,-1}},-1)};
    check(valid(hidden_chain,run(hidden_chain,4),4), "implied equalities expose further coupled moves to a fixed point");
    std::vector<Row> negative{row({{0,1}}),row({{1,-1}}),row({{0,1},{1,1}},-1)};
    check(valid(negative,run(negative,2),2), "direction can decrease a signed coordinate");
    std::vector<Row> duplicate{row({{0,7},{0,-6},{1,-2}},0,true),row({{0,-1}},-1)};
    check(valid(duplicate,run(duplicate,2),2), "duplicate row variables are combined exactly");
}
void conservative_failures() {
    std::vector<Row> blocked{row({{0,1}}),row({{0,-1}},-1)};
    auto result=run(blocked,1);
    check(!result.found, "inconsistent cone never returns SAT");
    check(result.row_pressure.size()==blocked.size(), "failed search retains pressure for each original row");
    uint64_t pressure=0;for(auto count:result.row_pressure)pressure+=count;
    check(pressure>0, "failed search reports actual violated-row pressure");
    std::vector<Row> empty{row({},-1)};
    check(!run(empty,0).found, "constant inconsistent strict objective has no direction");
    std::vector<Row> onlyzero{row({{0,1}},0,true),row({{0,-1}},-1)};
    check(!run(onlyzero,1).found, "zero nullspace cannot supply strict improvement");
    Options options; options.seconds=0;
    result=solve(blocked,1,options);
    check(!result.found&&result.expired, "zero budget never starts direction search");
    options.seconds=1;size_t callbacks=0;options.checkpoint=[&](){++callbacks;};
    solve({row({{0,-1}},-1)},1,options);
    check(callbacks>0,"external deadline checkpoint is called");
}
}
int main(){
    exact_direction_examples();conservative_failures();
    if(failures){std::cerr<<failures<<" direction SLS checks failed\n";return 1;}
    std::cout<<"exact direction local-search checks passed\n";
}
