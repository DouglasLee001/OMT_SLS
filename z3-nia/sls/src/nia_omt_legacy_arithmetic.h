#pragma once

#include "nia_omt_input.h"
#include <exception>
#include <gmpxx.h>

namespace nia {
namespace omt_legacy_arithmetic {
// Disabled in the default legacy/first-solution path. Only the optimization
// continuation enables this thread-local arithmetic policy.
inline thread_local bool enabled = false;

class Overflow final : public std::exception {
public:
    const char* what() const noexcept override { return "optimization legacy arithmetic exceeds signed 128-bit range"; }
};

inline __int128_t add(__int128_t a,__int128_t b) {
    __int128_t value;if(__builtin_add_overflow(a,b,&value))throw Overflow();return value;
}
inline __int128_t subtract(__int128_t a,__int128_t b) {
    __int128_t value;if(__builtin_sub_overflow(a,b,&value))throw Overflow();return value;
}
inline __int128_t multiply(__int128_t a,__int128_t b) {
    __int128_t value;if(__builtin_mul_overflow(a,b,&value))throw Overflow();return value;
}
inline __int128_t negate(__int128_t value) { return subtract(0,value); }
inline __int128_t absolute(__int128_t value) { return value<0?negate(value):value; }
inline __int128_t power(__int128_t base,unsigned exponent) {
    __int128_t value=1;
    while(exponent) {
        if(exponent&1)value=multiply(value,base);
        exponent>>=1;
        if(exponent)base=multiply(base,base);
    }
    return value;
}
inline __int128_t divide(__int128_t numerator,__int128_t denominator) {
    const __int128_t minimum=-__int128_t((__uint128_t(1)<<127)-1)-1;
    if(!denominator||(numerator==minimum&&denominator==-1))throw Overflow();
    return numerator/denominator;
}
inline __int128_t ceil_away(__int128_t numerator,__int128_t denominator) {
    __int128_t quotient=divide(numerator,denominator);
    if(numerator%denominator)quotient=add(quotient,(numerator<0)==(denominator<0)?1:-1);
    return quotient;
}
inline __int128_t delta(__int128_t current,__int128_t coefficient,__int128_t old_value,
                        __int128_t new_value,unsigned exponent) {
    return add(current,multiply(coefficient,subtract(power(new_value,exponent),power(old_value,exponent))));
}
inline mpz_class integer(__int128_t value) { return mpz_class(format_omt_integer(value),10); }

class Scope {
public:
    explicit Scope(bool exact = true) : previous_(enabled) { enabled = exact; }
    ~Scope() { enabled = previous_; }
    Scope(const Scope&) = delete;
    Scope& operator=(const Scope&) = delete;
private:
    bool previous_;
};
}
}
