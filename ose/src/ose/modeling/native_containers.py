"""Typed C and Rust containers built on the public SDL runtimes and stdio SDKs."""
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import sysconfig


def sdk_root():
    source = Path(__file__).resolve().parents[3] / "sdk"
    target = Path(__file__).resolve().parents[2] / "share/ose/sdk"
    installed = Path(sysconfig.get_path("data")) / "share/ose/sdk"
    for candidate in (target, source, installed):
        if (candidate / "c/ose_callbacks.c").is_file() and (candidate / "rust/Cargo.toml").is_file():
            return candidate
    raise ValueError("native container SDK files are missing from this installation")


def c_string(value):
    escapes = {34: '\\"', 92: '\\\\', 63: '\\?'}
    return '"' + "".join(escapes.get(byte, chr(byte) if 32 <= byte < 127 else f"\\{byte:03o}")
                         for byte in value.encode()) + '"'


def rust_string(value):
    escapes = {'"': '\\"', '\\': '\\\\', '\n': '\\n', '\r': '\\r', '\t': '\\t'}
    return '"' + "".join(escapes.get(char, f"\\u{{{ord(char):x}}}" if ord(char) < 32 else char) for char in value) + '"'


def merge_schemas(name, language, schemas, destination):
    files, types = {}, {}
    suffixes = {"c": (".c", ".h"), "rust": (".rs",), "java": (".java",), "ada": (".ads", ".adb")}[language]
    for directory, bindings in schemas:
        for path in Path(directory).rglob("*"):
            if not path.is_file() or path.suffix not in suffixes or path.name in ("sdl_registry.c", "sdl_registry.h", "mod.rs"):
                continue
            relative = path.relative_to(directory)
            content = path.read_bytes()
            if relative in files and files[relative] != content:
                raise ValueError(f"{language} container {name}: conflicting generated module {relative}")
            files[relative] = content
        for logical, binding in bindings.items():
            if logical in types and types[logical] != binding:
                raise ValueError(f"{language} container {name}: conflicting bindings for {logical}")
            types[logical] = binding
    destination = Path(destination)
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    for relative, content in files.items():
        path = destination / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    if language == "rust":
        directories = {destination, *(p.parent for p in destination.rglob("*.rs"))}
        directories |= {parent for path in list(directories) for parent in path.parents if parent.is_relative_to(destination)}
        for directory in directories:
            modules = sorted({p.stem for p in directory.glob("*.rs") if p.name != "mod.rs"} |
                             {p.name for p in directories if p.parent == directory})
            (directory / "mod.rs").write_text("// Generated SDL modules.\n" + "".join(f"pub mod {module};\n" for module in modules))
    return types


