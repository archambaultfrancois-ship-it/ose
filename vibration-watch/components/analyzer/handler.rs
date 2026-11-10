use crate::container::{Callbacks, Context, InputReference, Result};
use crate::types::{analysis::Analysis, vibration_signal::SignalBlock};
#[path = "math.rs"]
mod math;

#[derive(Default)]
pub struct Handler;

impl Callbacks for Handler {
    fn on_start(&mut self, ctx: &mut Context) -> Result {
        ctx.declare_recording_point::<SignalBlock>(
            "dsp.input",
            "Signal block received before RMS and peak calculation",
        )?;
        ctx.declare_measurement("vibration.rms")?;
        ctx.logf(sdl_ose_component::LogLevel::Info, "lifecycle", format_args!("Analyzer started"))
    }

    fn on_stop(&mut self, ctx: &mut Context) -> Result {
        ctx.logf(sdl_ose_component::LogLevel::Info, "lifecycle", format_args!("Analyzer stopped"))
    }

    fn on_input(&mut self, ctx: &mut Context, input: &InputReference<SignalBlock>) -> Result {
        let message = input.message.as_ref().ok_or("missing signal message")?;
        ctx.record("dsp.input", message)?;
        let (rms, peak) = math::summarize(&message.samples)?;
        ctx.measure("vibration.rms", rms)?;
        let result = Analysis {
            machine_id: message.machine_id,
            sequence: message.sequence,
            time_ns: message.time_ns,
            rms,
            peak,
        };
        match input.app_id {
            Some(id) => ctx.publish_result_with_id(&result, id)?,
            None => ctx.publish_result(&result)?,
        }
        if message.sequence == 1 || message.sequence % 10 == 0 {
            ctx.logf(sdl_ose_component::LogLevel::Debug, "analysis", format_args!(
                "Analyzed signal: machine={} sequence={} samples={} rms={:.3} peak={:.3}",
                message.machine_id, message.sequence, message.samples.len(), rms, peak,
            ))?;
        }
        Ok(())
    }
}
