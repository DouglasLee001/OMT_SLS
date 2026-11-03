// Diagnostic driver: replay an unchanged native formula from a retained
// feasible base. Old directions, anchors and selected literals are not inputs.
#include "nia_ls.h"
#include "nia_omt_input.h"
#include "nia_omt_unbounded.h"
#include <chrono>
#include <cmath>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

int main(int argc, char** argv) {
    if (argc != 4) {
        std::cerr << "usage: test_omt_unbounded_replay NATIVE BASE.txt SECONDS\n";
        return 2;
    }
    try {
        size_t used = 0;
        const double seconds = std::stod(argv[3], &used);
        if (used != std::string(argv[3]).size() || !std::isfinite(seconds) || seconds <= 0)
            throw std::runtime_error("seconds must be finite and positive");
        nia::ls_solver solver(9, 0, true);
        std::ifstream native(argv[1]);
        std::string error;
        if (!native || !solver.load_omt_instance(native, error))
            throw std::runtime_error("native import failed: " + error);
        std::ifstream input(argv[2]);
        size_t count;
        if (!(input >> count) || count != solver._num_vars)
            throw std::runtime_error("base variable count disagrees with native formula");
        std::vector<__int128_t> base(count);
        for (auto& value : base) {
            std::string number;
            if (!(input >> number) || !nia::parse_omt_integer(number, value))
                throw std::runtime_error("invalid signed-128 base coordinate");
        }
        std::string extra;
        if (input >> extra) throw std::runtime_error("unexpected data after base assignment");
        const auto began = std::chrono::steady_clock::now();
        const auto result = nia::prove_omt_unbounded(solver, base, seconds);
        const double elapsed = std::chrono::duration<double>(std::chrono::steady_clock::now() - began).count();
        // Status values are from the closed public result contract. The ray
        // certificate is already JSON produced by the solver's certificate writer.
        std::cout << std::setprecision(17)
                  << "{\"proved\":" << (result.proved ? "true" : "false")
                  << ",\"status\":\"" << result.status << "\",\"queries\":" << result.queries
                  << ",\"seconds\":" << elapsed
                  << ",\"direction_backend\":\"" << result.direction_backend << '"'
                  << ",\"direction_steps\":" << result.direction_steps
                  << ",\"guided_attempts\":" << result.guided_attempts
                  << ",\"fixed_set_adjustments\":" << result.fixed_set_adjustments
                  << ",\"certificate\":" << result.certificate_json << "}\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 2;
    }
}
