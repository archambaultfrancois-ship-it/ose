use crate::container::{Callbacks, Context, Result};
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

    fn on_input(&mut self, ctx: &mut Context, message: &SignalBlock) -> Result {
        ctx.record("dsp.input", message)?;
        let (rms, peak) = math::summarize(&message.samples)?;
        ctx.measure("vibration.rms", rms)?;
        ctx.publish_result(&Analysis {
            machine_id: message.machine_id,
            sequence: message.sequence,
            time_ns: message.time_ns,
            rms,
            peak,
        })?;
        if message.sequence == 1 || message.sequence % 10 == 0 {
            ctx.logf(sdl_ose_component::LogLevel::Debug, "analysis", format_args!(
                "Analyzed signal: machine={} sequence={} samples={} rms={:.3} peak={:.3}",
                message.machine_id, message.sequence, message.samples.len(), rms, peak,
            ))?;
        }
        Ok(())
    }
}
