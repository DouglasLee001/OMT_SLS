#pragma once
#include <algorithm>
#include <cmath>
#include <gmpxx.h>
#include <stdexcept>
#include <string>

namespace nia {
// Scheduling only: progress comes from independently validated incumbents.
// Reaching this finite threshold does not prove an infinite objective.
class OmtLargeTargetSchedule {
    bool enabled_=false,minimize_=true,has_first_=false,reached_=false;
    mpz_class magnitude_=0,goal_=0,first_=0,best_=0;
    unsigned improvements_=0;
    double allowance_=0,used_=0,next_=0;
public:
    OmtLargeTargetSchedule(const std::string& magnitude,const std::string& direction,double timeout)
        :enabled_(!magnitude.empty()),minimize_(direction=="min") {
        if(!enabled_)return;
        if((direction!="min"&&direction!="max")||magnitude.find_first_not_of("0123456789")!=std::string::npos)
            throw std::invalid_argument("invalid finite large target");
        magnitude_=mpz_class(magnitude,10);
        if(magnitude_<=0||magnitude_>mpz_class("170141183460469231731687303715884105727"))
            throw std::invalid_argument("finite large target must be positive and at most 2^127-1");
        goal_=minimize_?-magnitude_:magnitude_;
        allowance_=std::min(30.0,timeout*.05);
    }
    bool enabled()const{return enabled_;}
    bool reached()const{return reached_;}
    const mpz_class& goal()const{return goal_;}
    const mpz_class& magnitude()const{return magnitude_;}
    void accepted(const mpz_class& value) {
        if(!enabled_)return;
        if(!has_first_){first_=best_=value;has_first_=true;}
        else if(minimize_?value<best_:value>best_){best_=value;++improvements_;}
        reached_=minimize_?best_<=goal_:best_>=goal_;
    }
    double remaining_budget()const{return std::max(0.0,allowance_-used_);}
    bool ready(double elapsed,double remaining)const {
        if(!enabled_||!has_first_||reached_||improvements_<3||!(remaining>0)
           ||!(remaining_budget()>0)||!(elapsed>=next_))return false;
        const mpz_class relative=(abs(first_)+9)/10;
        const mpz_class required=relative>100?relative:mpz_class(100);
        return abs(best_-first_)>=required;
    }
    double slice_seconds(double remaining)const {
        if(!(remaining>0))return 0;
        return std::min(1.0,std::min(remaining,remaining_budget()));
    }
    void charge(double seconds,double now) {
        if(seconds>0)used_+=seconds;
        next_=now+5;
    }
};
}
