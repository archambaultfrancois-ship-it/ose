use crate::container::{Callbacks, Context, InputReference, Result, Selector};
use crate::types::value::Value;

#[derive(Default)]
pub struct Handler;

impl Callbacks for Handler {
    fn on_start(&mut self, ctx: &mut Context) -> Result {
        ctx.declare_measurement("feedback.count")
    }

    fn on_clock(&mut self, ctx: &mut Context, _input: &InputReference<()>) -> Result {
        if let Some(previous) = ctx.pull_feedback(Selector::default())? {
            ctx.measure("feedback.count", previous.message.as_ref().unwrap().count as f64)?;
        }
        ctx.publish_out_with_id(&Value { count: ctx.activation_index() as u32 }, ctx.activation_index())?;
        if ctx.activation_index() == 3 { ctx.disarm("clock")?; }
        Ok(())
    }
}
