"""Generate Ada 2012 typed callback containers; runtime codecs stay owned by SDL."""
import os
from pathlib import Path
import shlex
from ose.modeling.native_containers import sdk_root, merge_schemas
from ose.modeling.managed_containers import tool


def literal(text):
    # Ada source is UTF-8; catalogue strings must be the original UTF-8 octets.
    chunks = []
    for i in range(0, len(text), 160):
        part = text[i:i + 160]
        pieces = []
        for line in part.splitlines(keepends=True):
            raw = line.rstrip('\r\n')
            pieces.append('"' + raw.replace('"', '""') + '"')
            for c in line[len(raw):]:
                pieces.append(f'Character\'Val ({ord(c)})')
        chunks.append(' & '.join(pieces) or '""')
    return ' &\n      '.join(chunks) or '""'


def generate(name, node, handler, sdl_root, schemas, destination, identity):
    destination = Path(destination); sdl_root = Path(sdl_root)
    expected = identity.lower().replace('.', '-') + '.adb'
    if handler.name != expected:
        raise ValueError(f'Ada handler filename must follow lowercase GNAT unit naming: {expected}')
    if node.get('execution_model') != 2:
        raise ValueError('Ada containers require execution model 2')
    types = merge_schemas(name, 'ada', schemas, destination / 'types')
    api = 'Ose_' + name; stem = api.lower(); packages = sorted({p for p, _ in types.values()})
    if any(p.lower() in (api.lower(), identity.lower(), 'ose_component', 'ose_recording', 'ose_measurements', 'ose_inputs') for p in packages):
        raise ValueError('Ada schema package conflicts with component API')
    def typ(desc): return '.'.join(types[desc['root']])
    spec = ['-- Generated typed component API.', 'with Ada.Strings.Unbounded;', 'with Ada.Task_Identification;',
            'with Interfaces;', 'with SDL_Runtime;', 'with Ose_Component;', 'with Ose_Recording;', 'with Ose_Measurements;',
            'with Ose_Inputs;', *(f'with {p};' for p in packages), f'package {api} is',
            '   type Context is limited private;', '   function Name (Ctx : Context) return String;',
            '   function Config_JSON (Ctx : Context) return String;',
            '   function Time_Ns (Ctx : Context) return Interfaces.Unsigned_64;',
            '   function Tick_Index (Ctx : Context) return Interfaces.Unsigned_64;',
            '   function Now (Ctx : Context) return Long_Float;']
    from ose.modeling import ada_inputs
    spec += ada_inputs.declarations(node, typ)
    for port, desc in node['outputs'].items():
        spec += [f'   procedure Publish_{port} (Ctx : in out Context; Message : {typ(desc)}; Id : Ose_Inputs.Optional_Id := Ose_Inputs.No_Id);',
                 f'   procedure Publish_{port} (Ctx : in out Context; Message : {typ(desc)}; Id : Interfaces.Unsigned_64);']
    recording_symbols = {}
    for root in node.get('recording_catalogues', {}):
        symbol = types[root][1].removeprefix('T_')
        if symbol.lower() in {value.lower() for value in recording_symbols.values()}:
            raise ValueError('Ada recording helper name collision')
        recording_symbols[root] = symbol
        spec += [f'   procedure Declare_Recording_{symbol} (Ctx : in out Context; Name : String; Description : String := "");',
                 f'   function Record_{symbol} (Ctx : in out Context; Name : String; Value : {".".join(types[root])}) return Ose_Recording.Emit_Result;']
    spec += ['   function Recording_Enabled (Ctx : Context; Name : String) return Boolean;']
    spec += ['   procedure Declare_Measurement (Ctx : in out Context; Name : String);',
             '   procedure Log (Ctx : in out Context; Level : Ose_Component.Log_Level; Logger, Message : String);',
             '   procedure Measure (Ctx : in out Context; Name : String; Value : SDL_Runtime.Float64);']
    for alias, desc in node.get('parameter_bindings', {}).items():
        if 'read' in desc['access']:
            spec += [f'   function Parameters_{alias} (Ctx : Context) return {typ(desc)};']
        if 'write' in desc['access']:
            spec += [f'   procedure Set_Parameters_{alias} (Ctx : in out Context; Value : {typ(desc)});']
    spec += ['   procedure Run (Self : in out Handler\'Class);', 'private', '   type Context is limited record',
             '      Config : Ada.Strings.Unbounded.Unbounded_String;',
             '      Recordings : Ose_Recording.Registry;',
             '      Measurements : Ose_Measurements.Registry;',
             '      Time, Index : Interfaces.Unsigned_64 := 0;',
             '      Active : Boolean := False;', '      Owner : Ada.Task_Identification.Task_Id := Ada.Task_Identification.Null_Task_Id;']
    for port in node['outputs']:
        spec += [f'      Output_{port} : SDL_Runtime.Context;']
    for alias, desc in node.get('parameter_bindings', {}).items():
        spec += [f'      Parameter_Codec_{alias} : SDL_Runtime.Context;']
        if 'read' in desc['access']:
            spec += [f'      Parameter_Value_{alias} : {typ(desc)};', f'      Parameter_Present_{alias} : Boolean := False;']
    if node.get('parameter_bindings'):
        spec += ['      Parameter_Writes : Boolean := False;']
    spec += ada_inputs.context_fields(node)
    spec += ['   end record;', f'end {api};', '']
    (destination / (stem + '.ads')).write_text('\n'.join(spec))
    body = ['with Ada.Strings.Unbounded; use Ada.Strings.Unbounded;',
            'with Ose_Component; use Ose_Component;', f'package body {api} is',
            '   use type Ada.Task_Identification.Task_Id;']
    recording_catalogues = {text: index for index, text in enumerate(dict.fromkeys(node.get('recording_catalogues', {}).values()))}
    for text, index in recording_catalogues.items():
        body += [f'   Recording_Catalogue_{index} : constant String := {literal(text)};']
    body += [
            f'   function Name (Ctx : Context) return String is ({literal(name)});',
            '   function Config_JSON (Ctx : Context) return String is (To_String (Ctx.Config));',
            '   function Time_Ns (Ctx : Context) return Interfaces.Unsigned_64 is (Ctx.Time);',
            '   function Tick_Index (Ctx : Context) return Interfaces.Unsigned_64 is (Ctx.Index);',
            '   function Now (Ctx : Context) return Long_Float is (Long_Float (Ctx.Time) / 1.0E9);',
            '   procedure On_Start (Self : in out Handler; Ctx : in out Context) is begin null; end;',
            '   procedure On_Stop (Self : in out Handler; Ctx : in out Context) is begin null; end;']
    body += ['   use type Interfaces.Unsigned_64;', '   use type Interfaces.Unsigned_32;', '   use type Ose_Inputs.Event_Kind;']
    body += ada_inputs.callbacks(node) + ada_inputs.publications(node, types, typ)
    body += ['   function Recording_Enabled (Ctx : Context; Name : String) return Boolean is',
             '     (Ose_Recording.Is_Enabled (Ctx.Recordings, Name));']
    body += ['   procedure Log (Ctx : in out Context; Level : Ose_Component.Log_Level; Logger, Message : String) is',
             '      pragma Unreferenced (Ctx);', '   begin',
             '      Ose_Component.Log (Level, Logger, Message);', '   end Log;']
    body += ['   procedure Declare_Measurement (Ctx : in out Context; Name : String) is',
             '   begin',
             '      if Ctx.Owner /= Ada.Task_Identification.Current_Task then raise Program_Error with "measurement declaration requires on_start"; end if;',
             '      Ose_Measurements.Declare_Measurement (Ctx.Measurements, Name);',
             '   end Declare_Measurement;',
             '   procedure Measure (Ctx : in out Context; Name : String; Value : SDL_Runtime.Float64) is',
             '   begin',
             '      if not Ctx.Active or else Ctx.Owner /= Ada.Task_Identification.Current_Task then raise Program_Error with "measurement requires the active input callback"; end if;',
             '      Ose_Measurements.Measure (Ctx.Measurements, Name, Value);',
             '   end Measure;']
    for root, symbol in recording_symbols.items():
        package, value_type = types[root]
        text = node['recording_catalogues'][root]
        body += [f'   procedure Declare_Recording_{symbol} (Ctx : in out Context; Name : String; Description : String := "") is',
                 '   begin',
                 '      if Ctx.Owner /= Ada.Task_Identification.Current_Task then raise Program_Error with "recording declaration requires on_start"; end if;',
                 f'      Ose_Recording.Declare_Point (Ctx.Recordings, Name, {literal(root)}, Recording_Catalogue_{recording_catalogues[text]}, Description);',
                 '   end;',
                 f'   function Record_{symbol} (Ctx : in out Context; Name : String; Value : {package}.{value_type}) return Ose_Recording.Emit_Result is',
                 '   begin',
                 '      if not Ctx.Active or else Ctx.Owner /= Ada.Task_Identification.Current_Task then raise Program_Error with "recording requires the active input callback"; end if;',
                 f'      Ose_Recording.Check_Type (Ctx.Recordings, Name, {literal(root)});',
                 '      if not Recording_Enabled (Ctx, Name) then return Ose_Recording.Disabled; end if;',
                 "      if Ctx.Recordings.Tick_Bytes + Name'Length + 2 >= 256 * 1024 then return Ose_Recording.Dropped; end if;",
                 f'      return Ose_Recording.Emit (Ctx.Recordings, Name, {package}.Encode (Ctx.Recordings.Points.Constant_Reference (Name).Encoder, Value));',
                 '   end;']
    for alias, desc in node.get('parameter_bindings', {}).items():
        package = types[desc['root']][0]
        if 'read' in desc['access']:
            body += [f'   function Parameters_{alias} (Ctx : Context) return {typ(desc)} is (Ctx.Parameter_Value_{alias});']
        if 'write' in desc['access']:
            body += [f'   procedure Set_Parameters_{alias} (Ctx : in out Context; Value : {typ(desc)}) is', '   begin',
                     '      if not Ctx.Parameter_Writes or else Ctx.Owner /= Ada.Task_Identification.Current_Task then raise Program_Error with "parameter writes require an active callback"; end if;',
                     f'      Set_Parameters ({literal(alias)}, {package}.Encode (Ctx.Parameter_Codec_{alias}, Value));', '   end;']
    body += ada_inputs.context_methods(node, types, typ) + ada_inputs.runtime(node, types, api)
    (destination / (stem + '.adb')).write_text('\n'.join(body))
    return _finish(destination, sdl_root, handler, identity, api, types)