def generate_c(name, node, handler, sdl_root, schemas, destination):
    if node.get("execution_model") != 2:
        raise ValueError("C containers require execution model 2")
    destination, sdl_root = Path(destination), Path(sdl_root)
    bindings = destination / "types"
    types = merge_schemas(name, "c", schemas, bindings)
    reserved = {"OseContext", "OsePortSpec", "OseHook", "ComponentCallbacks", "main", "dispatch", "inputs", "outputs",
                "ose_now", "ose_time_ns", "ose_tick_index", "parameters", "OseParameterSpec",
                "ose_parameter_value", "ose_set_parameter_value",
                "OseRecordResult", "ose_declare_recording_value", "ose_recording_enabled", "ose_record_value"}
    reserved.update("ose_declare_recording_" + symbol for _, symbol in types.values())
    reserved.update("ose_record_" + symbol for _, symbol in types.values())
    reserved.update("recording_catalogue_" + str(index) for index in range(len(set(node.get("recording_catalogues", {}).values()))))
    reserved.update('parameters_' + alias for alias in node.get('parameter_bindings', {}))
    reserved.update('set_parameters_' + alias for alias in node.get('parameter_bindings', {}))
    reserved.update("publish_" + port for port in node["outputs"])
    reserved.update("Input_" + port for port in node["inputs"])
    reserved.update("Output_" + port for port in node["outputs"])
    reserved.update(("OseInputReference", "OseSelector", "OseInputRecord", "OseActivation", "OseInputEvent",
                     "OseInputKind", "OseInputPolicy", "OseInputHook", "policies", "ose_arm", "ose_disarm",
                     "ose_get_input", "ose_remove_input", "ose_publish_value_with_id", "ose_run_input_callbacks"))
    reserved.update("publish_" + port + "_with_id" for port in node["outputs"])
    reserved.update(operation + "_" + port for port, desc in node["inputs"].items() if desc.get("mode") == "pull"
                    for operation in ("get", "pull", "remove"))
    reserved.update(f"ose_allow_{index}_{event}" for index in range(len(node["inputs"])) for event in ("message", "timeout"))
    for _, symbol in types.values():
        if symbol in reserved:
            raise ValueError(f"C container {name}: SDL type {symbol} conflicts with the generated component API")
    headers = sorted({binding[0] for binding in types.values()})
    header = ["/* Generated component API. Edit the handler, not this file. */",
              "#ifndef OSE_GENERATED_COMPONENT_H", "#define OSE_GENERATED_COMPONENT_H",
              '#include "ose_callbacks.h"', *(f'#include "{path}"' for path in headers)]
    from ose.modeling import c_inputs
    header += c_inputs.header(node, types)
    for port, desc in node["outputs"].items():
        header.append(f"int publish_{port}(OseContext *, const {types[desc['root']][1]} *);")
    for root in node.get("recording_catalogues", {}):
        symbol = types[root][1]
        header += [f"int ose_declare_recording_{symbol}(OseContext *, const char *name, const char *description);",
                   f"OseRecordResult ose_record_{symbol}(OseContext *, const char *name, const {symbol} *);"]
    for alias, desc in node.get('parameter_bindings', {}).items():
        typ = types[desc['root']][1]
        if 'read' in desc['access']:
            header.append(f"const {typ} *parameters_{alias}(const OseContext *);")
        if 'write' in desc['access']:
            header.append(f"int set_parameters_{alias}(OseContext *, const {typ} *);")
    header += ["#endif", ""]
    (destination / "component.h").write_text("\n".join(header))
    source = ['/* Generated from the component model. */', '#include "component.h"', '#include <stdio.h>', '#include <string.h>']
    recording_catalogues = {text: index for index, text in enumerate(dict.fromkeys(node.get("recording_catalogues", {}).values()))}
    for text, index in recording_catalogues.items():
        source.append(f"static const char recording_catalogue_{index}[] = {c_string(text)};")
    for root, text in node.get("recording_catalogues", {}).items():
        symbol = types[root][1]
        source += [f"int ose_declare_recording_{symbol}(OseContext *ctx, const char *name, const char *description) {{",
                   f"    return ose_declare_recording_value(ctx, name, {c_string(root)}, recording_catalogue_{recording_catalogues[text]}, description);", "}",
                   f"OseRecordResult ose_record_{symbol}(OseContext *ctx, const char *name, const {symbol} *value) {{",
                   f"    return ose_record_value(ctx, name, {c_string(root)}, value);", "}"]
    registrations = set()
    for path in sorted(bindings.rglob("*.h")):
        source.append(f'#include "types/{path.relative_to(bindings).as_posix()}"')
        registrations.update(re.findall(r"bool (register_\w+_types)\(void\);", path.read_text()))
    for direction in ("inputs", "outputs"):
        source.append(f"static const OsePortSpec {direction}[] = {{")
        for port, desc in node[direction].items():
            source.append("    {" + ", ".join((c_string(port), c_string(desc["root"]) if "root" in desc else "NULL", c_string(desc["catalogue"]) if "catalogue" in desc else "NULL",
                                               repr(float(desc.get("timeout", 0))))) + "},")
        source += ["    {NULL, NULL, NULL, 0}", "};"]
    for index, (port, desc) in enumerate(node["outputs"].items()):
        source += [f"int publish_{port}(OseContext *ctx, const {types[desc['root']][1]} *message) {{",
                   f"    return ose_publish_value(ctx, {index}, message);", "}"]
    source += ['static const OseParameterSpec parameters[] = {']
    for alias, desc in node.get('parameter_bindings', {}).items():
        source.append('    {' + ', '.join((c_string(alias), c_string(desc['root']), c_string(desc['catalogue']),
            '1' if 'read' in desc['access'] else '0', '1' if 'write' in desc['access'] else '0')) + '},')
    source += ['    {NULL, NULL, NULL, 0, 0}', '};']
    for index, (alias, desc) in enumerate(node.get('parameter_bindings', {}).items()):
        typ = types[desc['root']][1]
        if 'read' in desc['access']:
            source += [f'const {typ} *parameters_{alias}(const OseContext *ctx) {{ return ose_parameter_value(ctx, {index}); }}']
        if 'write' in desc['access']:
            source += [f'int set_parameters_{alias}(OseContext *ctx, const {typ} *value) {{ return ose_set_parameter_value(ctx, {index}, value); }}']
    source += c_inputs.source(node, types)
    for function in sorted(registrations):
        source.append(f"    if (!{function}()) return 1;")
    source += [f"    return ose_run_input_callbacks({c_string(name)}, inputs, {len(node['inputs'])}, policies, outputs, {len(node['outputs'])}, parameters, {len(node.get('parameter_bindings', {}))},",
               "        component_callbacks.on_start, dispatch, component_callbacks.on_stop);", "}", ""]
    (destination / "container.c").write_text("\n".join(source))
    sdk = sdk_root() / "c"
    runtime = sdl_root / "runtime/c"
    sources = [runtime / filename for filename in ("type_engine.c", "type_registry.c", "type_codec.c", "sdl_wire.c", "sdl_dynamic.c", "type_display.c")]
    binary = destination / "component"
    command = [*shlex.split(os.environ.get("CC", "cc")), "-std=c99", "-Wall", "-Wextra", "-Werror", "-O2",
               "-I" + str(destination), "-I" + str(bindings), "-I" + str(runtime), "-I" + str(sdk),
               *map(str, sources), *map(str, sorted(bindings.rglob("*.c"))), str(sdk / "ose_component.c"),
               str(sdk / "ose_callbacks.c"), str(destination / "container.c"), str(handler), "-lm", "-o", str(binary)]
    return {"types": types, "command": [str(binary)], "build": [command]}


