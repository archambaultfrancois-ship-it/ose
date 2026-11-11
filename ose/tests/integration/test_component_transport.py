"""Standard streams and subprocesses cannot corrupt the private CH/2 channel."""
import asyncio
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

from ose.execution import ipc
from ose.execution.process import start_component, stop_component
from ose.modeling.compiler import build
from tests.component_driver import run_component, timer_frame

SDL_ROOT = os.environ.get('SDL_ROOT')
LANGUAGES = set(os.environ.get('TEST_LANGUAGES', 'python,c,rust,java,ada').split(','))

SOURCES = {
    'python': ('handler.py', '''import os, subprocess, sys
os.write(1, b"before\\n")
def on_start(ctx):
    print("start", flush=True)
    subprocess.run([sys.executable, "-c", "print('child')"], check=True)
def on_clock(ctx, input):
    os.write(1, b"tick\\n")
    os.write(2, b"diagnostic\\n")
def on_stop(ctx):
    print("stop", flush=True)
'''),
    'c': ('handler.c', '''#include "component.h"
#include <stdio.h>
#include <unistd.h>
#include <sys/wait.h>
__attribute__((constructor)) static void before(void) { puts("before"); fflush(stdout); }
static int start(OseContext *ctx) {
    (void)ctx; puts("start"); fflush(stdout);
    pid_t pid = fork();
    if (pid < 0) return 0;
    if (!pid) { execl("/bin/sh", "sh", "-c", "echo child", (char *)0); _exit(127); }
    int status; return waitpid(pid, &status, 0) == pid && status == 0;
}
static int tick(OseContext *ctx, const Input_clock *input) {
    (void)ctx; (void)input; puts("tick"); fflush(stdout); fputs("diagnostic\\n", stderr); return 1;
}
static int stop(OseContext *ctx) { (void)ctx; puts("stop"); return 1; }
const ComponentCallbacks component_callbacks = {.on_start=start, .on_clock=tick, .on_stop=stop};
'''),
    'rust': ('handler.rs', '''use crate::container::{Callbacks, Context, InputReference, Result};
pub struct Handler;
impl Default for Handler { fn default() -> Self { println!("before"); Self } }
impl Callbacks for Handler {
    fn on_start(&mut self, _ctx: &mut Context) -> Result {
        println!("start");
        if !std::process::Command::new("sh").args(["-c", "echo child"]).status()?.success() { return Err("child failed".into()); }
        Ok(())
    }
    fn on_clock(&mut self, _ctx: &mut Context, _input: &InputReference<()>) -> Result {
        println!("tick"); eprintln!("diagnostic"); Ok(())
    }
    fn on_stop(&mut self, _ctx: &mut Context) -> Result { println!("stop"); Ok(()) }
}
'''),
    'java': ('Handler.java', '''import org.sdlose.generated.probe.API;
import org.sdlose.InputProtocol.Reference;
public final class Handler extends API.Handler {
    static { System.out.println("before"); }
    @Override public void onStart(API.Context ctx) throws Exception {
        System.out.println("start");
        if (new ProcessBuilder("sh", "-c", "echo child").inheritIO().start().waitFor() != 0)
            throw new Exception("child failed");
    }
    @Override public void on_clock(API.Context ctx, Reference<Void> input) {
        System.out.println("tick"); System.err.println("diagnostic");
    }
    @Override public void onStop(API.Context ctx) { System.out.println("stop"); }
}
'''),
    'ada': ('handler_ada.adb', '''with Ada.Text_IO;
package body Handler_Ada is
    overriding procedure On_Start (Self : in out Object; Ctx : in out Ose_probe.Context) is
    begin Ada.Text_IO.Put_Line ("start"); end On_Start;
    overriding procedure On_clock (Self : in out Object; Ctx : in out Ose_probe.Context;
                                   Input : Ose_probe.Input_clock) is
    begin
        Ada.Text_IO.Put_Line ("tick");
        Ada.Text_IO.Put_Line (Ada.Text_IO.Standard_Error, "diagnostic");
    end On_clock;
    overriding procedure On_Stop (Self : in out Object; Ctx : in out Ose_probe.Context) is
    begin Ada.Text_IO.Put_Line ("stop"); end On_Stop;
begin Ada.Text_IO.Put_Line ("before");
end Handler_Ada;
'''),
}