def _finish(destination, sdl_root, handler, identity, api, types):
    (destination / 'main.adb').write_text(f'''with {api};
with {identity};
with Ada.Exceptions;
with Ada.Text_IO;
with Ada.Command_Line;
procedure Main is
   Self : {identity}.Object;
begin
   {api}.Run (Self);
exception when Error : others =>
   Ada.Text_IO.Put_Line (Ada.Text_IO.Standard_Error, Ada.Exceptions.Exception_Information (Error));
   Ada.Command_Line.Set_Exit_Status (Ada.Command_Line.Failure);
end Main;
''')
    objects = destination / 'obj'; objects.mkdir(exist_ok=True)
    sdk = sdk_root(); runtime = sdl_root / 'runtime'
    c_sources = [runtime / 'c' / (n + '.c') for n in ('type_engine', 'type_registry', 'type_codec', 'sdl_wire', 'sdl_dynamic', 'type_display')]
    c_sources += [runtime / 'ada/sdl_ada_bridge.c', sdk / 'c/ose_component.c']
    commands, linked = [], []
    for i, src in enumerate(c_sources):
        obj = objects / f'bridge_{i}.o'; linked.append(str(obj))
        commands.append(tool('CC', 'cc') + ['-std=c99', '-O2', '-I' + str(runtime / 'c'), '-I' + str(sdk / 'c'), '-c', str(src), '-o', str(obj)])
    flags = shlex.split(os.environ.get('ADA_FLAGS', '-gnat2012 -gnata -O2'))
    rts = ['--RTS=' + os.environ['ADA_RTS']] if os.environ.get('ADA_RTS') else []
    # GNAT timestamps have second precision: rapid regeneration can otherwise
    # reuse a body compiled against an older spec and leave callbacks undefined.
    commands.append(tool('GNATMAKE', 'gnatmake') + flags + rts + ['-f', '-D', str(objects), '-I' + str(destination), '-I' + str(destination / 'types'),
                    '-I' + str(handler.parent), '-I' + str(runtime / 'ada'), '-I' + str(sdk / 'ada'), str(destination / 'main.adb'), '-o', str(destination / 'component'), '-largs'] + linked + ['-lm'])
    return {'types': types, 'command': [str(destination / 'component')], 'build': commands}


