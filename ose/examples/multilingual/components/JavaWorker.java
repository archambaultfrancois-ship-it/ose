package example;
import org.sdlose.generated.java_worker.API;
import org.sdlose.InputProtocol.Reference;
import sdl.globaltypes.measurement.Measurement;
public final class JavaWorker extends API.Handler {
    private long received;
    @Override public void on_input(API.Context ctx, Reference<Measurement> input) throws Exception {
        ++received;
        String delay = System.getenv("OSE_TEST_DELAY_MS");
        if (delay != null) Thread.sleep(Long.parseLong(delay));
        Measurement result = new Measurement();
        result.sequence = input.message.sequence;
        result.value = input.message.value + 3.0;
        ctx.publish_value(result, input.appId);
    }
}
