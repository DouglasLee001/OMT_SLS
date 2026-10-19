#!/usr/bin/env python3
"""One benchmark solver process; stdout is a flushed JSON event stream."""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import selectors
import subprocess
import sys
import time
import traceback

from smtlib import finite_value, numeric_value, optimization_input, parse, render, strictly_better


ROOT = Path(__file__).resolve().parent.parent


class CandidateTracker:
    """Retain the first observation of each improving finite solver value."""

    def __init__(self, start_ns, emit):
        self.start_ns = start_ns
        self.emit = emit
        self.best_value = None
        self.time_to_best_seconds = None
        self.best_value_source = None
        self.best_exact = None

    def elapsed(self):
        return (time.monotonic_ns() - self.start_ns) / 1_000_000_000

    def consider(self, value, source, observed_seconds=None, exact_value=None, exact_less=None):
        finite = finite_value(value) is not None or exact_value is not None
        if not finite:
            return False
        better = strictly_better(value, self.best_value)
        if not better and exact_value is not None:
            better = self.best_value is None or (
                self.best_exact is not None and exact_less is not None
                and exact_less(exact_value, self.best_exact)
            )
        if not better:
            return False
        observed = self.elapsed() if observed_seconds is None else observed_seconds
        self.best_value = value
        self.best_exact = exact_value
        self.time_to_best_seconds = observed
        self.best_value_source = source
        self.emit({"event": "candidate", "value": value,
                   "time_seconds": observed, "source": source})
        return True

    def final(self, **fields):
        event = {
            "event": "final", "status": "error", "best_value": self.best_value,
            "completed_seconds": self.elapsed(),
            "time_to_best_seconds": self.time_to_best_seconds,
            "best_value_source": self.best_value_source, "reported_optimum": None,
            "model_value": None, "optimality_status": "none",
            "solver_reported_optimal": False, "native_exit_code": 1,
            "reason": None, "time_to_optimal_seconds": None,
            "lower_bound": None, "upper_bound": None,
            "time_to_reported_optimum_seconds": None,
        }
        event.update(fields)
        return event


@contextmanager
def native_logs(case_dir):
    """Keep Z3's native diagnostics out of the JSON protocol pipe."""
    sys.stdout.flush()
    sys.stderr.flush()
    saved = [os.dup(1), os.dup(2)]
    try:
        with (case_dir / "stdout.txt").open("ab", buffering=0) as stdout_log, \
                (case_dir / "stderr.txt").open("ab", buffering=0) as stderr_log:
            os.dup2(stdout_log.fileno(), 1)
            os.dup2(stderr_log.fileno(), 2)
            yield
    finally:
        sys.stdout.flush()
        sys.stderr.flush()
        os.dup2(saved[0], 1)
        os.dup2(saved[1], 2)
        for descriptor in saved:
            os.close(descriptor)


def classify(status, reported, model, best, lower=None, upper=None, optimal=False):
    """Check the solver's optimum report against its bounds and concrete model."""
    if status != "sat":
        return "none", False
    value = numeric_value(reported) if reported is not None else None
    lo = numeric_value(lower) if lower is not None else None
    hi = numeric_value(upper) if upper is not None else None
    model_number = finite_value(model) if model is not None else None
    best_number = finite_value(best) if best is not None else None
    if lo is not None and hi is not None and lo > hi:
        return "inconsistent", False
    if value is not None:
        if lo is not None and value < lo or hi is not None and value > hi:
            return "inconsistent", False
        if value[0] > 0 and model is not None:
            return "inconsistent", False
        if value[0] < 0:
            return "unbounded", optimal
        if value[2] != 0:
            if model_number is not None and (0, model_number, 0) < value:
                return "inconsistent", False
            return "infimum", optimal
        if model_number is not None and model_number != value[1]:
            return "inconsistent", False
        if optimal and model_number == best_number == value[1] and (
            lo is None or hi is None or lo == hi == value
        ):
            return "attained", True
    return "unconfirmed", False


