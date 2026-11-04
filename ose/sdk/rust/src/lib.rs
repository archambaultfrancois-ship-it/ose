//! Binary stdio component SDK. No transport or codec dependencies.
use std::io::{self, Read, Write};

pub const MAX_SIZE: usize = 1024 * 1024;
pub const TICK_CONTEXT: &[u8] = &[1];
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
#[repr(u8)]
pub enum LogLevel { Debug=10, Info=20, Warning=30, Error=40, Critical=50, Fatal=60 }
const MAX_LOG_MESSAGE: usize = 8192;
const LOG_TRUNCATION: &str = "… [truncated]";
fn log_prefix(message: &str, limit: usize) -> &str {
    let mut end = message.len().min(limit);
    while !message.is_char_boundary(end) { end -= 1; }
    &message[..end]
}
fn deposit_log(level: LogLevel, logger: &str, message: &str) -> io::Result<()> {
    if logger.len() > 200 || logger.contains('\0') || message.is_empty() || message.contains('\0') {
        return Err(invalid("invalid log category/message"));
    }
    let oversized = message.len() > MAX_LOG_MESSAGE;
    let prefix = if oversized { log_prefix(message, MAX_LOG_MESSAGE - LOG_TRUNCATION.len()) } else { message };
    let mut body = Vec::with_capacity(3 + logger.len() + prefix.len() + if oversized { LOG_TRUNCATION.len() } else { 0 });
    body.push(level as u8);
    body.extend_from_slice(&(logger.len() as u16).to_be_bytes());
    body.extend_from_slice(logger.as_bytes()); body.extend_from_slice(prefix.as_bytes());
    if oversized { body.extend_from_slice(LOG_TRUNCATION.as_bytes()); }
    send(b'L', &body)
}
#[derive(Default)]
struct LogText { text: String, truncated: bool }
impl LogText {
    fn finish(mut self) -> String {
        if self.truncated {
            let size = log_prefix(&self.text, MAX_LOG_MESSAGE - LOG_TRUNCATION.len()).len();
            self.text.truncate(size);
            self.text.push_str(LOG_TRUNCATION);
        }
        self.text
    }
}
impl std::fmt::Write for LogText {
    fn write_str(&mut self, text: &str) -> std::fmt::Result {
        if text.contains('\0') { return Err(std::fmt::Error); }
        if !self.truncated {
            let remaining = MAX_LOG_MESSAGE - self.text.len();
            self.text.push_str(log_prefix(text, remaining));
            self.truncated = text.len() > remaining;
        }
        Ok(())
    }
}
/// Deposit a log in the CH/1 stream; delivery and retention are best effort.
/// Use format_args! for compile-time checking of the template and argument types.
/// For preformatted text, pass format_args!("{}", message).
/// ```compile_fail
/// use sdl_ose_component::{logf, LogLevel};
/// logf(LogLevel::Info, "test", format_args!("{:x}", "wrong type"));
/// ```
pub fn logf(level: LogLevel, logger: &str, arguments: std::fmt::Arguments<'_>) -> io::Result<()> {
    let mut message = LogText::default();
    std::fmt::write(&mut message, arguments).map_err(|_| invalid("invalid formatted log"))?;
    deposit_log(level, logger, &message.finish())
}
#[derive(Default, Clone, Copy, Debug, PartialEq, Eq)]
pub struct TickContext { pub time_ns: u64, pub tick_index: u64 }
pub fn tick(event: &Event, previous_index: u64) -> io::Result<TickContext> {
    if event.op != b'T' { return Err(invalid("expected tick")); }
    if event.data.is_empty() {
        return Ok(TickContext { time_ns: 0, tick_index: previous_index + 1 });
    }
    if event.data.len() != 16 { return Err(invalid("invalid tick context")); }
    let time_ns = u64::from_be_bytes(event.data[..8].try_into().unwrap());
    let tick_index = u64::from_be_bytes(event.data[8..].try_into().unwrap());
    if tick_index == 0 { return Err(invalid("tick index must be positive")); }
    Ok(TickContext { time_ns, tick_index })
}
pub struct Event { pub op: u8, pub data: Vec<u8> }
pub struct Input<'a> { pub port: &'a str, pub catalogue: &'a str, pub payload: &'a [u8] }
impl Input<'_> {
    /// Explicit null input from the adapter, allowed only on a required timed input.
    pub fn is_null(&self) -> bool { self.catalogue.is_empty() && self.payload.is_empty() }
}

