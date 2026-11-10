"""Replay an evolved recorded schema through a real generated callback container."""
import asyncio
import hashlib
import json
from pathlib import Path

from ose.codec.catalogue import CatalogueTypes
from ose.codec.sdl2 import SDL2
from ose.modeling.compiler import build
from ose.recording.replay import recording_reader, run


HANDLERS = {
    'python': ('sink.py', '''def on_input(ctx, input):
    message = input.message
    message.value += 1.0
    ctx.publish_output(message, id=input.app_id)
'''),
    'c': ('sink.c', '''#include "component.h"
static int on_input(OseContext *ctx, const Input_input *input) {
    Measurement result = *input->message;
    result.value += 1.0;
    return input->meta.has_app_id ? publish_output_with_id(ctx, &result, input->meta.app_id)
                                  : publish_output(ctx, &result);
}
const ComponentCallbacks component_callbacks = { .on_input = on_input };
'''),
    'rust': ('sink.rs', '''use crate::container::{Callbacks, Context, Result};
use crate::types::measurement::Measurement;
#[derive(Default)] pub struct Handler;
impl Callbacks for Handler {
    fn on_input(&mut self, ctx: &mut Context, input: &crate::container::InputReference<Measurement>) -> Result {
        let mut result = input.message.as_ref().ok_or("missing message")?.clone();
        result.value += 1.0;
        match input.app_id {
            Some(id) => ctx.publish_output_with_id(&result, id),
            None => ctx.publish_output(&result),
        }
    }
}
'''),
    'java': ('Sink.java', '''import org.sdlose.generated.sink.API;
import sdl.globaltypes.measurement.Measurement;
public final class Sink extends API.Handler {
    public void on_input(API.Context ctx, org.sdlose.InputProtocol.Reference<Measurement> input) throws Exception {
        Measurement message = input.message;
        message.value += 1.0;
        ctx.publish_output(message, input.appId);
    }
}
'''),
    'ada': ('sink_handler.adb', '''with SDL_measurement; with SDL_Runtime;
package body Sink_Handler is
    overriding procedure On_input (Self : in out Object; Ctx : in out Ose_sink.Context;
                                  Input : Ose_sink.Input_input) is
        use type SDL_Runtime.Float64;
        Result : SDL_measurement.T_Measurement := Input.Message;
    begin
        Result.F_value := Result.F_value + 1.0;
        Ose_sink.Publish_output (Ctx, Result, Input.Meta.App_Id);
    end;
end Sink_Handler;
'''),
}


async def check_generated_replay(case, root, sdl_root, language):
    root = Path(root); root.mkdir(parents=True, exist_ok=True)
    (root / 'measurement.sdl').write_text('message Measurement {1: required uint64 sequence;2: required fl64 value;}')
    handler, source = HANDLERS[language]
    (root / handler).write_text(source)
    if language == 'ada':
        (root / 'sink_handler.ads').write_text('''with Ose_sink;
package Sink_Handler is
    type Object is new Ose_sink.Handler with null record;
    overriding procedure On_input (Self : in out Object; Ctx : in out Ose_sink.Context;
                                  Input : Ose_sink.Input_input);
end Sink_Handler;
''')
    desc = dict(root='Measurement')
    model = dict(version=2, schemas='measurement.sdl', external_topics={'external.signal': desc},
        components={'sink': dict(inputs={'input': dict(desc, activates={'message': ['output']})}, outputs={'output': desc})},
        subscriptions=[dict(id='signal', from_topic='external.signal', to='sink.input')])
    container = dict(language=language, handler=handler)
    if language == 'java': container['class'] = 'Sink'
    if language == 'ada': container['unit'] = 'Sink_Handler'
    deployment = dict(version=1, components={'sink': dict(container=container)})
    for name, document in (('model', model), ('deployment', deployment)):
        (root / (name + '.json')).write_text(json.dumps(document))
    plan_path = await asyncio.to_thread(build, root / 'model.json', root / 'deployment.json', sdl_root, root / 'build')
    plan = json.loads(plan_path.read_text())
    codec = SDL2(sdl_root)
    recording_reader(codec)  # Load optional recording dependencies from this SDL checkout.
    from sdl_recording import RecordingWriter
    recorded = 'SDL2|Earlier{1:bool:unused;}|Measurement{1:uint64:old_sequence;2:fl64:old_value;3?:string:legacy;}'
    types = CatalogueTypes(codec.api, recorded)
    capture = root / 'old.pcapng'
    with RecordingWriter(capture) as writer:
        channel = writer.register_channel(topic='sensor.old', catalogue=recorded, root_type='Measurement')
        for index in range(2):
            message = types.types['Measurement'](old_sequence=2**64 - 1 - index, old_value=2.5 + index, legacy='discarded')
            writer.write_encoded(channel, codec.api.encode(types.context, message), log_time_ns=100 + index)
    target = plan['external_topics']['external.signal']['catalogue']
    target_types = CatalogueTypes(codec.api, target)
    expected = []
    for index in range(2):
        message = target_types.types['Measurement'](sequence=2**64 - 1 - index, value=3.5 + index)
        expected.append(hashlib.sha256(codec.wrap(target, codec.api.encode(target_types.context, message))).hexdigest())
    trace = root / 'trace.jsonl'
    summary = await run(capture, plan_path, .001, mappings=['sensor.old=external.signal'],
                        schema_policy='compatible', trace=trace)
    publications = [json.loads(line) for line in trace.read_text().splitlines()]
    case.assertEqual([e['sha256'] for e in publications if e.get('topic') == 'sink.output'], expected)
    case.assertEqual(summary['replay']['schema_adaptation']['adapted_messages'], 2)
