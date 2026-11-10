"""CH/2 additions to the shared generated Rust context and lifecycle."""
from ose.modeling.native_containers import rust_string


def message_inputs(node):
    return {port: desc for port, desc in node["inputs"].items() if desc.get("kind") != "timer"}


def type_name(types, desc):
    module, symbol = types[desc["root"]]
    return f"crate::types::{module}::{symbol}"


def context_fields(node):
    return ["    allowed_outputs: &'static [&'static str],",
            *(f"    decoder_{port}: Option<(String, sdl_runtime::Context)>," for port in message_inputs(node))]


def output_methods(port, desc, types):
    typ, name = type_name(types, desc), rust_string(port)
    lines = []
    for suffix, argument, identity in (("", "", "None"), ("_with_id", ", id: u64", "Some(id)")):
        lines += [f"    pub fn publish_{port}{suffix}(&mut self, message: &{typ}{argument}) -> Result {{",
                  f"        self.authorize_output({name})?;",
                  f"        sdl_ose_component::inputs::publish({name}, &codec(sdl_runtime::encode(&self.output_{port}, message))?, {identity})?;",
                  "        Ok(())", "    }"]
    return lines


def context_methods(node, types):
    lines = ["    pub fn activation_index(&self) -> u64 { self.tick_index() }",
             "    fn authorize_output(&self, port: &str) -> Result {",
             '        if !self.active || !self.allowed_outputs.contains(&port) { return Err(format!("input event cannot activate output {port}").into()); }',
             "        Ok(())", "    }",
             "    fn timer_access(&self, port: &str) -> Result {",
             '        if !self.active && (self.recording_ready || !self.parameter_writes) { return Err("timer control requires initialization or an active callback".into()); }',
             "        match port {"]
    for port, desc in node["inputs"].items():
        if desc.get("kind") == "timer":
            lines.append(f"            {rust_string(port)} => Ok(()),")
    lines += ['            _ => Err("timer control requires a timer input".into()),', "        }", "    }",
              "    pub fn arm(&mut self, port: &str, delay: Option<std::time::Duration>) -> Result {",
              "        self.timer_access(port)?; sdl_ose_component::inputs::arm(port, delay)?; Ok(())", "    }",
              "    pub fn disarm(&mut self, port: &str) -> Result {",
              "        self.timer_access(port)?; sdl_ose_component::inputs::disarm(port)?; Ok(())", "    }"]
    for port, desc in message_inputs(node).items():
        typ = type_name(types, desc)
        lines += [f"    fn decode_{port}(&mut self, record: sdl_ose_component::inputs::Record) -> Result<InputReference<{typ}>> {{",
                  "        let (identity, payload) = codec(sdl_runtime::unpack_envelope(&record.envelope))?;",
                  '        if identity != codec(sdl_runtime::catalogue_id(&record.catalogue))? { return Err("catalogue identity mismatch".into()); }',
                  f"        if self.decoder_{port}.as_ref().map(|(text, _)| text.as_str()) != Some(record.catalogue.as_str()) {{",
                  f"            let check = sdl_runtime::compare_catalogues(&record.catalogue, {rust_string(desc['catalogue'])}, Some({rust_string(desc['root'])}), true);",
                  '            if !check.compatible() { return Err(format!("{} at {}", check.code, check.path).into()); }',
                  f"            let prepared = codec(sdl_runtime::prepare(&record.catalogue, &[{typ}::type_info()]))?;",
                  f"            self.decoder_{port} = Some((record.catalogue.clone(), prepared));", "        }",
                  f"        let message: {typ} = codec(sdl_runtime::decode(&self.decoder_{port}.as_ref().unwrap().1, payload))?;",
                  f"        Ok(record.typed({rust_string(port)}, message))", "    }"]
        if desc.get("mode", "push") == "pull":
            for operation in ("get", "pull"):
                lines += [f"    pub fn {operation}_{port}(&mut self, selector: Selector<'_>) -> Result<Option<InputReference<{typ}>>> {{",
                          '        if !self.active { return Err("input access requires an active callback".into()); }',
                          f"        sdl_ose_component::inputs::{operation}({rust_string(port)}, selector)?.map(|record| self.decode_{port}(record)).transpose()",
                          "    }"]
            lines += [f"    pub fn remove_{port}(&mut self, selector: Selector<'_>) -> Result<bool> {{",
                      '        if !self.active { return Err("input access requires an active callback".into()); }',
                      f"        Ok(sdl_ose_component::inputs::remove({rust_string(port)}, selector)?)", "    }"]
    return lines


