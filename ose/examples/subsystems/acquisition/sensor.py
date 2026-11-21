from value import Value

def on_clock(ctx, input):
    ctx.publish_out(Value(value=90))
