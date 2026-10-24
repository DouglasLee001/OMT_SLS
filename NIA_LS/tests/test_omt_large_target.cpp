#include "nia_omt_large_target.h"
#include <iostream>
#include <limits>
#include <stdexcept>

void require(bool condition,const char* message) {
    if(!condition)throw std::runtime_error(message);
}
int main() {
    using nia::OmtLargeTargetSchedule;
    OmtLargeTargetSchedule min("10000000000","min",1200);
    require(min.goal().get_str()=="-10000000000","min signed goal");
    require(!min.ready(0,1200),"no attempt without a feasible seed");
    min.accepted(0);min.accepted(-1);min.accepted(-2);min.accepted(-3);
    require(!min.ready(0,1200),"small improvements do not trigger");
    min.accepted(-100);
    require(min.ready(0,1200),"sustained significant improvements trigger");
    require(min.slice_seconds(1200)==1,"slice cap is one second");
    min.charge(1,2);
    require(!min.ready(6.9,1190)&&min.ready(7,1190),"five seconds between slices");
    min.charge(29,36);
    require(!min.ready(100,1000)&&min.remaining_budget()==0,"total probe budget capped at thirty seconds");
    OmtLargeTargetSchedule max("10000000000","max",10);
    max.accepted(-1000);max.accepted(-990);max.accepted(-980);max.accepted(-970);
    require(!max.ready(0,10),"relative improvement gate");
    max.accepted(-900);
    require(max.ready(0,10)&&max.slice_seconds(10)==.5,"five percent budget for short runs");
    require(max.goal().get_str()=="10000000000","max signed goal");
    max.accepted(mpz_class("10000000000"));
    require(max.reached()&&!max.ready(0,10),"threshold is inclusive and skips probing");
    OmtLargeTargetSchedule already("10000000000","min",1200);
    already.accepted(mpz_class("-10000000001"));
    require(already.reached()&&!already.ready(0,1200),"first feasible model may already reach goal");
    const mpz_class boundary("170141183460469231731687303715884105727");
    OmtLargeTargetSchedule boundary_min(boundary.get_str(),"min",1200);
    OmtLargeTargetSchedule boundary_max(boundary.get_str(),"max",1200);
    require(boundary_min.goal()==-boundary&&boundary_max.goal()==boundary,
            "signed128 maximum magnitude is preserved exactly in both directions");
    boundary_min.accepted(mpz_class(-boundary+1));boundary_max.accepted(mpz_class(boundary-1));
    require(!boundary_min.reached()&&!boundary_max.reached(),"one below boundary is not reached");
    boundary_min.accepted(-boundary);boundary_max.accepted(boundary);
    require(boundary_min.reached()&&boundary_max.reached(),"boundary goal includes equality");
    bool rejected=false;
    try {OmtLargeTargetSchedule too_large(mpz_class(boundary+1).get_str(),"min",1200);}
    catch(const std::invalid_argument&){rejected=true;}
    require(rejected,"magnitude 2^127 is rejected instead of using a special path");
    OmtLargeTargetSchedule disabled("","min",1200);
    disabled.accepted(-1000000);
    require(!disabled.enabled()&&!disabled.reached()&&!disabled.ready(100,1200),"default disabled");
    require(!max.ready(0,0)&&!max.ready(0,std::numeric_limits<double>::quiet_NaN()),"expired/NaN global budget");
    std::cout<<"large target scheduling checks passed\n";
}
