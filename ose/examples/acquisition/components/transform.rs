use crate::container::{Callbacks, Context, InputReference, Result};
use crate::types::measurement::Measurement;

#[derive(Default)]
pub struct Handler;

impl Callbacks for Handler {
    fn on_input(&mut self, ctx: &mut Context, input: &InputReference<Measurement>) -> Result {
        let message = input.message.as_ref().ok_or("missing input message")?;
        let mut result = message.clone();
        result.value *= 2.0;
        match input.app_id {
            Some(id) => ctx.publish_result_with_id(&result, id),
            None => ctx.publish_result(&result),
        }
    }
}