def run_z3(input_path, tracker):
    package = ROOT / "competitor/z3-5.1.0/install/lib/python3.12/site-packages"
    sys.path.insert(0, str(package))
    import z3

    opt = z3.Optimize()
    opt.from_file(str(input_path))
    objectives = opt.objectives()
    if len(objectives) != 1:
        raise ValueError("Expected exactly one Z3 objective")
    objective = objectives[0]
    callback_errors = []

    def exact_less(new, old):
        return z3.is_true(z3.simplify(new < old))

    def observe(model, source, observed):
        # AlgebraicNumRef owns its AST; it remains valid after the callback model expires.
        evaluated = model.eval(objective, model_completion=True)
        if not (z3.is_int_value(evaluated) or z3.is_rational_value(evaluated)
                or z3.is_algebraic_value(evaluated)):
            return None
        value = evaluated.sexpr()
        tracker.consider(value, source, observed, exact_value=evaluated, exact_less=exact_less)
        return value

    def on_model(model):
        observed = tracker.elapsed()
        try:
            observe(model, "model_callback", observed)
        except Exception as error:
            # A cancellation can make callback model queries fail; preserve earlier values.
            callback_errors.append(str(error))

    opt.set_on_model(on_model)
    reason = None
    try:
        status = str(opt.check())
    except z3.Z3Exception as error:
        if "cancel" not in str(error).lower() and "timeout" not in str(error).lower():
            raise
        status, reason = "unknown", str(error)
    if status == "unknown" and reason is None:
        try:
            reason = opt.reason_unknown()
        except Exception as error:
            reason = str(error)
    model_value = None
    if status in ("sat", "unknown"):
        try:
            observed = tracker.elapsed()
            model_value = observe(opt.model(), "final_model", observed)
        except Exception as error:
            reason = reason or str(error)
    lower = upper = None
    bound_observed = None
    if status in ("sat", "unknown"):
        try:
            handle = z3.OptimizeObjective(opt, 0, is_max=False)
            lower = handle.lower().sexpr()
            upper = handle.upper().sexpr()
            bound_observed = tracker.elapsed()
        except Exception as error:
            reason = reason or str(error)
    reported_optimal = status == "sat" and lower is not None and lower == upper
    optimality, _ = classify(
        status, lower, model_value, tracker.best_value, lower, upper,
        optimal=reported_optimal,
    )
    reported_seconds = bound_observed
    if lower is not None and tracker.best_value is not None:
        number, best_number = finite_value(lower), finite_value(tracker.best_value)
        if number is not None and number == best_number:
            reported_seconds = tracker.time_to_best_seconds
    completed = tracker.elapsed()
    if callback_errors and reason is None:
        reason = "Callback observation failed: " + callback_errors[-1]
    return tracker.final(
        status=status, reported_optimum=lower, model_value=model_value,
        optimality_status=optimality, solver_reported_optimal=reported_optimal,
        native_exit_code=0, reason=reason, lower_bound=lower, upper_bound=upper,
        time_to_reported_optimum_seconds=reported_seconds,
        time_to_optimal_seconds=completed if optimality == "attained" else None,
    )


def opti_numeric(text):
    """Translate Opti's diagnostic numeric notation into exact SMT expressions."""
    text = text.strip()
    if text == "-oo":
        return "(- oo)"
    if text == "+oo":
        return "oo"
    match = re.fullmatch(r"(.+?)\s*([+-])\s*epsilon", text)
    if match:
        return f"({match[2]} {opti_numeric(match[1])} epsilon)"
    match = re.fullmatch(r"(-?\d+)\s*/\s*(\d+)", text)
    if match:
        numerator = match[1]
        if numerator.startswith("-"):
            numerator = f"(- {numerator[1:]})"
        return f"(/ {numerator} {match[2]})"
    if re.fullmatch(r"-\d+(?:\.\d+)?", text):
        return f"(- {text[1:]})"
    return text


class OptiOutput:
    def __init__(self, objective, tracker):
        self.objective = objective
        self.tracker = tracker
        self.stdout = ""
        self.forms_seen = 0
        self.status = None
        self.reported_optimum = None
        self.model_value = None
        self.lower = None
        self.upper = None
        self.optimal = False
        self.optimal_seconds = None
        self.errors = []
        self.progress_times = {}
        self.reported_query_seconds = None

    def stderr_line(self, line, observed):
        if "sat_optimal" in line:
            self.optimal = True
            if self.optimal_seconds is None:
                self.optimal_seconds = observed
        if "# obj(" not in line:
            return
        match = re.search(r"-\s+new:\s*(.*?)\s*$", line)
        if match:
            value = opti_numeric(match[1])
            key = numeric_value(value)
            key = value if key is None else key
            self.progress_times.setdefault(key, observed)
            self.tracker.consider(value, "solver_progress", observed)
        interval = re.search(r"\[\s*(.*?)\s*,\s*(.*?)\s*\]", line)
        if interval:
            self.lower, self.upper = map(opti_numeric, interval.groups())

    def stdout_chunk(self, text, observed, final=False):
        self.stdout += text
        # A read can split an atom such as sat; only parse delimited input.
        complete = self.stdout if final else self.stdout[:self.stdout.rfind("\n") + 1]
        try:
            forms = parse(complete)
        except ValueError:
            return
        for form in forms[self.forms_seen:]:
            if isinstance(form, str) and form in ("sat", "unsat", "unknown"):
                self.status = form
            elif isinstance(form, list) and form:
                if form[0] == "error":
                    self.errors.append(render(form))
                elif form[0] == "objectives" and len(form) > 1:
                    entry = form[1]
                    if isinstance(entry, list) and len(entry) == 2:
                        value = render(entry[1])
                        self.reported_optimum = None if value in ("unsat", "unknown") else value
                        self.reported_query_seconds = observed
                elif isinstance(form[0], list) and len(form[0]) == 2:
                    self.model_value = render(form[0][1])
                    self.tracker.consider(self.model_value, "final_model", observed)
        self.forms_seen = len(forms)