def generate_rust(name, node, handler, sdl_root, schemas, destination):
    if node.get("execution_model") != 2:
        raise ValueError("Rust containers require execution model 2")
    destination, sdl_root = Path(destination), Path(sdl_root)
    source = destination / "src"
    types = merge_schemas(name, "rust", schemas, source / "types")
    imports = "receive, send" + (", input" if node.get("parameter_bindings") else "")
    lines = ["// Generated from the component model.", "#![allow(dead_code, non_snake_case)]",
             "use sdl_runtime::SdlMessage;", "use sdl_ose_component::{" + imports + "};",
             "pub use sdl_ose_component::inputs::{InputReference, EventKind, Selector};",
             "pub type Result<T = ()> = std::result::Result<T, Box<dyn std::error::Error>>;",
             "fn codec<T>(value: std::result::Result<T, sdl_runtime::CodecError>) -> Result<T> {",
             '    value.map_err(|error| format!("SDL codec: {error:?}").into())', "}",
             "pub struct Context {", "    pub name: &'static str,", "    pub config_json: String,", "    active: bool,",
             "    tick: sdl_ose_component::TickContext,", "    parameter_writes: bool,",
             "    recording: std::collections::HashMap<String, (std::any::TypeId, sdl_runtime::Context, bool)>,",
             "    recording_ready: bool, recording_bytes: usize,",
             "    measurements: std::collections::HashSet<String>,"]
    from ose.modeling import rust_inputs
    lines += rust_inputs.context_fields(node)
    for port in node["outputs"]:
        lines.append(f"    output_{port}: sdl_runtime::Context,")
    for alias, desc in node.get('parameter_bindings', {}).items():
        module, symbol = types[desc['root']]
        lines.append(f'    parameter_codec_{alias}: sdl_runtime::Context,')
        if 'read' in desc['access']:
            lines.append(f'    parameter_value_{alias}: Option<crate::types::{module}::{symbol}>,')
    lines += ["}", "impl Context {",
              "    pub fn time_ns(&self) -> u64 { self.tick.time_ns }",
              "    pub fn tick_index(&self) -> u64 { self.tick.tick_index }",
              "    pub fn now(&self) -> f64 { self.time_ns() as f64 / 1_000_000_000.0 }"]
    lines += r'''
    pub fn logf(&self, level: sdl_ose_component::LogLevel, logger: &str, arguments: std::fmt::Arguments<'_>) -> Result {
        sdl_ose_component::logf(level, logger, arguments)?;
        Ok(())
    }
    pub fn declare_measurement(&mut self, name: &str) -> Result {
        if self.recording_ready || !self.parameter_writes || self.measurements.contains(name) || self.measurements.len() >= 128 {
            return Err("measurement declarations require on_start and unique names (max 128)".into());
        }
        sdl_ose_component::declare_measurement(name)?;
        self.measurements.insert(name.to_owned());
        Ok(())
    }
    pub fn measure(&mut self, name: &str, value: f64) -> Result {
        if !self.active || !self.measurements.contains(name) {
            return Err("measurement requires a declared name and an active tick callback".into());
        }
        sdl_ose_component::measure(name, value)?;
        Ok(())
    }
    pub fn declare_recording_point<T: SdlMessage>(&mut self, name: &str, description: &str) -> Result {
        if self.recording_ready || self.recording.contains_key(name) || self.recording.len() >= 128 {
            return Err("recording declarations require initialization and unique names".into());
        }
        let catalogue = codec(sdl_runtime::description(&[T::type_info()]))?;
        let encoder = codec(sdl_runtime::prepare(&catalogue, &[T::type_info()]))?;
        let root = T::type_info().name;
        sdl_ose_component::declare_recording_point(name, root, &catalogue, description)?;
        self.recording.insert(name.to_owned(), (std::any::TypeId::of::<T>(), encoder, false));
        Ok(())
    }
    pub fn recording_enabled(&self, name: &str) -> Result<bool> {
        Ok(self.recording.get(name).ok_or("unknown recording point")?.2)
    }
    pub fn record<T: SdlMessage>(&mut self, name: &str, value: &T) -> Result<sdl_ose_component::RecordResult> {
        use sdl_ose_component::RecordResult;
        if !self.active { return Err("recording requires an active tick callback".into()); }
        let (typ, encoder, enabled) = self.recording.get(name).ok_or("unknown recording point")?;
        if *typ != std::any::TypeId::of::<T>() { return Err("wrong recording message type".into()); }
        if !enabled { return Ok(RecordResult::Disabled); }
        if self.recording_bytes + name.len() + 2 >= 256 * 1024 { return Ok(RecordResult::Dropped); }
        let payload = codec(sdl_runtime::encode(encoder, value))?;
        let size = name.len() + 2 + payload.len();
        if size > 256 * 1024 - self.recording_bytes { return Ok(RecordResult::Dropped); }
        sdl_ose_component::record(name, &payload)?;
        self.recording_bytes += size;
        Ok(RecordResult::Queued)
    }
    fn recording_update(&mut self, data: &[u8]) -> Result {
        for point in self.recording.values_mut() { point.2 = false; }
        if !data.is_empty() {
            for name in std::str::from_utf8(data)?.split('\0') {
                let point = self.recording.get_mut(name).ok_or("unknown active recording point")?;
                if point.2 { return Err("duplicate active recording point".into()); }
                point.2 = true;
            }
        }
        Ok(())
    }
'''.splitlines()
    for port, desc in node["outputs"].items():
        module, symbol = types[desc["root"]]
        lines += rust_inputs.output_methods(port, desc, types)
        continue
        lines += [f"    pub fn publish_{port}(&mut self, message: &crate::types::{module}::{symbol}) -> Result {{",
                  '        if !self.active { return Err("publication is only allowed inside a tick callback".into()); }',
                  f"        publish({rust_string(port)}, &codec(sdl_runtime::encode(&self.output_{port}, message))?)?;", "        Ok(())", "    }"]
    for alias, desc in node.get('parameter_bindings', {}).items():
        module, symbol = types[desc['root']]
        typ = f'crate::types::{module}::{symbol}'
        if 'read' in desc['access']:
            lines += [f'    pub fn parameters_{alias}(&self) -> &{typ} {{ self.parameter_value_{alias}.as_ref().expect("missing initial parameters") }}']
        if 'write' in desc['access']:
            lines += [f'    pub fn set_parameters_{alias}(&mut self, value: &{typ}) -> Result {{',
                      '        if !self.parameter_writes { return Err("parameter writes require an active callback".into()); }',
                      f'        sdl_ose_component::set_parameters({rust_string(alias)}, &codec(sdl_runtime::encode(&self.parameter_codec_{alias}, value))?)?;',
                      '        Ok(())', '    }']
    if node.get('parameter_bindings'):
        lines += ['    fn parameter_update(&mut self, event: &sdl_ose_component::Event, initial: bool) -> Result {',
                  '        if event.op != b\'U\' { return Err("expected parameter update".into()); }',
                  '        let data = input(event)?;', '        match data.port {']
        for alias, desc in node['parameter_bindings'].items():
            if 'read' not in desc['access']: continue
            lines += [f'            {rust_string(alias)} => {{',
                      f'                if data.catalogue != {rust_string(desc["catalogue"])} || (initial && self.parameter_value_{alias}.is_some()) {{ return Err("invalid parameter update".into()); }}',
                      f'                self.parameter_value_{alias} = Some(codec(sdl_runtime::decode(&self.parameter_codec_{alias}, data.payload))?);', '            }']
        lines += ['            _ => return Err("unknown readable parameter".into()),', '        }', '        Ok(())', '    }']
    lines += rust_inputs.context_methods(node, types)
    lines.append("}")
    return _finish_rust(name, handler, sdl_root, destination, source, types,
                        rust_inputs.runtime(name, node, types, lines))


