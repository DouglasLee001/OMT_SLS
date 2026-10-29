#pragma once

#include <string>

namespace nia {

// Parse without passing through a 64-bit intermediate.  This also accepts
// -2^127, whose positive magnitude is not representable by signed __int128.
bool parse_omt_integer(const std::string& text, __int128_t& value);

// Unlike the legacy printer, this is defined for the most negative integer.
std::string format_omt_integer(__int128_t value);

}
