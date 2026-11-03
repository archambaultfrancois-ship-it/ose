"""Generated SDK log deposits, including initialization and shutdown."""
import json
from pathlib import Path
from ose.modeling.compiler import build
from ose.execution.process import start_component, stop_component, tick_events


SOURCES = {
    'python': ('source.py', '''def on_start(ctx):
    ctx.log('INFO', 'started', 'lifecycle')
def on_tick(ctx):
    ctx.logf('FATAL', '%s', 'work', logger='detection')
    ctx.logf('WARNING', '%s', 'é' * 5000, logger='long')
def on_stop(ctx):
    ctx.log('INFO', 'stopped', 'lifecycle')
'''),
    'c': ('source.c', '''#include "component.h"
static int start(OseContext *ctx) { return ose_logf(ctx, OSE_LOG_INFO, "lifecycle", "started"); }
static int tick(OseContext *ctx) {
    return ose_logf(ctx, OSE_LOG_FATAL, "detection", "%s", "work") &&
           ose_logf(ctx, OSE_LOG_WARNING, "long", "%09000u", 1U);
}
static int stop(OseContext *ctx) { return ose_logf(ctx, OSE_LOG_INFO, "lifecycle", "stopped"); }
const ComponentCallbacks component_callbacks = { .on_start=start, .on_tick=tick, .on_stop=stop };
'''),
    'rust': ('source.rs', '''use crate::container::{Callbacks, Context, Result};
use sdl_ose_component::LogLevel;
#[derive(Default)] pub struct Handler;
impl Callbacks for Handler {
    fn on_start(&mut self, ctx: &mut Context) -> Result { ctx.logf(LogLevel::Info, "lifecycle", format_args!("{}", "started")) }
    fn on_tick(&mut self, ctx: &mut Context) -> Result {
        ctx.logf(LogLevel::Fatal, "detection", format_args!("{}", "work"))?;
        ctx.logf(LogLevel::Warning, "long", format_args!("{:09000}", 1))
    }
    fn on_stop(&mut self, ctx: &mut Context) -> Result { ctx.logf(LogLevel::Info, "lifecycle", format_args!("stopped")) }
}
'''),
    'java': ('Source.java', '''import org.sdlose.generated.source.API;
import org.sdlose.Component.LogLevel;
public final class Source extends API.Handler {
    public void onStart(API.Context ctx) throws Exception {
        try { ctx.logf(LogLevel.INFO, "test", "%d", "wrong"); throw new AssertionError("format accepted"); }
        catch (java.util.IllegalFormatException expected) { }
        ctx.logf(LogLevel.INFO, "lifecycle", "%s", "started");
    }
    public void onTick(API.Context ctx, API.Inputs inputs) throws Exception {
        ctx.logf(LogLevel.FATAL, "detection", "%s", "work");
        ctx.logf(LogLevel.WARNING, "long", "%s", new String(new char[5000]).replace((char)0, 'é'));
    }
    public void onStop(API.Context ctx) throws Exception { ctx.logf(LogLevel.INFO, "lifecycle", "stopped"); }
}
'''),
    'ada': ('source_handler.adb', '''with Ose_Component;
package body Source_Handler is
    overriding procedure On_Start (Self : in out Object; Ctx : in out Ose_source.Context) is
    begin Ose_source.Log (Ctx, Ose_Component.Info, "lifecycle", "started"); end;
    overriding procedure On_Tick (Self : in out Object; Ctx : in out Ose_source.Context; Selected : Ose_source.Inputs) is
    begin
        Ose_source.Log (Ctx, Ose_Component.Fatal, "detection", "work");
        Ose_source.Log (Ctx, Ose_Component.Warning, "long", String'(1 .. 9000 => 'x'));
    end;
    overriding procedure On_Stop (Self : in out Object; Ctx : in out Ose_source.Context) is
    begin Ose_source.Log (Ctx, Ose_Component.Info, "lifecycle", "stopped"); end;
end Source_Handler;
'''),
}


def build_component(root, language, sdl_root):
    root = Path(root); root.mkdir(parents=True, exist_ok=True)
    name, source = SOURCES[language]
    (root / name).write_text(source)
    (root / 'empty.sdl').write_text('message Empty {}')
    container = dict(language=language, handler=name)
    if language == 'java': container['class'] = 'Source'
    if language == 'ada':
        container['unit'] = 'Source_Handler'
        (root / 'source_handler.ads').write_text('''with Ose_source;
package Source_Handler is
    type Object is new Ose_source.Handler with null record;
    overriding procedure On_Start (Self : in out Object; Ctx : in out Ose_source.Context);
    overriding procedure On_Tick (Self : in out Object; Ctx : in out Ose_source.Context; Selected : Ose_source.Inputs);
    overriding procedure On_Stop (Self : in out Object; Ctx : in out Ose_source.Context);
end Source_Handler;
''')
    model = dict(version=1, schemas='empty.sdl', components={'source': dict(inputs={}, outputs={},
                 trigger=dict(mode='periodic', period_ms=50))}, subscriptions=[])
    deployment = dict(version=1, components={'source': dict(container=container)})
    for name, doc in (('model', model), ('deployment', deployment)):
        (root / (name + '.json')).write_text(json.dumps(doc))
    path = build(root / 'model.json', root / 'deployment.json', sdl_root, root / 'build')
    return json.loads(path.read_text())


async def check_component(test, plan):
    node = plan['components']['source']
    process, _ = await start_component(node)
    try:
        test.assertEqual([item async for item in tick_events(process, node, {}, 123, 1)], [])
    finally:
        await stop_component(process)
    rows = process.logs.drain()
    test.assertEqual([row['message'] for row in rows[:2]], ['started', 'work'])
    test.assertEqual(rows[-1]['message'], 'stopped')
    test.assertEqual([row['level'] for row in rows], ['INFO', 'FATAL', 'WARNING', 'INFO'])
    test.assertTrue(rows[2]['message'].endswith('… [truncated]'))
    test.assertLessEqual(len(rows[2]['message'].encode()), 8192)