def scaffold(node, container):
    if node.get('execution_model') != 2:
        raise ValueError('Ada containers require execution model 2')
    handler = Path(container['handler']); spec = handler.with_suffix('.ads')
    if handler.exists() and spec.exists(): return
    if handler.exists() or spec.exists(): raise ValueError('Ada handler requires both .ads and .adb; refusing to overwrite a partial pair')
    name = Path(node['command'][0]).parent.name; api = 'Ose_' + name; unit = container['unit']
    lines = [f'with {api};', f'package {unit} is', f'   type Object is new {api}.Handler with null record;']
    bodies = [f'package body {unit} is']
    for port, desc in node['inputs'].items():
        if desc.get('mode', 'push') == 'pull':
            continue
        signature = f'procedure On_{port} (Self : in out Object; Ctx : in out {api}.Context; Input : {api}.Input_{port})'
        lines += ['   overriding ' + signature + ';']
        placeholder = '      raise Program_Error with "Implement input processing";'
        bodies += ['   overriding ' + signature + ' is', '   begin', '      -- Implement message processing here.', placeholder, '   end;']
    lines += [f'end {unit};', '']; bodies += [f'end {unit};', '']
    handler.parent.mkdir(parents=True, exist_ok=True)
    # GNAT requires parent specs for child handler units.
    parts = unit.split('.')
    for i in range(1, len(parts)):
        parent = '.'.join(parts[:i]); path = handler.parent / (parent.lower().replace('.', '-') + '.ads')
        if not path.exists(): path.write_text(f'package {parent} is\nend {parent};\n')
    spec.write_text('\n'.join(lines)); handler.write_text('\n'.join(bodies))
