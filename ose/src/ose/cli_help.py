"""Display existing subcommands in workflow phases without changing parsing."""


def group_commands(parser, commands, phases):
    names = [name for _, group in phases for name in group]
    if len(names) != len(set(names)) or set(names) != set(commands.choices):
        raise ValueError('help phases must list every command exactly once')
    actions = {action.dest: action for action in commands._choices_actions}
    choices = {name: commands.choices[name] for name in names}
    # Keep the real subparser action for parsing/usage; display its help entries
    # in separate sections. Completion follows the same chronological order.
    for group in parser._action_groups:
        if commands in group._group_actions:
            group._group_actions.remove(commands)
    for title, group in phases:
        parser.add_argument_group(title)._group_actions.extend(actions[name] for name in group)
    commands._choices_actions[:] = [actions[name] for name in names]
    commands.choices.clear()
    commands.choices.update(choices)
