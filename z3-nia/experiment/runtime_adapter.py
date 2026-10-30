"""Experiment-only command translation; this is not the embedded solver."""
import math


def validated_solver_options(options=()):
    """Allow only the explicitly supported search exchange experiment controls."""
    if not isinstance(options, (list, tuple)):
        raise ValueError('solver options must be a list')
    seen = set()
    timing = {'--core-neighborhood-stall', '--core-neighborhood-budget'}
    for option in options:
        if not isinstance(option, str):
            raise ValueError('solver option must be a string')
        name, separator, value = option.partition('=')
        if name in seen:
            raise ValueError(f'duplicate solver option: {name}')
        seen.add(name)
        if name in ('--core-neighborhood', '--convex-exchange') and not separator:
            continue
        if name == '--smt-exchange' and separator and value in ('backend', 'hints'):
            continue
        if name not in timing or not separator or value.strip() != value:
            raise ValueError(f'unsupported solver option: {option}')
        try:
            seconds = float(value)
        except ValueError:
            raise ValueError(f'solver option requires finite positive seconds: {option}') from None
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError(f'solver option requires finite positive seconds: {option}')
    if seen.intersection(timing) and '--core-neighborhood' not in seen:
        raise ValueError('solver option timing controls require --core-neighborhood')
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