def _finish_rust(name, handler, sdl_root, destination, source, types, lines):
    (source / "container.rs").write_text("\n".join(lines))
    (source / "main.rs").write_text("// Generated component entry point.\npub mod types;\npub mod container;\n" +
        f"#[path = {rust_string(str(handler))}]\nmod handler;\n" +
        "fn main() -> container::Result { container::run(handler::Handler::default()) }\n")
    sdk = sdk_root()
    manifest = destination / "Cargo.toml"
    binary = "ose-component-" + name
    manifest.write_text('[package]\nname="ose-container"\nversion="0.1.0"\nedition="2021"\nautobins=false\n' +
        '[[bin]]\nname=' + json.dumps(binary) + '\npath="src/main.rs"\n[workspace]\n[dependencies]\n' +
        'sdl-runtime={path=' + json.dumps(str(sdl_root / 'runtime/rust')) + '}\n' +
        'sdl-ose-component={path=' + json.dumps(str(sdk / 'rust')) + '}\n')
    target = destination.parents[1] / "rust-target"
    command = ["cargo", "build", "--offline", "--release", "--manifest-path", str(manifest), "--target-dir", str(target)]
    return {"types": types, "command": [str(target / "release" / binary)], "build": [command]}


def scaffold_native(node, handler, types, language):
    if node.get("execution_model") != 2:
        raise ValueError(f"{language.title()} containers require execution model 2")
    handler = Path(handler)
    if handler.exists():
        if not handler.is_file():
            raise ValueError(f"handler is not a file: {handler}")
        return
    if language == "c":
        lines = ['/* User callbacks. Never overwritten by the builder. */', '#include "component.h"']
        callbacks = []
        for port, desc in node["inputs"].items():
            if desc.get("mode", "push") == "pull":
                continue
            lines += [f"static int on_{port}(OseContext *ctx, const Input_{port} *input) {{",
                      "    (void)input;", '    return ose_fail(ctx, "implement the input callback");', "}"]
            callbacks.append(f".on_{port} = on_{port}")
        lines += ["const ComponentCallbacks component_callbacks = { " + (", ".join(callbacks) or "0") + " };", ""]
    else:
        lines = ["// User callbacks. Never overwritten by the builder.", "use crate::container::{Callbacks, Context, Result};",
                 "#[derive(Default)]", "pub struct Handler;", "impl Callbacks for Handler {"]
        from ose.modeling.rust_inputs import type_name
        for port, desc in node["inputs"].items():
            if desc.get("mode", "push") == "pull":
                continue
            typ = "()" if desc.get("kind") == "timer" else type_name(types, desc)
            lines += [f"    fn on_{port}(&mut self, _ctx: &mut Context, _input: &crate::container::InputReference<{typ}>) -> Result {{",
                      '        Err("implement the input callback".into())', "    }"]
        lines += ["}", ""]
    handler.parent.mkdir(parents=True, exist_ok=True)
    try:
        with handler.open("x") as stream:
            stream.write("\n".join(lines))
    except FileExistsError:
        pass
