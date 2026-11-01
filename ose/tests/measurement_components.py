"""Small port-free measurement components exercising the five generated SDKs."""
import json
from pathlib import Path

from ose.execution.process import start_component, stop_component, tick_events
from ose.modeling.compiler import build


HANDLERS = {
    'python': ('source.py', '''def on_start(ctx):
    ctx.declare_measurement("moteur.temperature")
    ctx.declare_measurement("moteur.unpublished")
def on_tick(ctx):
    ctx.measure("moteur.temperature", ctx.tick_index + 0.5)
    ctx.measure("moteur.temperature", ctx.tick_index + 1.0)
'''),
    'c': ('source.c', '''#include "component.h"
static int start(OseContext *ctx) {
    return ose_declare_measurement(ctx, "moteur.temperature") &&
           ose_declare_measurement(ctx, "moteur.unpublished");
}
static int tick(OseContext *ctx) {
    return ose_measure(ctx, "moteur.temperature", (double)ose_tick_index(ctx) + 0.5) &&
           ose_measure(ctx, "moteur.temperature", (double)ose_tick_index(ctx) + 1.0);
}
const ComponentCallbacks component_callbacks = { .on_start = start, .on_tick = tick };
'''),
    'rust': ('source.rs', '''use crate::container::{Callbacks, Context, Result};
#[derive(Default)] pub struct Handler;
impl Callbacks for Handler {
    fn on_start(&mut self, ctx: &mut Context) -> Result {
        ctx.declare_measurement("moteur.temperature")?;
        ctx.declare_measurement("moteur.unpublished")
    }
    fn on_tick(&mut self, ctx: &mut Context) -> Result {
        ctx.measure("moteur.temperature", ctx.tick_index() as f64 + 0.5)?;
        ctx.measure("moteur.temperature", ctx.tick_index() as f64 + 1.0)
    }
}
'''),
    'java': ('Source.java', '''import org.sdlose.generated.source.API;
public final class Source extends API.Handler {
    public void onStart(API.Context ctx) throws Exception {
        ctx.declareMeasurement("moteur.temperature");
        ctx.declareMeasurement("moteur.unpublished");
    }
    public void onTick(API.Context ctx, API.Inputs inputs) throws Exception {
        ctx.measure("moteur.temperature", ctx.tickIndex().doubleValue() + 0.5);
        ctx.measure("moteur.temperature", ctx.tickIndex().doubleValue() + 1.0);
    }
}
'''),
    'ada': ('source_handler.adb', '''with SDL_Runtime;
package body Source_Handler is
    overriding procedure On_Start (Self : in out Object; Ctx : in out Ose_source.Context) is
    begin
        Ose_source.Declare_Measurement (Ctx, "moteur.temperature");
        Ose_source.Declare_Measurement (Ctx, "moteur.unpublished");
    end;
    overriding procedure On_Tick (Self : in out Object; Ctx : in out Ose_source.Context; Selected : Ose_source.Inputs) is
        use type SDL_Runtime.Float64;
        Index : constant SDL_Runtime.Float64 := SDL_Runtime.Float64 (Ose_source.Tick_Index (Ctx));
    begin
        Ose_source.Measure (Ctx, "moteur.temperature", Index + 0.5);
        Ose_source.Measure (Ctx, "moteur.temperature", Index + 1.0);
    end;
end Source_Handler;
'''),
}


def build_component(root, language, sdl_root):
    root = Path(root); root.mkdir(parents=True, exist_ok=True)
    (root / 'empty.sdl').write_text('message Empty {}')
    name, source = HANDLERS[language]
    (root / name).write_text(source)
    container = dict(language=language, handler=name)
    if language == 'java': container['class'] = 'Source'
    if language == 'ada':
        container['unit'] = 'Source_Handler'
        (root / 'source_handler.ads').write_text('''with Ose_source;
package Source_Handler is
    type Object is new Ose_source.Handler with null record;
    overriding procedure On_Start (Self : in out Object; Ctx : in out Ose_source.Context);
    overriding procedure On_Tick (Self : in out Object; Ctx : in out Ose_source.Context; Selected : Ose_source.Inputs);
end Source_Handler;
''')
    model = dict(version=1, schemas='empty.sdl', components={'source': dict(inputs={}, outputs={},
                 trigger=dict(mode='periodic', period_ms=50))}, subscriptions=[])
    deployment = dict(version=1, components={'source': dict(container=container)})
    for name, doc in (('model', model), ('deployment', deployment)):
        (root / (name + '.json')).write_text(json.dumps(doc))
    path = build(root / 'model.json', root / 'deployment.json', sdl_root, root / 'build')
    return json.loads(path.read_text())


async def check_component(case, plan):
    node = plan['components']['source']
    for _ in range(2):  # A fresh generation must not restore the preceding values.
        process, _ = await start_component(node)
        try:
            case.assertEqual(process.measurements.snapshot(), {'moteur.temperature': None, 'moteur.unpublished': None})
            for index in (1, 2):
                outputs = [item async for item in tick_events(process, node, {}, index * 100, index)]
                case.assertEqual(outputs, [])
                case.assertEqual(process.measurements.snapshot(),
                                 {'moteur.temperature': index + 1.0, 'moteur.unpublished': None})
        finally:
            await stop_component(process)
