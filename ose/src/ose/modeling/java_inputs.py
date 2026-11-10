"""CH/2 additions to the shared generated Java context and lifecycle."""
from ose.modeling.managed_containers import java_string


def input_type(desc, typ):
    return "java.lang.Void" if desc.get("kind") == "timer" else typ(desc)


def methods(node, typ):
    lines = []
    for index, (port, desc) in enumerate(node["inputs"].items()):
        if desc.get("mode") != "pull":
            continue
        value = typ(desc)
        reference = f"org.sdlose.InputProtocol.Reference<{value}>"
        for operation in ("get", "pull"):
            lines += [f"        public {reference} {operation}_{port}() throws Exception {{ return {operation}_{port}(null); }}",
                      f"        public {reference} {operation}_{port}(org.sdlose.InputProtocol.Selector selector) throws Exception {{",
                      f"            return getInput({index}, selector, {value}.class, {str(operation == 'pull').lower()});", "        }"]
        lines += [f"        public boolean remove_{port}() throws Exception {{ return remove_{port}(null); }}",
                  f"        public boolean remove_{port}(org.sdlose.InputProtocol.Selector selector) throws Exception {{ return removeInput({index}, selector); }}"]
    for index, (port, desc) in enumerate(node["outputs"].items()):
        lines += [f"        public void publish_{port}({typ(desc)} message, java.math.BigInteger id) throws Exception {{ publish({index}, message, id); }}"]
    return lines


def api(node, typ):
    lines = ["    public static abstract class Handler {",
             "        public void onStart(Context ctx) throws Exception {}",
             "        public void onStop(Context ctx) throws Exception {}"]
    activating = {port: desc for port, desc in node["inputs"].items() if desc.get("mode", "push") != "pull"}
    for port, desc in activating.items():
        value = input_type(desc, typ)
        lines += [f"        public void on_{port}(Context ctx, org.sdlose.InputProtocol.Reference<{value}> input) throws Exception {{",
                  f"            throw new UnsupportedOperationException(\"missing on_{port}\");", "        }"]
    lines += ["    }", "    static void dispatch(Handler handler, Context ctx, org.sdlose.InputProtocol.Reference<?> input) throws Exception {"]
    for port, desc in activating.items():
        lines += [f"        if (input.port.equals({java_string(port)})) {{ handler.on_{port}(ctx, input.asType({input_type(desc, typ)}.class)); return; }}"]
    lines += ['        throw new IllegalArgumentException("unknown activating input");', "    }", "}"]
    return lines


def main(package, identity):
    return f'''package {package};
import org.sdlose.Component;
public final class Main {{
    public static void main(String[] args) {{
        int status = 0;
        try {{
            final API.Context ctx = new API.Context();
            final API.Handler handler = (API.Handler)Class.forName({java_string(identity)}).getDeclaredConstructor().newInstance();
            Component.runInputs(ctx, new Component.InputCallbacks() {{
                public void start() throws Exception {{ handler.onStart(ctx); }}
                public void activate(org.sdlose.InputProtocol.Reference<?> input) throws Exception {{ API.dispatch(handler, ctx, input); }}
                public void stop() throws Exception {{ handler.onStop(ctx); }}
            }});
        }} catch (Throwable failure) {{ failure.printStackTrace(System.err); status = 1; }}
        System.exit(status);
    }}
}}
'''
