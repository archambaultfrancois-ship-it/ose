use crate::container::{Callbacks, Context, InputReference, Result};
use crate::types::value::Value;

#[derive(Default)]
pub struct Handler;

impl Callbacks for Handler {
    fn on_input(&mut self, ctx: &mut Context, input: &InputReference<Value>) -> Result {
        let output = Value { count: input.message.as_ref().ok_or("missing input message")?.count + 100 };
        match input.app_id {
            Some(id) => ctx.publish_out_with_id(&output, id),
            None => ctx.publish_out(&output),
        }
    }
}
