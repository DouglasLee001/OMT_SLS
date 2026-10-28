#pragma once
#include <algorithm>
#include <cmath>
#include <limits>
#include <string>
namespace nia {
class OmtOptimalitySchedule {
    bool enabled_, has_incumbent_ = false, checked_ = false, productive_ = false;
    double stall_, cap_, floor_, initial_, window_, next_, last_ = 0;
    bool sat_bound_ = false, long_check_ = false, persistent_, balanced_;
    double due_ = std::numeric_limits<double>::infinity();
public:
    OmtOptimalitySchedule(bool enabled,double stall,double initial,double total,double search_window=0,bool persistent=false,bool balanced=false)
        : enabled_(enabled),stall_(stall),cap_(total),
          floor_(std::min({1.0,initial,total})),initial_(std::min(initial,total)),
          window_(search_window),next_(initial_),persistent_(persistent),balanced_(balanced) {}
    void incumbent(double now) {
        if(!enabled_)return;
        has_incumbent_=true;
        sat_bound_=false;
        if(checked_&&!productive_){
            next_=std::max(floor_,last_/2);
            if(window_>0)next_=std::min(initial_,next_);
            long_check_=false;
            productive_=true;
        }
        due_=now+stall_;
    }
    bool ready(double now,double remaining) const {
        return enabled_&&has_incumbent_&&!sat_bound_&&remaining>0&&now>=due_;
    }
    double next_timeout() const { return next_; }
    double begin(double remaining) {
        // Keep a small tail for the final record; the outer case deadline is unchanged.
        last_=std::min(next_,long_check_?remaining*.98:remaining);
        return last_;
    }
    void finished(double now,const std::string& status="") {
        checked_=true;productive_=false;
        if((window_>0||persistent_)&&status=="sat"){
            // SAT already refutes optimality at this incumbent. Retrying the
            // same query cannot prove UNSAT, even when its model cannot import.
            sat_bound_=true;
        }
        if(window_>0&&(status=="budget"||status=="unknown")){
            next_=cap_;long_check_=true;due_=now+window_;
            return;
        }
        long_check_=false;
        // Clamp before multiplication to avoid overflow after many retries.
        next_=last_>=cap_/2?cap_:std::max(floor_,last_*2);
        // An inconclusive proof yields an SLS window at least as long as its
        // next proof slice. Improvements still reset the ordinary stall clock.
        due_=now+(balanced_&&(status=="budget"||status=="unknown")?std::max(stall_,next_):stall_);
    }
};
}
