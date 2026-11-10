"""Shared, port-free diagnostic components for SDK and live capture tests."""
import json
from pathlib import Path
from ose.modeling.compiler import build

PYTHON = '''from diagnostic import Diagnostic
def on_start(ctx):
    ctx.declare_recording_point("dsp.fft.intermediaire", Diagnostic, "Spectre avant filtrage")
    ctx.declare_recording_point("dsp.etat", Diagnostic, "Etat interne")
def on_clock(ctx, input):
    value = Diagnostic(sequence=ctx.tick_index)
    ctx.record("dsp.fft.intermediaire", value)
    ctx.record("dsp.etat", value)
'''
C = '''#include "component.h"
static int start(OseContext *ctx) {
    return ose_declare_recording_Diagnostic(ctx, "dsp.fft.intermediaire", "Spectre avant filtrage") &&
           ose_declare_recording_Diagnostic(ctx, "dsp.etat", "Etat interne");
}
static int clock_input(OseContext *ctx, const Input_clock *input) {
    (void)input;
    Diagnostic value = {0};
    value.sequence = ctx->tick_index;
    if (ose_record_Diagnostic(ctx, "dsp.fft.intermediaire", &value) == OSE_RECORD_ERROR) return 0;
    return ose_record_Diagnostic(ctx, "dsp.etat", &value) != OSE_RECORD_ERROR;
}
const ComponentCallbacks component_callbacks = { .on_start = start, .on_clock = clock_input };
'''
RUST = '''use crate::container::{Callbacks, Context, Result};
use crate::types::diagnostic::Diagnostic;
#[derive(Default)]
pub struct Handler;
impl Callbacks for Handler {
    fn on_start(&mut self, ctx: &mut Context) -> Result {
        ctx.declare_recording_point::<Diagnostic>("dsp.fft.intermediaire", "Spectre avant filtrage")?;
        ctx.declare_recording_point::<Diagnostic>("dsp.etat", "Etat interne")
    }
    fn on_clock(&mut self, ctx: &mut Context, _input: &crate::container::InputReference<()>) -> Result {
        let value = Diagnostic { sequence: ctx.tick_index() };
        ctx.record("dsp.fft.intermediaire", &value)?;
        ctx.record("dsp.etat", &value)?;
        Ok(())
    }
}
'''
JAVA = '''import org.sdlose.generated.source.API;
import sdl.globaltypes.diagnostic.Diagnostic;
public final class Source extends API.Handler {
    public void onStart(API.Context ctx) throws Exception {
        ctx.declareRecordingPoint("dsp.fft.intermediaire", Diagnostic.class, "Spectre avant filtrage");
        ctx.declareRecordingPoint("dsp.etat", Diagnostic.class, "Etat interne");
    }
    public void on_clock(API.Context ctx, org.sdlose.InputProtocol.Reference<Void> input) throws Exception {
        Diagnostic value = new Diagnostic(); value.sequence = ctx.tickIndex().longValue();
        ctx.record("dsp.fft.intermediaire", value);
        ctx.record("dsp.etat", value);
    }
}
'''
ADA_SPEC = '''with Ose_source;
package Source_Handler is
   type Object is new Ose_source.Handler with null record;
   overriding procedure On_Start (Self : in out Object; Ctx : in out Ose_source.Context);
   overriding procedure On_clock (Self : in out Object; Ctx : in out Ose_source.Context; Input : Ose_source.Input_clock);
end Source_Handler;
'''
ADA = '''with SDL_diagnostic; with Ose_Recording;
package body Source_Handler is
   overriding procedure On_Start (Self : in out Object; Ctx : in out Ose_source.Context) is
   begin
      Ose_source.Declare_Recording_Diagnostic (Ctx, "dsp.fft.intermediaire", "Spectre avant filtrage");
      Ose_source.Declare_Recording_Diagnostic (Ctx, "dsp.etat", "Etat interne");
   end;
   overriding procedure On_clock (Self : in out Object; Ctx : in out Ose_source.Context; Input : Ose_source.Input_clock) is
      Value : SDL_diagnostic.T_Diagnostic;
      Outcome : Ose_Recording.Emit_Result;
   begin
      Value.F_sequence := Ose_source.Tick_Index (Ctx);
      Outcome := Ose_source.Record_Diagnostic (Ctx, "dsp.fft.intermediaire", Value);
      Outcome := Ose_source.Record_Diagnostic (Ctx, "dsp.etat", Value);
   end;
end Source_Handler;
'''


def build_component(root, language, sdl_root, *, distributed=False, extra_schema=False):
    root = Path(root); root.mkdir(parents=True, exist_ok=True)
    (root / "diagnostic.sdl").write_text("message Diagnostic {1: required uint64 sequence;}")
    handler, content = {"python": ("source.py", PYTHON), "c": ("source.c", C),
                        "rust": ("source.rs", RUST), "java": ("Source.java", JAVA),
                        "ada": ("source_handler.adb", ADA)}[language]
    (root / handler).write_text(content)
    if language == "ada":
        (root / "source_handler.ads").write_text(ADA_SPEC)
    model = dict(version=2, schemas="diagnostic.sdl", components={"source": dict(
        inputs={'clock': dict(kind='timer', timer=dict(mode='periodic', delay=.01))}, outputs={})}, subscriptions=[])
    if extra_schema:
        (root / "public.sdl").write_text("message Public {1: uint8 value;}")
        model["schemas"] = "public.sdl"
        model["components"]["source"]["recording_types"] = {
            "diagnostic": {"root": "Diagnostic", "schema": "diagnostic.sdl"}}
    container = dict(language=language, handler=handler)
    if language == "java": container["class"] = "Source"
    if language == "ada": container["unit"] = "Source_Handler"
    deployment = dict(version=1, components={"source": dict(container=container)})
    if distributed: deployment["execution"] = "distributed"
    for name, value in (("model", model), ("deployment", deployment)):
        (root / (name + ".json")).write_text(json.dumps(value))
    plan_path = build(root / "model.json", root / "deployment.json", sdl_root, root / "build")
    return json.loads(plan_path.read_text())
