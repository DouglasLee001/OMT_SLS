#include "../src/nia_omt_optimality_schedule.h"
#include <cassert>
#include <cmath>
#include <iostream>

int main() {
    nia::OmtOptimalitySchedule s(true,50,30,1200);
    assert(!s.ready(2000,1200)); // No feasible incumbent.
    s.incumbent(1);
    assert(!s.ready(50,1000));
    assert(s.ready(51,1000));
    assert(s.next_timeout()==30);
    s.incumbent(40); // Improvements before the first check retain its initial budget.
    assert(s.next_timeout()==30);
    assert(!s.ready(89,1000));
    assert(s.ready(90,1000));
    s.begin(1000); s.finished(120);
    assert(s.next_timeout()==60);
    assert(!s.ready(169,1000));
    assert(s.ready(170,1000));
    s.begin(1000);s.finished(230);
    assert(s.next_timeout()==120);
    s.incumbent(240); // A productive SLS phase halves the preceding 60 second check.
    assert(s.next_timeout()==30);
    s.incumbent(241);
    assert(s.next_timeout()==30); // One adjustment per phase, rather than per event.
    assert(!s.ready(290,900));
    assert(s.ready(291,900));
    s.begin(900);s.finished(321);
    s.incumbent(321); // A SAT model accepted immediately after a check is improvement.
    assert(s.next_timeout()==15);
    assert(!s.ready(370,800));
    assert(s.ready(371,800));
    assert(s.begin(4)==4); // The actual proof cannot exceed the remaining case time.
    s.finished(375);s.incumbent(375);
    assert(s.next_timeout()==2);
    s.begin(800);s.finished(427);s.incumbent(427);
    assert(s.next_timeout()==1);
    s.begin(800);s.finished(478);s.incumbent(478);
    assert(s.next_timeout()==1);

    nia::OmtOptimalitySchedule capped(true,1,30,90);
    capped.incumbent(0);capped.begin(100);capped.finished(31);
    assert(capped.next_timeout()==60);
    capped.begin(100);capped.finished(92);
    assert(capped.next_timeout()==90);
    capped.begin(100);capped.finished(183);
    assert(capped.next_timeout()==90);
    assert(!capped.ready(1000,0));

    nia::OmtOptimalitySchedule short_test(true,.01,.02,.5);
    short_test.incumbent(0);short_test.begin(.4);short_test.finished(.03);short_test.incumbent(.03);
    assert(short_test.next_timeout()==.02);
    nia::OmtOptimalitySchedule off(false,50,30,1200);
    off.incumbent(0);
    assert(!off.ready(1000,100));
    // Preserve the successful first short proof, then avoid cold restarts.
    nia::OmtOptimalitySchedule windowed(true,50,30,1200,250);
    windowed.incumbent(1);
    assert(windowed.ready(51,1149));
    assert(windowed.begin(1149)==30);
    windowed.finished(81,"budget");
    assert(!windowed.ready(330,870));
    assert(windowed.ready(331,869));
    const double long_budget=windowed.begin(869);
    assert(long_budget>800 && long_budget<869); // Reserve final publication time.
    windowed.finished(1000,"unknown");
    windowed.incumbent(1001); // A productive phase restores the first short probe.
    assert(windowed.next_timeout()==30);
    assert(!windowed.ready(1050,150));
    assert(windowed.ready(1051,149));
    windowed.begin(149);windowed.finished(1060,"sat");
    assert(!windowed.ready(1199,1)); // SAT refutes this bound even if model import fails.
    windowed.incumbent(1061);
    assert(windowed.ready(1111,89));
    assert(windowed.next_timeout()==15);
    // Source errors remain retryable and cannot be treated as SAT/UNSAT.
    windowed.begin(89);windowed.finished(1120,"error");
    assert(windowed.ready(1170,30));
    nia::OmtOptimalitySchedule short_window(true,.01,.02,.5,.05);
    short_window.incumbent(0);short_window.begin(.49);short_window.finished(.03,"budget");
    assert(!short_window.ready(.079,.4));
    assert(short_window.ready(.081,.4));
    assert(short_window.begin(.1)<.1);
    nia::OmtOptimalitySchedule retained(true,50,30,1200,0,true);
    retained.incumbent(0);retained.begin(1000);retained.finished(80,"sat");
    assert(!retained.ready(1199,1));
    retained.incumbent(90);
    assert(retained.ready(140,1060));
    assert(retained.next_timeout()==15);

    nia::OmtOptimalitySchedule balanced(true,50,30,1200,0,true,true);
    balanced.incumbent(0);
    assert(!balanced.ready(49,1151));
    assert(balanced.ready(50,1150)); // The initial probe retains its stall and slice.
    assert(balanced.begin(1150)==30);
    balanced.finished(80,"budget");
    assert(balanced.next_timeout()==60);
    assert(!balanced.ready(139,1061)); // Return a full next-slice window to SLS.
    assert(balanced.ready(140,1060));
    assert(balanced.begin(1060)==60);
    balanced.finished(200,"unknown");
    assert(balanced.next_timeout()==120);
    assert(!balanced.ready(319,881));
    assert(balanced.ready(320,880));

    balanced.incumbent(250); // An SLS improvement restores stall and halves once.
    assert(balanced.next_timeout()==30);
    assert(!balanced.ready(299,901));
    assert(balanced.ready(300,900));
    balanced.incumbent(251);
    assert(balanced.next_timeout()==30);
    assert(!balanced.ready(300,900));
    assert(balanced.ready(301,899));
    balanced.begin(899);balanced.finished(331,"sat");
    assert(!balanced.ready(1199,1)); // Same-bound SAT suppression stays intact.
    balanced.incumbent(331); // SAT acceptance keeps the ordinary burst/halve path.
    assert(balanced.next_timeout()==15);
    assert(!balanced.ready(380,820));
    assert(balanced.ready(381,819));

    nia::OmtOptimalitySchedule balanced_short(true,50,10,1200,0,false,true);
    balanced_short.incumbent(0);balanced_short.begin(1150);balanced_short.finished(60,"budget");
    assert(balanced_short.next_timeout()==20);
    assert(!balanced_short.ready(109,1091));
    assert(balanced_short.ready(110,1090)); // stall remains the minimum SLS window.

    nia::OmtOptimalitySchedule balanced_error(true,50,30,1200,0,false,true);
    balanced_error.incumbent(0);balanced_error.begin(1150);balanced_error.finished(80,"error");
    assert(balanced_error.next_timeout()==60);
    assert(!balanced_error.ready(129,1071));
    assert(balanced_error.ready(130,1070)); // Errors retain the original retry policy.
    balanced_error.begin(1070);balanced_error.finished(190,"sat");
    assert(!balanced_error.ready(239,961));
    assert(balanced_error.ready(240,960)); // Nonpersistent SAT behavior is unchanged.

    nia::OmtOptimalitySchedule balanced_cap(true,1,30,90,0,true,true);
    balanced_cap.incumbent(0);balanced_cap.begin(100);balanced_cap.finished(31,"budget");
    assert(balanced_cap.next_timeout()==60);
    assert(!balanced_cap.ready(90,1));
    assert(balanced_cap.ready(91,8));
    assert(balanced_cap.begin(8)==8); // The actual slice still obeys remaining time.
    balanced_cap.finished(99,"budget");
    assert(balanced_cap.next_timeout()==16);
    assert(!balanced_cap.ready(114,1));
    assert(!balanced_cap.ready(115,0));
    assert(balanced_cap.ready(115,.5));
    assert(balanced_cap.begin(.5)==.5);

    nia::OmtOptimalitySchedule balanced_limit(true,1,30,90,0,true,true);
    balanced_limit.incumbent(0);balanced_limit.begin(100);balanced_limit.finished(31,"budget");
    balanced_limit.begin(100);balanced_limit.finished(151,"unknown");
    assert(balanced_limit.next_timeout()==90);
    assert(!balanced_limit.ready(240,100));
    assert(balanced_limit.ready(241,100));
    assert(balanced_limit.begin(100)==90); // Balanced waiting does not enlarge the cap.

    nia::OmtOptimalitySchedule balanced_off(false,50,30,1200,0,true,true);
    balanced_off.incumbent(0);
    assert(!balanced_off.ready(2000,100));
    std::cout<<"adaptive, windowed, persistent and balanced optimality schedules passed\n";
}
