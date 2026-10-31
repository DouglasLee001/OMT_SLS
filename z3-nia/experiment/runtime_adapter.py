"""Experiment-only command translation; this is not the embedded solver."""
import math
import re


def validated_solver_options(options=()):
    """Allow only the explicitly supported search exchange experiment controls."""
    if not isinstance(options, (list, tuple)):
        raise ValueError('solver options must be a list')
    seen = set()
    core_timing = {'--core-neighborhood-stall', '--core-neighborhood-budget'}
    timing = core_timing | {'--objective-slice-budget'}
    for option in options:
        if not isinstance(option, str):
            raise ValueError('solver option must be a string')
        name, separator, value = option.partition('=')
        if name in seen:
            raise ValueError(f'duplicate solver option: {name}')
        seen.add(name)
        if name in ('--core-neighborhood', '--convex-exchange', '--objective-slice', '--integer-convex-exchange',
                    '--integer-affine-convex-exchange',
                    '--isolate-exchange-context', '--slack-repair', '--optimality-tail',
                    '--guarded-product') and not separator:
            continue
        if name == '--smt-exchange' and separator and value in ('backend', 'hints'):
            continue
        if name == '--optimality-bv-seconds' and separator:
            if not re.fullmatch(r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?', value):
                raise ValueError('BV seconds require 0 (OFF) or finite seconds in [0.001,1200]')
            seconds = float(value)
            # A nonzero mantissa may underflow to signed floating-point zero.
            # Only a textual zero is OFF; match the native CLI's range guard.
            nonzero_mantissa = any(digit in '123456789' for digit in re.split('[eE]', value, maxsplit=1)[0])
            if (not math.isfinite(seconds) or seconds < 0 or seconds > 1200 or 0 < seconds < .001
                    or seconds == 0 and nonzero_mantissa):
                raise ValueError('BV seconds require 0 (OFF) or finite seconds in [0.001,1200]')
            continue
        if name == '--advisory-share' and separator:
            if not re.fullmatch(r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?', value):
                raise ValueError('advisory share requires a finite fraction strictly between 0 and 1')
            share = float(value)
            if not math.isfinite(share) or not 0 < share < 1:
                raise ValueError('advisory share requires a finite fraction strictly between 0 and 1')
            continue
        if name not in timing or not separator or value.strip() != value:
            raise ValueError(f'unsupported solver option: {option}')
        try:
            seconds = float(value)
        except ValueError:
            raise ValueError(f'solver option requires finite positive seconds: {option}') from None
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError(f'solver option requires finite positive seconds: {option}')
    if seen.intersection(core_timing) and '--core-neighborhood' not in seen:
        raise ValueError('solver option timing controls require --core-neighborhood')
    if '--objective-slice-budget' in seen and '--objective-slice' not in seen:
        raise ValueError('--objective-slice-budget requires --objective-slice')
    if '--integer-convex-exchange' in seen and '--convex-exchange' not in seen:
        raise ValueError('--integer-convex-exchange requires --convex-exchange')
    if '--integer-affine-convex-exchange' in seen and '--convex-exchange' not in seen:
        raise ValueError('--integer-affine-convex-exchange requires --convex-exchange')
    if '--isolate-exchange-context' in seen and not seen.intersection({'--convex-exchange', '--smt-exchange', '--objective-slice'}):
        raise ValueError('--isolate-exchange-context requires an exchange backend')
    return tuple(options)


def translate_command(command, variant, executable, converter, solver_options=()):
    command = list(map(str, command))
    options = validated_solver_options(solver_options)
    if variant == 'baseline':
        return command
    if variant != 'embedded':
        raise ValueError('unknown experiment variant')
    if len(command) >= 5 and command[1] == str(converter):
        if command[3] != '-o' or any(x != '--keep-definitions' for x in command[5:]):
            raise ValueError('unsupported reference converter command')
        return [str(executable), '--nia-ls', command[2], '--import-only',
                '--export-native=' + command[4], *command[5:]]
    if len(command) >= 3 and command[2] == '--optimize':
        return [str(executable), '--nia-ls', '--native-input', command[1], *command[2:], *options]
    raise ValueError('unexpected reference runner command')


def install(runner, variant, executable, converter, solver_options=()):
    options = validated_solver_options(solver_options)
    original = runner._stage

    def stage(command, *args, **kwargs):
        return original(translate_command(command, variant, executable, converter, options), *args, **kwargs)

    runner._stage = stage
