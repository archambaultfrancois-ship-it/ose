"""Verify installed CLI/SDK assets and CH/2 execution outside the source path.

TEST_LANGUAGES selects build toolchains, as for make check; all are used by default.
"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile

SDL = Path(os.environ.get('SDL_ROOT', Path(__file__).resolve().parents[2] / "sdl")).resolve()
LANGUAGES = set(os.environ.get('TEST_LANGUAGES', 'python,c,rust,java,ada').split(','))
if not LANGUAGES or LANGUAGES - {'python', 'c', 'rust', 'java', 'ada'}:
    raise ValueError('unknown TEST_LANGUAGES entry')
PROGRAM = r'''
import asyncio, importlib.metadata, json, os, pathlib, shutil, subprocess, sys
if os.environ.get("OSE_INSTALLED_TARGET"):
    sys.path.insert(0, os.environ["OSE_INSTALLED_TARGET"])
    os.environ["PYTHONPATH"] = os.environ["OSE_INSTALLED_TARGET"]
else:
    os.environ.pop("PYTHONPATH", None)
import ose
from ose.modeling.compiler import build
from ose.modeling.native_containers import sdk_root
distribution = importlib.metadata.distribution('ose')
assert distribution.metadata['Name'] == 'ose'
assert [(entry.name, entry.value) for entry in distribution.entry_points
        if entry.group == 'console_scripts'] == [('ose', 'ose.cli:main')]
command = (str(pathlib.Path(os.environ['OSE_INSTALLED_TARGET']) / 'bin/ose')
           if os.environ.get('OSE_INSTALLED_TARGET') else shutil.which('ose'))
assert command, 'installed ose command not found'
assert subprocess.check_output([command, '--help'], text=True).startswith('usage: ose ')
assert pathlib.Path(ose.__file__).resolve().is_relative_to(pathlib.Path(distribution.locate_file('')).resolve())
root = pathlib.Path.cwd()
sdk = sdk_root()
assert 'share/ose/sdk' in str(sdk), sdk
for asset in ('c/ose_inputs.h', 'c/ose_transport.h', 'rust/src/inputs.rs', 'rust/src/transport.rs',
              'java/ose_transport_jni.c', 'java/org/sdlose/NativeTransport.java',
              'java/org/sdlose/InputProtocol.java', 'ada/ose_inputs.ads', 'ada/ose_inputs.adb',
              'java/org/sdlose/Component.java', 'ada/ose_component.ads',
              'ada/ose_measurements.ads', 'ada/ose_measurements.adb',
              'client/java/ose_jni.c', 'client/ada/ose_client-typed.adb',
              'client/c/ose_parameters.h', 'client/rust/src/parameters.rs',
              'client/rust/src/parameters_tls.rs',
              'client/java/org/sdlose/client/ParameterClient.java',
              'client/ada/ose_parameters.ads', 'client/ada/ose_parameters.adb',
              'client/ada/ose_parameters-typed.ads', 'client/ada/ose_parameters-typed.adb'):
    assert (sdk / asset).is_file(), asset
(root / 'probe.sdl').write_text('message Empty {}')
(root / 'settings.sdl').write_text('message Settings { uint32 limit; }')
languages = sys.argv[2].split(',')
containers = {
    'python': {'language': 'python', 'handler': 'handler.py'},
    'c': {'language': 'c', 'handler': 'handler.c'},
    'rust': {'language': 'rust', 'handler': 'handler.rs'},
    'java': {'language': 'java', 'handler': 'Handler.java', 'class': 'Handler'},
    'ada': {'language': 'ada', 'handler': 'handler_ada.adb', 'unit': 'Handler_Ada'},
}
handlers = {
    'python': """def on_clock(ctx, input):
    assert input.timed_out and input.message is None
""",
    'c': """#include "component.h"
static int on_clock(OseContext *ctx, const Input_clock *input) {
    (void)ctx; (void)input; return 1;
}
const ComponentCallbacks component_callbacks = { .on_clock=on_clock };
""",
    'rust': """use crate::container::{Callbacks, Context, InputReference, Result};
#[derive(Default)] pub struct Handler;
impl Callbacks for Handler {
    fn on_clock(&mut self, _ctx: &mut Context, _input: &InputReference<()>) -> Result { Ok(()) }
}
""",
    'java': """import org.sdlose.generated.java.API;
import org.sdlose.InputProtocol.Reference;
public final class Handler extends API.Handler {
    @Override public void on_clock(API.Context ctx, Reference<Void> input) {}
}
""",
    'ada': """package body Handler_Ada is
    overriding procedure On_clock (Self : in out Object; Ctx : in out Ose_ada.Context;
                                   Input : Ose_ada.Input_clock) is
    begin
        null;
    end On_clock;
end Handler_Ada;
""",
}
for language in languages:
    (root / containers[language]['handler']).write_text(handlers[language])
if 'ada' in languages:
    (root / 'handler_ada.ads').write_text("""with Ose_ada;
package Handler_Ada is
    type Object is new Ose_ada.Handler with null record;
    overriding procedure On_clock (Self : in out Object; Ctx : in out Ose_ada.Context;
                                   Input : Ose_ada.Input_clock);
end Handler_Ada;
""")
model = {'version': 2, 'schemas': 'probe.sdl', 'components': {
    name: {'inputs': {'clock': {'kind': 'timer', 'timer': {'mode': 'one_shot', 'delay': .01}}}, 'outputs': {},
           'parameter_bindings': {'settings': {'path': 'shared/settings', 'access': 'read_write'}}}
    for name in languages}, 'subscriptions': [],
    'shared_parameters': {'shared/settings': {'schema': 'settings.sdl', 'root': 'Settings',
                                             'initial': {'limit': 42}}}}
deployment = {'version': 1, 'components': {
    name: {'container': containers[name]} for name in languages}}
(root / 'model.json').write_text(json.dumps(model)); (root / 'deployment.json').write_text(json.dumps(deployment))
plan_path = build(root / 'model.json', root / 'deployment.json', sys.argv[1], root / 'build')
from ose.execution.simulation import Simulation
engine = Simulation(json.loads(plan_path.read_text()), .02, workers=len(languages), trace=root / 'trace.jsonl')
summary = asyncio.run(engine.execute())
assert summary['ticks'] == len(languages), summary
assert summary['stop_reason'] == 'quiescent', summary
assert all(process.returncode == 0 for process in engine.processes.values())
activations = [json.loads(line) for line in (root / 'trace.jsonl').read_text().splitlines()
               if json.loads(line).get('event') == 'tick']
assert {row['component'] for row in activations} == set(languages), activations
assert all(row['activation_event'] == 'timeout' and row['time_ns'] == 10_000_000
           for row in activations), activations
from ose.modeling.client_build import build_java
from ose.modeling.ada_client_build import build_ada
if 'java' in languages:
    build_java(sys.argv[1], root / 'java-client')
if 'ada' in languages:
    build_ada(sys.argv[1], root / 'ada-client')
print('Installed ose package, CLI, SDK builds and CH/2 timer callbacks passed: ' + ', '.join(languages))
'''
with tempfile.TemporaryDirectory() as directory:
    # -I removes PYTHONPATH and the current directory from imports.
    subprocess.run([sys.executable, '-I', '-c', PROGRAM, str(SDL), ','.join(sorted(LANGUAGES))], cwd=directory, check=True)
