"""Experiment-only command translation; this is not the embedded solver."""


def translate_command(command, variant, executable, converter):
    command = list(map(str, command))
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
        return [str(executable), '--nia-ls', '--native-input', command[1], *command[2:]]
    raise ValueError('unexpected reference runner command')


def install(runner, variant, executable, converter):
    original = runner._stage

    def stage(command, *args, **kwargs):
        return original(translate_command(command, variant, executable, converter), *args, **kwargs)

    runner._stage = stage