pub fn receive() -> io::Result<Event> {
    let mut reader = io::stdin().lock();
    let mut prefix = [0; 4];
    reader.read_exact(&mut prefix)?;
    let size = u32::from_be_bytes(prefix) as usize;
    if size == 0 || size > MAX_SIZE { return Err(invalid("invalid IPC size")); }
    let mut body = vec![0; size];
    reader.read_exact(&mut body)?;
    #[cfg(target_os = "linux")]
    if body[0] == b'I' {
        if let Ok(name) = std::env::var("OSE_PROCESS_NAME") {
            if !name.is_empty() && name.len() <= 15 &&
                name.bytes().all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-') {
                let _ = std::fs::write("/proc/thread-self/comm", name);
            }
        }
    }
    Ok(Event { op: body[0], data: body[1..].to_vec() })
}
pub fn send(op: u8, data: &[u8]) -> io::Result<()> {
    if data.len() >= MAX_SIZE { return Err(invalid("IPC too large")); }
    let mut writer = io::stdout().lock();
    writer.write_all(&((data.len()+1) as u32).to_be_bytes())?;
    writer.write_all(&[op])?;
    writer.write_all(data)?;
    writer.flush()
}
fn named_send(op: u8, port: &str, payload: &[u8]) -> io::Result<()> {
    if port.len() > u16::MAX as usize { return Err(invalid("port too long")); }
    let mut data = Vec::new();
    data.extend_from_slice(&(port.len() as u16).to_be_bytes());
    data.extend_from_slice(port.as_bytes()); data.extend_from_slice(payload);
    send(op, &data)
}
pub fn publish(port: &str, payload: &[u8]) -> io::Result<()> { named_send(b'P', port, payload) }
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum RecordResult { Queued, Disabled, Dropped }
pub fn valid_recording_name(name: &str) -> bool {
    !name.is_empty() && name.len() <= 200 && name.split('.').all(|part| {
        let mut bytes = part.bytes();
        bytes.next().is_some_and(|b| b.is_ascii_alphabetic() || b == b'_') &&
            bytes.all(|b| b.is_ascii_alphanumeric() || b == b'_')
    })
}
pub fn declare_recording_point(name: &str, root: &str, catalogue: &str, description: &str) -> io::Result<()> {
    if !valid_recording_name(name) || root.is_empty() || description.len() > 1024 ||
        [root, catalogue, description].iter().any(|s| s.contains('\0')) {
        return Err(invalid("invalid recording declaration"));
    }
    named_send(b'N', name, format!("{root}\0{description}\0{catalogue}").as_bytes())
}
pub fn record(name: &str, payload: &[u8]) -> io::Result<()> { named_send(b'C', name, payload) }
pub fn declare_measurement(name: &str) -> io::Result<()> {
    if !valid_recording_name(name) { return Err(invalid("invalid measurement name")); }
    named_send(b'M', name, &[])
}
pub fn measure(name: &str, value: f64) -> io::Result<()> {
    if !valid_recording_name(name) || !value.is_finite() { return Err(invalid("invalid measurement name or fl64")); }
    named_send(b'V', name, &value.to_be_bytes())
}
pub fn set_parameters(name: &str, payload: &[u8]) -> io::Result<()> {
    named_send(b'S', name, payload)?;
    let result = receive()?;
    if result.op == b'A' && result.data == [1] { return Ok(()); }
    if result.op == b'A' && result.data.first() == Some(&0) {
        return Err(io::Error::new(io::ErrorKind::InvalidInput, String::from_utf8_lossy(&result.data[1..]).into_owned()));
    }
    Err(invalid("invalid parameter write result"))
}
pub fn input(event: &Event) -> io::Result<Input<'_>> {
    let d = &event.data;
    if !matches!(event.op, b'D' | b'U') || d.len() < 6 { return Err(invalid("expected input")); }
    let n = u16::from_be_bytes([d[0],d[1]]) as usize;
    if n > d.len()-6 { return Err(invalid("truncated port")); }
    let c = u32::from_be_bytes(d[2+n..6+n].try_into().unwrap()) as usize;
    if c > d.len()-6-n { return Err(invalid("truncated catalogue")); }
    Ok(Input { port: std::str::from_utf8(&d[2..2+n]).map_err(|_| invalid("port UTF8"))?,
        catalogue: std::str::from_utf8(&d[6+n..6+n+c]).map_err(|_| invalid("catalogue UTF8"))?,
        payload: &d[6+n+c..] })
}
pub fn initialize() -> io::Result<Vec<u8>> {
    let event = receive()?;
    if event.op != b'I' { return Err(invalid("expected initialization")); }
    send(b'R', &[])?;
    Ok(event.data)
}
fn invalid(text: &str) -> io::Error { io::Error::new(io::ErrorKind::InvalidData, text) }

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn formatted_logs_preserve_text_and_mark_utf8_truncation() {
        let mut output = LogText::default();
        std::fmt::write(&mut output, format_args!("motor={} rms={:.2}", 7, 1.25)).unwrap();
        assert_eq!(output.finish(), "motor=7 rms=1.25");
        for text in ["a".repeat(8192), "a".repeat(8193), "é".repeat(5000), "🙂".repeat(3000)] {
            let mut bounded = LogText::default();
            std::fmt::write(&mut bounded, format_args!("{}", text)).unwrap();
            let result = bounded.finish();
            assert!(result.len() <= MAX_LOG_MESSAGE);
            if text.len() <= MAX_LOG_MESSAGE { assert_eq!(result, text); }
            else { assert!(result.ends_with(LOG_TRUNCATION)); }
        }
        let mut bounded = LogText::default();
        std::fmt::write(&mut bounded, format_args!("{:08193}", 1)).unwrap();
        assert!(bounded.finish().ends_with(LOG_TRUNCATION));
    }
    #[test]
    fn timestamped_ticks_and_legacy_frames() {
        assert_eq!(tick(&Event { op: b'T', data: vec![] }, 2).unwrap(), TickContext { time_ns: 0, tick_index: 3 });
        let data = [123456789_u64.to_be_bytes(), 4_u64.to_be_bytes()].concat();
        assert_eq!(tick(&Event { op: b'T', data }, 0).unwrap(), TickContext { time_ns: 123456789, tick_index: 4 });
        for size in [1, 15, 17] {
            assert!(tick(&Event { op: b'T', data: vec![0; size] }, 0).is_err());
        }
        assert!(tick(&Event { op: b'T', data: vec![0; 16] }, 0).is_err());
    }
    #[test]
    fn truncated_inputs_are_rejected() {
        for n in 0..6 { assert!(input(&Event {op:b'D',data:vec![0;n]}).is_err()); }
        assert!(input(&Event {op:b'D',data:vec![255;6]}).is_err());
    }
}
