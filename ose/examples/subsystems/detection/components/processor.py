from value import Value


def on_input(ctx, input):
    if input.message.value >= ctx.parameter('settings').limit:
        ctx.publish_out(Value(value=input.message.value))
