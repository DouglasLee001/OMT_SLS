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
    std::cout<<"adaptive optimality schedule passed\n";
}
