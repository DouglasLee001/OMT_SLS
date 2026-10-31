#include "../src/nia_omt_optimality_schedule.h"
#include <array>
#include <cassert>
#include <cmath>
#include <iostream>
#include <limits>
#include <random>
#include <string>

// Frozen pre-feature policy reference, schedule SHA256:
// 4b78c677f2eaca4262d18355b7ca6fbc9f640d6eb2f178d264fa1c3b953a01f4
namespace nia {
class LegacyOptimalitySchedule {
    bool enabled_, has_incumbent_ = false, checked_ = false, productive_ = false;
    double stall_, cap_, floor_, initial_, window_, next_, last_ = 0;
    bool sat_bound_ = false, long_check_ = false, persistent_, balanced_;
    double due_ = std::numeric_limits<double>::infinity();
public:
    LegacyOptimalitySchedule(bool enabled,double stall,double initial,double total,double search_window=0,bool persistent=false,bool balanced=false)
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
using Candidate = nia::OmtOptimalitySchedule;

Candidate late_pause(double pause=1120, const std::string& status="budget") {
    Candidate s(true,10,32,1200,0,true,true,true);
    s.incumbent(900);
    assert(s.ready(910,290));
    assert(s.begin(290)==32);
    s.finished(942,"budget",258);
    assert(s.ready(pause-64,1200-(pause-64)));
    assert(s.begin(1200-(pause-64))==64);
    s.finished(pause-63,"sat",1200-(pause-63));
    s.incumbent(pause-63);
    s.finished(pause-30,"sat",1200-(pause-30));
    s.incumbent(pause-30);
    s.finished(pause,status,1200-pause);
    assert(s.next_timeout()==128);
    return s;
}

void test_early_windows_and_first_feasible() {
    Candidate s(true,10,30,1200,0,true,true,true);
    assert(!s.ready(1199,1)); // No policy proof before the first feasible incumbent.
    s.incumbent(100);
    assert(!s.ready(109.999,1090.001));
    assert(s.ready(110,1090));
    assert(s.begin(1090)==30);
    s.finished(140,"budget",1060);
    assert(s.next_timeout()==60);
    assert(!s.ready(199.999,1000.001));
    assert(s.ready(200,1000)); // A full ordinary balanced window is untouched.

    Candidate first_late(true,10,30,1200,0,true,true,true);
    first_late.incumbent(1195);
    assert(!first_late.ready(1199,1)); // Feature never bypasses a first-incumbent stall.
}

void test_improvement_overrides_reservation() {
    auto s=late_pause(); // Would otherwise resume at 1160.
    for(double now: {1155.,1164.,1173.,1182.,1191.}) {
        s.incumbent(now);
        assert(s.next_timeout()==32); // Halve outer64 once; never once per event.
        assert(!s.ready(now+9,1200-(now+9)));
    }
    assert(!s.ready(1199,1)); // Continuous improvements can consume the proof reservation.
    auto once=late_pause();
    once.incumbent(1155);
    assert(!once.ready(1164.999,35.001));
    assert(once.ready(1165,35)); // Last improvement must receive the entire normal stall.
    assert(once.begin(35)==32);
}

void test_same_bound_sat_and_slice_accounting() {
    auto s=late_pause();
    assert(s.next_timeout()==128); // Inner30 is deliberately not charged as outer last_.
    assert(s.ready(1160,40));
    assert(s.begin(40)==40); // Remaining time still limits the real allowance.
    s.finished(1161,"sat",39);
    assert(!s.ready(1199,1)); // Failed SAT import must not permit same-bound UNSAT retry.
    s.incumbent(1162);
    assert(!s.ready(1171.999,28.001));
    assert(s.ready(1172,28));
    assert(s.next_timeout()==20);
}

void test_wait_telemetry_and_representability() {
    auto s=late_pause();
    assert(s.tail_caps()==1&&s.last_original_wait()==128&&s.last_wait()==40);
    assert(s.ready(1160,40));
    s.begin(40);s.finished(1160,"unknown",40);
    assert(s.tail_caps()==2&&s.last_original_wait()==80&&s.last_wait()==20);
    s.incumbent(1175);
    assert(!s.ready(1184.999,15.001));
    assert(s.ready(1185,15));
    s.begin(15);s.finished(1185,"sat",15);
    assert(s.tail_caps()==2&&s.last_original_wait()==10&&s.last_wait()==10);
    assert(!s.ready(1199,1));

    Candidate window(true,10,30,1200,250,true,true,true);
    window.incumbent(1000);window.begin(190);window.finished(1040,"budget",160);
    assert(window.tail_caps()==0&&window.last_original_wait()==250&&window.last_wait()==250);
    Candidate tiny_stall(true,1e-300,64,1200,0,true,true,true);
    tiny_stall.incumbent(0);tiny_stall.begin(1000);tiny_stall.finished(1000,"budget",200);
    assert(tiny_stall.tail_caps()==0&&tiny_stall.last_wait()==128); // now+stall==now is not representable.
    assert(!tiny_stall.ready(1100,100));
    const double maximum=std::numeric_limits<double>::max();
    Candidate overflowing_due(true,maximum*.6,maximum*.7,maximum,0,true,true,true);
    overflowing_due.incumbent(0);overflowing_due.begin(maximum);
    overflowing_due.finished(maximum*.6,"budget",maximum*.8);
    assert(overflowing_due.tail_caps()==0); // now+stall overflows.
    assert(overflowing_due.last_wait()==maximum);
    for(double now: {maximum,std::numeric_limits<double>::infinity(),std::numeric_limits<double>::quiet_NaN()}) {
        Candidate invalid_now(true,10,64,1200,0,true,true,true);
        invalid_now.incumbent(0);invalid_now.begin(1000);invalid_now.finished(now,"budget",80);
        assert(invalid_now.tail_caps()==0&&invalid_now.last_wait()==128);
    }
}

void test_short_tail_protects_stall() {
    auto s=late_pause(1185);
    assert(!s.ready(1194.999,5.001));
    assert(s.ready(1195,5)); // Full10 SLS,5 proof; cannot promise R/2 while keeping stall.
    assert(s.begin(5)==5);
    s.finished(1195,"unknown",5);
    assert(!s.ready(1199,1)); // No fractional-tail spin when remaining <= stall.
    assert(!s.ready(1200,0));
}

void test_immediate_unknown_cannot_zeno() {
    auto s=late_pause();
    double previous_pause=1120;
    unsigned resumes=0;
    for(double now: {1160.,1180.,1190.}) {
        assert(now-previous_pause>=10);
        assert(!s.ready(now-.001,1200-now+.001));
        assert(s.ready(now,1200-now));
        assert(s.begin(1200-now)==1200-now);
        // Adversarial worker returns unknown with zero consumed wall time.
        s.finished(now,"unknown",1200-now);
        previous_pause=now;
        ++resumes;
    }
    assert(resumes==3);
    assert(!s.ready(1199.999,.001));
    assert(!s.ready(1200,0));
}

void test_scoping_and_missing_clock() {
    auto s=late_pause(1120,"error");
    assert(!s.ready(1129.999,70.001));
    assert(s.ready(1130,70)); // Errors preserve the legacy stall, never the balanced cap.

    Candidate explicit_window(true,10,30,1200,250,true,true,true);
    explicit_window.incumbent(1000);
    explicit_window.begin(190);
    explicit_window.finished(1040,"budget",160);
    assert(!explicit_window.ready(1199,1)); // Explicit search window has precedence.

    Candidate unbalanced(true,10,30,1200,0,true,false,true);
    unbalanced.incumbent(1000);
    unbalanced.begin(190);
    unbalanced.finished(1040,"budget",160);
    assert(!unbalanced.ready(1049.999,150.001));
    assert(unbalanced.ready(1050,150));

    for(double remaining: {0.,-1.,std::numeric_limits<double>::infinity(),
                          std::numeric_limits<double>::quiet_NaN()}) {
        auto invalid=late_pause();
        invalid.finished(1120,"budget",remaining);
        assert(!invalid.ready(1199,1)); // Absent/invalid clock leaves the legacy wait.
    }
    auto omitted=late_pause();
    omitted.finished(1120,"budget");
    assert(!omitted.ready(1199,1));

    Candidate off(false,10,30,1200,0,true,true,true);
    off.incumbent(1000);off.begin(190);off.finished(1040,"budget",160);
    assert(!off.ready(1199,1));
}

void test_default_off_trace_parity() {
    std::mt19937 rng(20261030);
    const std::array<std::string,5> statuses={"budget","unknown","sat","error",""};
    for(bool balanced: {false,true}) for(bool persistent: {false,true})
    for(double window: {0.,250.}) {
        nia::LegacyOptimalitySchedule old(true,10,30,1200,window,persistent,balanced);
        Candidate candidate(true,10,30,1200,window,persistent,balanced); // Omitted flag OFF.
        double now=0;
        for(unsigned event=0;event<1000;++event) {
            now+=double(rng()%23);
            double remaining=std::max(0.,1200-now);
            if(rng()%3==0) {old.incumbent(now);candidate.incumbent(now);}
            else if(rng()%2==0) {
                assert(old.begin(remaining)==candidate.begin(remaining));
                const auto& status=statuses[rng()%statuses.size()];
                old.finished(now,status);candidate.finished(now,status,remaining);
            }
            assert(old.next_timeout()==candidate.next_timeout());
            for(double offset: {0.,9.,10.,60.,250.})
                assert(old.ready(now+offset,remaining)==candidate.ready(now+offset,remaining));
        }
    }
}

void test_reservation_and_budget_properties() {
    // All 4,800 synthetic states are generic, with no instance/objective information.
    for(const auto& status: {std::string("budget"),std::string("unknown")})
    for(double stall: {1.,10.,50.})
    for(double allowance: {1.,4.,30.,128.})
    for(unsigned tail=1;tail<=200;++tail) {
        const double remaining=double(tail);
        const double now=1200-remaining;
        Candidate s(true,stall,allowance,1200,0,true,true,true);
        s.incumbent(0);s.begin(1100);s.finished(now,status,remaining);
        const double next=s.next_timeout();
        const double old_wait=std::max(stall,next);
        const double reserve=std::min(next,remaining*.5);
        const double wait=remaining>stall
            ?std::min(old_wait,std::max(stall,remaining-reserve)):old_wait;
        assert(wait>=stall);
        assert(wait<=old_wait);
        assert(s.last_original_wait()==old_wait&&s.last_wait()==wait);
        assert(s.tail_caps()==unsigned(wait<old_wait));
        assert(!s.ready(now+wait-.0001,1200-(now+wait-.0001)));
        if(now+wait<1200) {
            const double available=1200-(now+wait);
            assert(s.ready(now+wait,available));
            const double granted=s.begin(available);
            assert(granted>0&&granted<=available&&granted<=next);
            if(remaining>=2*stall) assert(available+1e-9>=reserve);
        }
    }
}

int main() {
    test_early_windows_and_first_feasible();
    test_improvement_overrides_reservation();
    test_same_bound_sat_and_slice_accounting();
    test_wait_telemetry_and_representability();
    test_short_tail_protects_stall();
    test_immediate_unknown_cannot_zeno();
    test_scoping_and_missing_clock();
    test_default_off_trace_parity();
    test_reservation_and_budget_properties();
    std::cout << "PASS: 9 policy groups; real 1200-second tail fixture; 8000 default-OFF parity events; "
                 "4800 reservation/budget states; no solver linked or run\n";
}