def run_optimathsat(input_path, case_dir, generated, objective, tracker):
    generated_path = case_dir / "input.generated.smt2"
    generated_path.write_text(generated)
    command = [str(ROOT / "competitor/bin/optimathsat"), "-optimization=true",
               "-model_generation=true", "-opt.strategy=bin", "-opt.verbose=true",
               "-preprocessor.toplevel_propagation=false", str(generated_path)]
    output = OptiOutput(objective, tracker)
    process = None
    try:
        with (case_dir / "stdout.txt").open("ab", buffering=0) as stdout_log, \
                (case_dir / "stderr.txt").open("ab", buffering=0) as stderr_log:
            # Inherit the worker's process group so the scheduler can stop both together.
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ, ("stdout", stdout_log))
                selector.register(process.stderr, selectors.EVENT_READ, ("stderr", stderr_log))
                pending_stderr = b""
                while selector.get_map():
                    for key, _ in selector.select():
                        stream, log = key.data
                        chunk = os.read(key.fileobj.fileno(), 65536)
                        observed = tracker.elapsed()
                        if not chunk:
                            selector.unregister(key.fileobj)
                            key.fileobj.close()
                            if stream == "stderr" and pending_stderr:
                                output.stderr_line(pending_stderr.decode("utf-8", "replace"), observed)
                            elif stream == "stdout":
                                output.stdout_chunk("", observed, final=True)
                            continue
                        log.write(chunk)
                        if stream == "stdout":
                            output.stdout_chunk(chunk.decode("utf-8", "replace"), observed)
                        else:
                            pending_stderr += chunk
                            lines = pending_stderr.split(b"\n")
                            pending_stderr = lines.pop()
                            for line in lines:
                                output.stderr_line(line.decode("utf-8", "replace"), observed)
            native_exit_code = process.wait()
        status = output.status or "error"
        reason = None
        errors = output.errors
        if status in ("unsat", "unknown"):
            errors = [error for error in errors if not any(
                phrase in error.lower() for phrase in
                ("model generation not enabled", "model is not available", "no model", "model not available")
            )]
        if native_exit_code != 0:
            status, reason = "error", f"OptiMathSAT exited with status {native_exit_code}"
        if errors:
            status, reason = "error", "; ".join(errors)
        if output.status is None and reason is None:
            reason = "OptiMathSAT produced no satisfiability status"
        optimality, _ = classify(
            status, output.reported_optimum, output.model_value, tracker.best_value,
            output.lower, output.upper, output.optimal,
        )
        reported_seconds = output.reported_query_seconds
        if output.reported_optimum is not None:
            key = numeric_value(output.reported_optimum)
            key = output.reported_optimum if key is None else key
            progress_seconds = output.progress_times.get(key)
            if progress_seconds is not None:
                reported_seconds = min(progress_seconds, reported_seconds) if reported_seconds is not None else progress_seconds
            report_number = finite_value(output.reported_optimum)
            best_number = finite_value(tracker.best_value) if tracker.best_value is not None else None
            if report_number is not None and report_number == best_number:
                reported_seconds = tracker.time_to_best_seconds
        return tracker.final(
            status=status, reported_optimum=output.reported_optimum,
            model_value=output.model_value, optimality_status=optimality,
            solver_reported_optimal=output.optimal, native_exit_code=native_exit_code,
            lower_bound=output.lower, upper_bound=output.upper,
            time_to_reported_optimum_seconds=reported_seconds,
            reason=reason, time_to_optimal_seconds=(
                max(output.optimal_seconds or 0, tracker.time_to_best_seconds or 0)
                if optimality == "attained" else None),
        )
    finally:
        try:
            if process is not None:
                for pipe in (process.stdout, process.stderr):
                    if pipe is not None and not pipe.closed:
                        try:
                            pipe.close()
                        except OSError:
                            pass
                if process.poll() is None:
                    try:
                        process.terminate()
                    except ProcessLookupError:
                        pass
                    try:
                        process.wait(timeout=0.2)
                    except subprocess.TimeoutExpired:
                        try:
                            process.kill()
                        except ProcessLookupError:
                            pass
                        process.wait()
                else:
                    process.wait()
        finally:
            generated_path.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--solver", choices=("z3", "optimathsat"), required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--case-dir", type=Path, required=True)
    parser.add_argument("--start-ns", type=int, required=True)
    args = parser.parse_args()
    args.case_dir.mkdir(parents=True, exist_ok=True)
    for name in ("stdout.txt", "stderr.txt"):
        (args.case_dir / name).touch()
    with os.fdopen(os.dup(1), "w", buffering=1) as protocol:
        def emit(event):
            protocol.write(json.dumps(event, allow_nan=False) + "\n")
            protocol.flush()
        tracker = CandidateTracker(args.start_ns, emit)
        try:
            text = args.input.read_text()
            generated, objective = optimization_input(text)
            if args.solver == "z3":
                with native_logs(args.case_dir):
                    final = run_z3(args.input, tracker)
            else:
                final = run_optimathsat(args.input, args.case_dir, generated, objective, tracker)
        except Exception as error:
            with (args.case_dir / "stderr.txt").open("a") as error_log:
                traceback.print_exc(file=error_log)
            final = tracker.final(status="error", reason=str(error))
        final["completed_seconds"] = tracker.elapsed()
        emit(final)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
