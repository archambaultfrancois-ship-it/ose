import org.sdlose.generated.a.API;
import org.sdlose.InputProtocol.Reference;
import java.math.BigInteger;
import sdl.globaltypes.value.Value;

public final class A extends API.Handler {
    @Override public void onStart(API.Context ctx) throws Exception {
        ctx.declareMeasurement("feedback.count");
    }

    @Override public void on_clock(API.Context ctx, Reference<Void> input) throws Exception {
        Reference<Value> previous = ctx.pull_feedback();
        if (previous != null) ctx.measure("feedback.count", previous.message.count);
        Value value = new Value(); value.count = ctx.activationIndex().intValue();
        ctx.publish_out(value, ctx.activationIndex());
        if (ctx.activationIndex().equals(BigInteger.valueOf(3))) ctx.disarm("clock");
    }
}
