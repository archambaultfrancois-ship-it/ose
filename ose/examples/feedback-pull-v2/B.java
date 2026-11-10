import org.sdlose.generated.b.API;
import org.sdlose.InputProtocol.Reference;
import sdl.globaltypes.value.Value;

public final class B extends API.Handler {
    @Override public void on_input(API.Context ctx, Reference<Value> input) throws Exception {
        Value value = new Value(); value.count = input.message.count + 100;
        ctx.publish_out(value, input.appId);
    }
}
