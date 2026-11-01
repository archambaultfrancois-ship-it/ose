"""Generate Bash completion from the CLI's argparse definitions."""
import argparse
import shlex


def bash_completion(parser):
    """Return a standalone script; completing never runs a CLI command."""
    transitions, values, suggestions = [], [], []

    def visit(command, context):
        words = []
        for action in command._actions:
            if action.help == argparse.SUPPRESS:
                continue
            words.extend(action.option_strings)
            if isinstance(action, argparse._SubParsersAction):
                for name, child in action.choices.items():
                    target = context + '/' + name
                    transitions.append(f'        {shlex.quote(context + ":" + name)}) context={shlex.quote(target)} ;;')
                    words.append(name)
                    visit(child, target)
            elif action.option_strings and action.nargs != 0:
                for option in action.option_strings:
                    key = context + ':' + option
                    transitions.append(f'        {shlex.quote(key)}) [[ "$word" == *=* ]] || expecting={shlex.quote(key)} ;;')
                    choices = ' '.join(map(str, action.choices or ()))
                    values.append(f'        {shlex.quote(key)}) candidates={shlex.quote(choices)} ;;')
            elif not action.option_strings and action.choices:
                words.extend(map(str, action.choices))
        suggestions.append(f'        {shlex.quote(context)}) candidates={shlex.quote(" ".join(words))} ;;')

    visit(parser, 'root')
    return '''# Bash completion for ose. Load with: source <(ose completion bash)
_ose_complete() {
    local context=root expecting= candidates= word option current prefix= candidate
    local i
    local -a words=()
    COMPREPLY=()
    # Bash normally splits --option=value at '='. Reassemble those words
    # without changing the user's global COMP_WORDBREAKS setting.
    for ((i=1; i<=COMP_CWORD; i++)); do
        word=${COMP_WORDS[i]}
        if [[ "$word" == = && ${#words[@]} -gt 0 ]]; then
            words[${#words[@]}-1]+="="
        elif [[ ${#words[@]} -gt 0 && ${words[${#words[@]}-1]} == *= ]]; then
            words[${#words[@]}-1]+="$word"
        else
            words+=("$word")
        fi
    done
    for ((i=0; i<${#words[@]}-1; i++)); do
        word=${words[i]}
        if [[ -n "$expecting" ]]; then
            expecting=
            continue
        fi
        [[ "$word" != -- ]] || return 0
        option=${word%%=*}
        case "$context:$option" in
''' + '\n'.join(transitions) + '''
        esac
    done
    current=${words[${#words[@]}-1]-}
    if [[ -z "$expecting" && "$current" == --*=* ]]; then
        expecting="$context:${current%%=*}"
        # Include the option prefix only if Bash kept it in the current word.
        [[ ${COMP_WORDS[COMP_CWORD]} != "$current" ]] || prefix="${current%%=*}="
        current=${current#*=}
    fi
    if [[ -n "$expecting" ]]; then
        case "$expecting" in
''' + '\n'.join(values) + '''
        esac
    else
        case "$context" in
''' + '\n'.join(suggestions) + '''
        esac
    fi
    while IFS= read -r candidate; do
        COMPREPLY+=("$prefix$candidate")
    done < <(compgen -W "$candidates" -- "$current")
    return 0
}
complete -o bashdefault -o default -F _ose_complete ose
'''
