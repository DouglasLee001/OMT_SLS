#pragma once
#include <stdexcept>
#include <string>

namespace nia { namespace embedded {

struct ImportOptions {
    bool preprocess = true;
    bool keep_definitions = false;
};

class ImportError : public std::runtime_error {
public:
    using std::runtime_error::runtime_error;
};

// Parse/preprocess exactly one scalar integer OMT query into NIA_LS_OMT v1.
// This owns a private Z3 context, uses only parsing/tactics, and never searches.
std::string convert_text(std::string const& source, std::string const& source_name = "",
                         ImportOptions const& options = ImportOptions{});

} }
