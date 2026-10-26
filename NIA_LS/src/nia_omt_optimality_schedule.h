#pragma once
#include <algorithm>
#include <cmath>
#include <limits>
namespace nia {
class OmtOptimalitySchedule {
    bool enabled_, has_incumbent_ = false, checked_ = false, productive_ = false;
    double stall_, cap_, floor_, next_, last_ = 0;
    double due_ = std::numeric_limits<double>::infinity();
public:
    OmtOptimalitySchedule(bool enabled,double stall,double initial,double total)
        : enabled_(enabled),stall_(stall),cap_(total),
          floor_(std::min({1.0,initial,total})),next_(std::min(initial,total)) {}
    void incumbent(double now) {
        if(!enabled_)return;
        has_incumbent_=true;
        if(checked_&&!productive_){
            next_=std::max(floor_,last_/2);
            productive_=true;
        }
        due_=now+stall_;
    }
    bool ready(double now,double remaining) const {
        return enabled_&&has_incumbent_&&remaining>0&&now>=due_;
    }
    double next_timeout() const { return next_; }
    double begin(double remaining) {
        last_=std::min(next_,remaining);
        return last_;
    }
    void finished(double now) {
        checked_=true;productive_=false;
        // Clamp before multiplication to avoid overflow after many retries.
        next_=last_>=cap_/2?cap_:std::max(floor_,last_*2);
        due_=now+stall_;
    }
};
}
