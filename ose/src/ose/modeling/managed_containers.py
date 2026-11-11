"""Java and Ada backends using public generated SDL declarations."""
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import sys
from ose.modeling.native_containers import merge_schemas, sdk_root

JAVA_KEYWORDS = set('abstract assert boolean break byte case catch char class const continue default do double else enum extends final finally float for goto if implements import instanceof int interface long native new package private protected public return short static strictfp super switch synchronized this throw throws transient try void volatile while true false null'.split())
ADA_KEYWORDS = set('abort abs abstract accept access aliased all and array at begin body case constant declare delay delta digits do else elsif end entry exception exit for function generic goto if in interface is limited loop mod new not null of or others out overriding package pragma private procedure protected raise range record rem renames requeue return reverse select separate some subtype synchronized tagged task terminate then type until use when while with xor'.split())


def validate_names(language, name, node, container):
    if language not in ('java', 'ada'):
        if 'class' in container or 'unit' in container:
            raise ValueError('class/unit only apply to Java/Ada containers')
        return
    key = 'class' if language == 'java' else 'unit'
    if set(container) - {'language', 'handler', key}:
        raise ValueError(f'unexpected {language} container identity field')
    identity = container.get(key)
    if not isinstance(identity, str) or not identity:
        raise ValueError(f'{language} container requires {key}')
    def valid(value, allow_keyword=False):
        if language == 'java':
            return bool(re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', value)) and value not in JAVA_KEYWORDS
        return bool(re.fullmatch(r'[A-Za-z](?:[A-Za-z0-9]|_[A-Za-z0-9])*', value)) and (allow_keyword or value.lower() not in ADA_KEYWORDS)
    if not all(valid(part) for part in identity.split('.')):
        raise ValueError(f'invalid {language} {key}: {identity}')
    root = identity.split('.')[0].lower()
    if language == 'ada' and (root in {'ada', 'interfaces', 'system', 'gnat', 'main'} or
                              any(port.lower() in {'start', 'stop', 'tick'} for port in node['inputs'])):
        raise ValueError('Ada identity conflicts with runtime units or reserved callbacks')
    if language == 'java' and identity.startswith(('java.', 'sun.', 'jdk.')):
        raise ValueError('Java handler package is reserved by the JVM')
    if identity.lower().startswith(('org.sdlose.', 'sdl.', 'sdl_', 'ose_')):
        raise ValueError('handler identity conflicts with a reserved SDK namespace')
    for index, names in enumerate(([name], list(node['inputs']), list(node['outputs']))):
        # Ports appear inside prefixed Ada identifiers, so keywords are valid.
        allow_keyword = language == 'ada' and index > 0
        if not all(valid(part, allow_keyword) for part in names):
            raise ValueError(f'invalid or reserved {language} identifier')
        if language == 'ada' and len(set(p.lower() for p in names)) != len(names):
            raise ValueError('Ada port identifiers collide ignoring case')


def discover_bindings(destination, language, logical_types):
    result = {}
    directory = Path(destination) / language
    if language == 'java':
        for source in directory.rglob('*.java'):
            text = source.read_text()
            package = re.search(r'^package\s+([\w.]+)\s*;', text, re.M)
            if not package:
                continue
            for match in re.finditer(r'public static final class (\w+) implements SdlCodec.Message\s*\{(.*?)(?=public static final class|\Z)', text, re.S):
                logical = re.search(r'public static final String SDL_NAME = ("(?:\\.|[^"\\])*");', match[2])
                if logical:
                    name = json.loads(logical[1])
                    result[name] = [package[1] + '.' + source.stem, match[1]]
    else:
        for source in directory.rglob('*.adb'):
            text = source.read_text()
            package = re.search(r'package body ([\w.]+) is', text)
            if not package:
                continue
            # The generated Encode body contains the authoritative logical name.
            for match in re.finditer(r'function Encode \(C : Context; M : (\w+)\) return Bytes is(.*?)(?=\n   function|\Z)', text, re.S):
                logical = re.search(r'(?:Check_Encode|Message_Id)\s*\(C,\s*"([^"]+)"\)', match[2])
                if logical:
                    result[logical[1]] = [package[1], match[1]]
    missing = set(logical_types) - result.keys()
    if missing:
        raise ValueError(f'cannot resolve generated {language} bindings: {sorted(missing)}')
    return {name: result[name] for name in logical_types}


def tool(name, default):
    if name in os.environ:
        return shlex.split(os.environ[name])
    if name in ('JAVA', 'JAVAC', 'JAR') and os.environ.get('JAVA_HOME'):
        return [str(Path(os.environ['JAVA_HOME']) / 'bin' / default)]
    return [default]


def java_string(value):
    return json.dumps(value, ensure_ascii=True)


def generate_java(name, node, handler, sdl_root, schemas, destination, identity):
    if node.get('execution_model') != 2:
        raise ValueError('Java containers require execution model 2')
    destination = Path(destination)
    types = merge_schemas(name, 'java', schemas, destination / 'types')
    # A component without ports still needs the public runtime.
    runtime = destination / 'types/sdl/runtime/SdlCodec.java'
    if not runtime.exists():
        runtime.parent.mkdir(parents=True, exist_ok=True)
        runtime.write_bytes((Path(sdl_root) / 'runtime/java/SdlCodec.java').read_bytes())
    package = 'org.sdlose.generated.' + name
    source = destination / 'src' / Path(package.replace('.', '/'))
    source.mkdir(parents=True, exist_ok=True)
    classes = destination / 'classes'
    if classes.exists():
        shutil.rmtree(classes)
    classes.mkdir(parents=True, exist_ok=True)
    reserved = {'org.sdlose.Component', 'org.sdlose.Json', 'org.sdlose.InputProtocol', 'org.sdlose.NativeTransport',
                package + '.API', package + '.Main', identity}
    if any(wrapper in reserved for wrapper, _ in types.values()):
        raise ValueError('Java schema wrapper conflicts with the generated component API or handler')
    if handler.stem != identity.rsplit('.', 1)[-1]:
        raise ValueError('Java handler filename must match class name')
    def typ(desc):
        return '.'.join(types[desc['root']])
    def ports(direction):
        expressions = []
        for index, (port, desc) in enumerate(node.get(direction, {}).items()):
            if desc.get('kind') == 'timer':
                expression = 'null'
            else:
                resource = direction + '_' + str(index) + '.sdl'
                target = classes / package.replace('.', '/') / resource
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(desc['catalogue'], encoding='utf-8')
                expression = 'new Component.Port(%s, %s, Component.resource(API.class, %s), %s.class, %s)' % (
                    java_string(port), java_string(desc['root']), java_string(resource), typ(desc),
                    str('timeout' in desc).lower())
            if direction == 'inputs':
                mode = desc['timer']['mode'] if desc.get('kind') == 'timer' else desc.get('mode', 'push')
                outputs = [','.join(map(java_string, desc.get('activates', {}).get(event, []))) for event in ('message', 'timeout')]
                expression = (f'new Component.InputSpec({java_string(port)}, {expression}, org.sdlose.InputProtocol.Mode.{mode.upper()}, '
                              f'new String[]{{{outputs[0]}}}, new String[]{{{outputs[1]}}})')
            expressions.append(expression)
        # Individual factory methods avoid a large method for registration tables.
        port_type = 'Component.InputSpec' if direction == 'inputs' else 'Component.Port'
        methods = [f'    private static {port_type} {direction}_{i}() throws Exception {{ return {expr}; }}' for i, expr in enumerate(expressions)]
        methods += [f'    static {port_type}[] {direction}() throws Exception {{', f'        {port_type}[] ports = new {port_type}[{len(expressions)}];']
        methods += [f'        ports[{i}] = {direction}_{i}();' for i in range(len(expressions))]
        methods += ['        return ports;', '    }']
        return methods
    lines = [f'package {package};', 'import org.sdlose.Component;', 'public final class API {'] + ports('inputs') + ports('outputs') + ports('parameter_bindings')
    lines += ['    public static final class Context extends Component.Context {']
    setup = f'super({java_string(name)}, outputs()); configureInputs(inputs());'
    if node.get('parameter_bindings'):
        readable = ','.join(str('read' in b['access']).lower() for b in node['parameter_bindings'].values())
        writable = ','.join(str('write' in b['access']).lower() for b in node['parameter_bindings'].values())
        setup += f' configureParameters(parameter_bindings(), new boolean[]{{{readable}}}, new boolean[]{{{writable}}});'
    lines += [f'        public Context() throws Exception {{ {setup} }}']
    if node.get('parameter_bindings'):
        for i, (alias, desc) in enumerate(node['parameter_bindings'].items()):
            if 'read' in desc['access']:
                lines += [f'        public {typ(desc)} parameters_{alias}() throws Exception {{ return ({typ(desc)})parameter({i}); }}']
            if 'write' in desc['access']:
                lines += [f'        public void set_parameters_{alias}({typ(desc)} value) throws Exception {{ setParameter({i}, value); }}']
    for i, (port, desc) in enumerate(node['outputs'].items()):
        lines += [f'        public void publish_{port}({typ(desc)} message) throws Exception {{ publish({i}, message); }}']
    from ose.modeling import java_inputs
    lines += java_inputs.methods(node, typ) + ['    }'] + java_inputs.api(node, typ)
    (source / 'API.java').write_text('\n'.join(lines) + '\n')
    main = java_inputs.main(package, identity)
    (source / 'Main.java').write_text(main)
    sources = sorted((destination / 'types').rglob('*.java')) + sorted((sdk_root() / 'java').rglob('*.java')) + [source / 'API.java', source / 'Main.java', handler]
    command = [sys.executable, '-m', 'ose.modeling.java_build', str(classes), *map(str, sources)]
    from ose.modeling.java_transport_build import library_name
    native = destination / 'native'
    bridge = [sys.executable, '-m', 'ose.modeling.java_transport_build', str(native)]
    return {'types': types, 'command': tool('JAVA', 'java') +
            ['-Dose.component.transport.library=' + str(native / library_name()), '-cp', str(classes), package + '.Main'],
            'build': [bridge, command]}


def generate_ada(*args, **kwargs):
    from ose.modeling.ada_containers import generate
    return generate(*args, **kwargs)


def scaffold_managed(node, container):
    handler = Path(container['handler'])
    if container['language'] == 'ada':
        from ose.modeling.ada_containers import scaffold
        return scaffold(node, container)
    if node.get('execution_model') != 2:
        raise ValueError('Java containers require execution model 2')
    if handler.exists():
        return
    identity = container['class']; package, _, simple = identity.rpartition('.')
    name = next(part for part in node['command'] if part.endswith('.Main')).split('.')[-2]
    lines = ([f'package {package};'] if package else []) + [f'import org.sdlose.generated.{name}.API;', f'public final class {simple} extends API.Handler {{']
    for port, desc in node['inputs'].items():
        if desc.get('mode', 'push') == 'pull':
            continue
        from ose.modeling.java_inputs import input_type
        typ = 'org.sdlose.InputProtocol.Reference<' + input_type(desc, lambda d: '.'.join(container['types'][d['root']])) + '>'
        lines += [f'    @Override public void on_{port}(API.Context ctx, {typ} input) throws Exception {{',
                  '        throw new UnsupportedOperationException("Implement input processing");', '    }']
    lines += ['}', '']
    if handler.stem != simple:
        raise ValueError('Java handler filename must match class name')
    handler.parent.mkdir(parents=True, exist_ok=True)
    handler.write_text('\n'.join(lines))