@unittest.skipUnless(SDL_ROOT, 'set SDL_ROOT')
class ComponentTransportTests(unittest.TestCase):
    def check_language(self, language):
        if language not in LANGUAGES:
            self.skipTest('language omitted by TEST_LANGUAGES')
        tool = {'python': sys.executable, 'c': 'cc', 'rust': 'cargo', 'java': 'javac', 'ada': 'gnatmake'}[language]
        if not shutil.which(tool):
            self.skipTest(tool + ' unavailable')
        with tempfile.TemporaryDirectory(prefix='ose socketpair ') as temporary:
            root = Path(temporary)
            name, source = SOURCES[language]
            # Termux has no /bin/sh.
            source = source.replace('execl("/bin/sh",', 'execl(' + json.dumps(shutil.which('sh')) + ',')
            (root / name).write_text(source)
            (root / 'value.sdl').write_text('message Empty {}')
            if language == 'ada':
                (root / 'handler_ada.ads').write_text('''with Ose_probe;
package Handler_Ada is
    type Object is new Ose_probe.Handler with null record;
    overriding procedure On_Start (Self : in out Object; Ctx : in out Ose_probe.Context);
    overriding procedure On_clock (Self : in out Object; Ctx : in out Ose_probe.Context; Input : Ose_probe.Input_clock);
    overriding procedure On_Stop (Self : in out Object; Ctx : in out Ose_probe.Context);
end Handler_Ada;
''')
            container = dict(language=language, handler=name)
            if language == 'java': container['class'] = 'Handler'
            if language == 'ada': container['unit'] = 'Handler_Ada'
            model = dict(version=2, schemas='value.sdl', components=dict(probe=dict(
                inputs=dict(clock=dict(kind='timer', timer=dict(mode='periodic', delay=.01))), outputs={})), subscriptions=[])
            deployment = dict(version=1, components=dict(probe=dict(container=container)))
            (root / 'model.json').write_text(json.dumps(model))
            (root / 'deployment.json').write_text(json.dumps(deployment))
            path = build(root / 'model.json', root / 'deployment.json', SDL_ROOT, root / 'build')
            node = json.loads(path.read_text())['components']['probe']
            result = run_component(node['command'], input=ipc.frame('I', json.dumps(node).encode()) + timer_frame(1) + ipc.frame('X'),
                                   cwd=node['cwd'], env=dict(os.environ, **node.get('env', {})))
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertEqual(result.stdout, ipc.frame('R', b'\2') + ipc.frame('F'))
            expected = b'before\nstart\n' + (b'child\n' if language != 'ada' else b'') + b'tick\nstop\n'
            self.assertEqual(result.application_stdout, expected)
            self.assertIn(b'diagnostic\n', result.stderr)

    def test_python(self): self.check_language('python')
    def test_c(self): self.check_language('c')
    def test_rust(self): self.check_language('rust')
    def test_java(self): self.check_language('java')
    def test_ada(self): self.check_language('ada')


class TransportLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_standard_input_and_output_are_inherited(self):
        # Launch the supervisor with redirected streams, then exercise the real
        # launcher so this also catches accidental subprocess.PIPE settings.
        source = '''import asyncio, sys
from ose.execution.process import start_component, stop_component
async def main():
    child = """import sys
from ose.execution.component_io import SessionIO
with SessionIO() as session:
    print('application: ' + sys.stdin.readline().strip(), flush=True)
    print('diagnostic', file=sys.stderr, flush=True)
    session.send('R', b'\\x02')
    assert session.receive()[0] == 'X'
"""
    process = await start_component(dict(execution_model=2,
        command=[sys.executable, '-c', child], cwd='.'), inputs=object())
    await stop_component(process)
    assert process.returncode == 0
asyncio.run(main())
'''
        supervisor = await asyncio.create_subprocess_exec(sys.executable, '-c', source,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            output, errors = await asyncio.wait_for(supervisor.communicate(b'hello\n'), 10)
            self.assertEqual(supervisor.returncode, 0, errors.decode())
            self.assertEqual(output, b'application: hello\n')
            self.assertEqual(errors, b'diagnostic\n')
        finally:
            if supervisor.returncode is None:
                supervisor.kill()
                await supervisor.communicate()

    async def test_repeated_start_stop_does_not_leak_descriptors(self):
        if not Path('/proc/self/fd').exists(): self.skipTest('requires procfs')
        source = "from ose.execution.component_io import SessionIO\nwith SessionIO() as s:\n s.send('R', b'\\2')\n assert s.receive()[0] == 'X'\n"
        before = len(list(Path('/proc/self/fd').iterdir()))
        for _ in range(10):
            process = await start_component(dict(execution_model=2, command=[sys.executable, '-c', source], cwd='.'), inputs=object())
            await stop_component(process)
            self.assertEqual(process.returncode, 0)
        self.assertEqual(len(list(Path('/proc/self/fd').iterdir())), before)

    async def test_cancellation_during_spawn_reaps_the_child(self):
        from unittest.mock import patch
        spawn = asyncio.create_subprocess_exec
        spawned, release = asyncio.Event(), asyncio.Event()
        children = []

        async def delayed_spawn(*args, **kwargs):
            process = await spawn(*args, **kwargs)
            children.append(process)
            spawned.set()
            await release.wait()
            return process

        with patch('ose.execution.process.asyncio.create_subprocess_exec', delayed_spawn):
            startup = asyncio.create_task(start_component(dict(execution_model=2,
                command=[sys.executable, '-c', 'import time; time.sleep(30)'], cwd='.'), inputs=object()))
            await asyncio.wait_for(spawned.wait(), 5)
            startup.cancel()
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(startup, 5)
        self.assertIsNotNone(children[0].returncode)

    async def test_spawn_failure_does_not_leak_descriptors(self):
        if not Path('/proc/self/fd').exists(): self.skipTest('requires procfs')
        before = len(list(Path('/proc/self/fd').iterdir()))
        for _ in range(10):
            with self.assertRaises(FileNotFoundError):
                await start_component(dict(execution_model=2, command=['/no/ose-component'], cwd='.'), inputs=object())
        await asyncio.sleep(0)
        self.assertEqual(len(list(Path('/proc/self/fd').iterdir())), before)

    async def test_child_exec_does_not_keep_the_socket_open(self):
        # A descendant deliberately outlives its parent, with close_fds disabled.
        # EOF must still arrive immediately because the SDK sets CLOEXEC.
        source = '''import os, subprocess, sys
from ose.execution.component_io import SessionIO
s = SessionIO()
s.send('R', b'\\2')
subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(2)'], close_fds=False)
os._exit(0)
'''
        process = await start_component(dict(execution_model=2, command=[sys.executable, '-c', source], cwd='.'), inputs=object())
        try:
            self.assertEqual(await asyncio.wait_for(process.ipc_reader.read(), 1), b'')
        finally:
            await stop_component(process)

    async def test_leaked_descendant_and_full_socket_cannot_block_shutdown(self):
        source = '''import os, subprocess, sys
from ose.execution.component_io import SessionIO
s = SessionIO()
fd = s.channel.fileno()
# Deliberately defeat CLOEXEC to model a broken custom component.
subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(4)'], pass_fds=(fd,))
s.send('R', b'\\2')
os._exit(0)
'''
        process = await start_component(dict(execution_model=2, command=[sys.executable, '-c', source], cwd='.'), inputs=object())
        await process.wait()
        sending = asyncio.create_task(ipc.send(process.ipc_writer, 'D', b'x' * 900000))
        try:
            for _ in range(100):
                if process.ipc_writer.transport.get_write_buffer_size(): break
                await asyncio.sleep(.01)
            self.assertGreater(process.ipc_writer.transport.get_write_buffer_size(), 0)
        finally:
            sending.cancel()
            await asyncio.gather(sending, return_exceptions=True)
            await asyncio.wait_for(stop_component(process), 2.5)

    async def test_shutdown_is_bounded_when_an_endpoint_is_leaked(self):
        from unittest.mock import patch
        spawn = asyncio.create_subprocess_exec
        leaked = []

        async def leaking_spawn(*args, **kwargs):
            # Keep another copy of the child's endpoint, as a fork without exec
            # would, so reaping the component cannot produce transport EOF.
            leaked.append(os.dup(kwargs['pass_fds'][0]))
            return await spawn(*args, **kwargs)

        source = "from ose.execution.component_io import SessionIO\nwith SessionIO() as s:\n s.send('R', b'\\2')\n assert s.receive()[0] == 'X'\n"
        process = None
        try:
            with patch('ose.execution.process.asyncio.create_subprocess_exec', leaking_spawn):
                process = await start_component(dict(execution_model=2,
                    command=[sys.executable, '-c', source], cwd='.'), inputs=object())
            await asyncio.wait_for(stop_component(process), 3)
            self.assertEqual(process.returncode, 0)
            self.assertTrue(process.ipc_writer.is_closing())
        finally:
            for fd in leaked:
                os.close(fd)
            if process is not None and process.returncode is None:
                process.kill()
                await process.wait()

    async def test_component_socket_rejects_missing_and_standard_descriptors(self):
        from unittest.mock import patch
        from ose.execution.channel import component_socket
        for value in (None, '', '0', '1', '2', '-1', ' 3', 'abc'):
            with patch.dict(os.environ, {}, clear=True):
                if value is not None: os.environ['OSE_IPC_FD'] = value
                with self.assertRaisesRegex(ValueError, 'OSE_IPC_FD'):
                    component_socket()