def runtime(name, node, types, lines):
    lines += ["pub trait Callbacks {", "    fn on_start(&mut self, _ctx: &mut Context) -> Result { Ok(()) }",
              "    fn on_stop(&mut self, _ctx: &mut Context) -> Result { Ok(()) }"]
    activating = {port: desc for port, desc in node["inputs"].items() if desc.get("mode", "push") != "pull"}
    for port, desc in activating.items():
        typ = "()" if desc.get("kind") == "timer" else type_name(types, desc)
        lines += [f"    fn on_{port}(&mut self, _ctx: &mut Context, _input: &InputReference<{typ}>) -> Result {{",
                  f'        Err("implement on_{port}".into())', "    }"]
    lines += ["}", "pub fn run<H: Callbacks>(mut handler: H) -> Result {", "    let init = receive()?;",
              '    if init.op != b\'I\' { return Err("expected INIT".into()); }', "    let mut ctx = Context {",
              f"        name: {rust_string(name)}, config_json: String::from_utf8(init.data)?, active: false, tick: Default::default(), parameter_writes: true,",
              "        recording: Default::default(), recording_ready: false, recording_bytes: 0, measurements: Default::default(), allowed_outputs: &[],"]
    for port in message_inputs(node):
        lines.append(f"        decoder_{port}: None,")
    for port, desc in node["outputs"].items():
        lines.append(f"        output_{port}: codec(sdl_runtime::prepare({rust_string(desc['catalogue'])}, &[{type_name(types, desc)}::type_info()]))?,")
    for alias, desc in node.get("parameter_bindings", {}).items():
        lines.append(f"        parameter_codec_{alias}: codec(sdl_runtime::prepare({rust_string(desc['catalogue'])}, &[{type_name(types, desc)}::type_info()]))?,")
        if "read" in desc["access"]:
            lines.append(f"        parameter_value_{alias}: None,")
    lines += ["    };", "    let execution = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| -> Result {"]
    for desc in node.get("parameter_bindings", {}).values():
        if "read" in desc["access"]:
            lines.append("        ctx.parameter_update(&receive()?, true)?;")
    lines += ["        handler.on_start(&mut ctx)?;", "        ctx.parameter_writes = false; ctx.recording_ready = true;",
              "        send(b'R', sdl_ose_component::inputs::READY)?;", "        loop {", "            let event = receive()?;",
              "            match event.op {", "                b'E' => ctx.recording_update(&event.data)?,"]
    if node.get("parameter_bindings"):
        lines.append("                b'U' => ctx.parameter_update(&event, false)?,")
    mutable = "mut " if any(desc.get("kind") != "timer" for desc in activating.values()) else ""
    lines += ["                b'T' => {", f"                    let {mutable}activation = sdl_ose_component::inputs::activation(&event)?;",
              '                    if ctx.tick_index().checked_add(1) != Some(activation.index) || activation.time_ns < ctx.time_ns() { return Err("non-monotonic CH/2 activation".into()); }',
              "                    ctx.tick = sdl_ose_component::TickContext { time_ns: activation.time_ns, tick_index: activation.index };",
              "                    ctx.active = true; ctx.parameter_writes = true; ctx.recording_bytes = 0;",
              "                    match activation.port.as_str() {"]
    for port, desc in activating.items():
        lines += [f"                        {rust_string(port)} => {{"]
        events = {"timeout": desc.get("activates", {}).get("timeout", [])}
        if desc.get("kind") == "timer":
            check = "activation.event != EventKind::Timeout"
            if desc["timer"]["mode"] == "one_shot":
                check += " || activation.elapsed != 1"
            lines.append(f'                            if {check} {{ return Err("invalid timer activation".into()); }}')
            lines.append("                            let input = activation.reference::<()>(None);")
        else:
            typ = type_name(types, desc)
            lines += ["                            let mut input = match activation.event {",
                      f"                                EventKind::Message => ctx.decode_{port}(activation.record.take().ok_or(\"missing message record\")?)?,"]
            if "timeout" in desc:
                lines.append(f"                                EventKind::Timeout => activation.reference::<{typ}>(None),")
            else:
                lines.append('                                EventKind::Timeout => return Err("input has no activating timeout".into()),')
            lines += ["                            };", "                            input.due_ns = activation.due_ns;"]
            events["message"] = desc.get("activates", {}).get("message", [])
        lines.append("                            ctx.allowed_outputs = match activation.event {")
        for event in ("message", "timeout"):
            outputs = ", ".join(rust_string(output) for output in events.get(event, []))
            variant = "Message" if event == "message" else "Timeout"
            lines.append(f"                                EventKind::{variant} => &[{outputs}],")
        lines += ["                            };", f"                            handler.on_{port}(&mut ctx, &input)?;", "                        }"]
    lines += ['                        _ => return Err("unknown activating input".into()),', "                    }",
              "                    ctx.active = false; ctx.parameter_writes = false; ctx.allowed_outputs = &[];",
              "                    send(b'F', &[])?;", "                }", "                b'X' if event.data.is_empty() => return Ok(()),",
              '                _ => return Err("unexpected CH/2 component event".into()),', "            }", "        }", "    }));",
              "    ctx.active = false; ctx.parameter_writes = false; ctx.recording_ready = true; ctx.allowed_outputs = &[];",
              "    let stop = handler.on_stop(&mut ctx);", "    match execution {",
              "        Ok(result) => result.and(stop),", "        Err(panic) => std::panic::resume_unwind(panic),", "    }", "}", ""]
    return lines
