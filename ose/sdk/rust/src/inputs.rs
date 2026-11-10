//! CH/2 single-input activation references and synchronous parent-owned inboxes.
use crate::{invalid, receive, send, Event, MAX_SIZE};
use std::{io, time::Duration};

pub const READY: &[u8] = &[2];

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum EventKind { Message, Timeout }

/// Owned callback input or explicit Pull result. A timeout has no message.
#[derive(Clone, Debug)]
pub struct InputReference<T> {
    pub port: String,
    pub event: EventKind,
    pub message: Option<T>,
    pub handle: Option<String>,
    pub app_id: Option<u64>,
    pub source: Option<String>,
    pub elapsed: u64,
    pub due_ns: u64,
}
impl<T> InputReference<T> {
    pub fn timed_out(&self) -> bool { self.event == EventKind::Timeout }
}

#[derive(Clone, Debug)]
pub struct Record {
    pub handle: String,
    pub source: String,
    pub app_id: Option<u64>,
    pub received_ns: u64,
    pub catalogue: String,
    pub envelope: Vec<u8>,
}
impl Record {
    pub fn typed<T>(self, port: &str, message: T) -> InputReference<T> {
        InputReference { port: port.into(), event: EventKind::Message, message: Some(message),
            handle: Some(self.handle), app_id: self.app_id, source: Some(self.source),
            elapsed: 1, due_ns: self.received_ns }
    }
}

#[derive(Clone, Debug)]
pub struct Activation {
    pub time_ns: u64,
    pub index: u64,
    pub due_ns: u64,
    pub elapsed: u64,
    pub port: String,
    pub event: EventKind,
    pub record: Option<Record>,
}
impl Activation {
    pub fn reference<T>(&self, message: Option<T>) -> InputReference<T> {
        InputReference { port: self.port.clone(), event: self.event, message,
            handle: self.record.as_ref().map(|r| r.handle.clone()),
            app_id: self.record.as_ref().and_then(|r| r.app_id),
            source: self.record.as_ref().map(|r| r.source.clone()),
            elapsed: self.elapsed, due_ns: self.due_ns }
    }
}

#[derive(Clone, Copy, Default, Debug)]
pub struct Selector<'a> {
    pub id: Option<u64>,
    pub handle: Option<&'a str>,
    pub source: Option<&'a str>,
}
impl<'a> Selector<'a> {
    pub fn id(mut self, value: u64) -> Self { self.id = Some(value); self }
    pub fn handle(mut self, value: &'a str) -> Self { self.handle = Some(value); self }
    pub fn source(mut self, value: &'a str) -> Self { self.source = Some(value); self }
}

struct Reader<'a> { data: &'a [u8], offset: usize }
impl<'a> Reader<'a> {
    fn take(&mut self, size: usize) -> io::Result<&'a [u8]> {
        if size > self.data.len() - self.offset { return Err(invalid("truncated CH/2 body")); }
        let start = self.offset; self.offset += size;
        Ok(&self.data[start..self.offset])
    }
    fn byte(&mut self) -> io::Result<u8> { Ok(self.take(1)?[0]) }
    fn u64(&mut self) -> io::Result<u64> { Ok(u64::from_be_bytes(self.take(8)?.try_into().unwrap())) }
    fn text(&mut self, wide: bool) -> io::Result<String> {
        let size = if wide { u32::from_be_bytes(self.take(4)?.try_into().unwrap()) as usize }
            else { u16::from_be_bytes(self.take(2)?.try_into().unwrap()) as usize };
        if size == 0 || size > MAX_SIZE { return Err(invalid("invalid CH/2 text length")); }
        String::from_utf8(self.take(size)?.to_vec()).map_err(|_| invalid("CH/2 text UTF-8"))
    }
    fn optional_id(&mut self) -> io::Result<Option<u64>> {
        match self.byte()? { 0 => Ok(None), 1 => Ok(Some(self.u64()?)),
            _ => Err(invalid("invalid CH/2 optional ID flag")) }
    }
    fn end(&self) -> io::Result<()> {
        if self.offset != self.data.len() { return Err(invalid("trailing CH/2 data")); }
        Ok(())
    }
    fn record(&mut self) -> io::Result<Option<Record>> {
        match self.byte()? {
            0 => { self.end()?; Ok(None) }
            1 => {
                let handle = self.text(false)?;
                let source = self.text(false)?;
                let app_id = self.optional_id()?;
                let received_ns = self.u64()?;
                let catalogue = self.text(true)?;
                let envelope = self.take(self.data.len() - self.offset)?.to_vec();
                if envelope.is_empty() { return Err(invalid("empty CH/2 message envelope")); }
                Ok(Some(Record { handle, source, app_id, received_ns, catalogue, envelope }))
            }
            _ => Err(invalid("invalid CH/2 record flag")),
        }
    }
}

pub fn activation(event: &Event) -> io::Result<Activation> {
    if event.op != b'T' || event.data.len() >= MAX_SIZE { return Err(invalid("expected CH/2 activation")); }
    let mut reader = Reader { data: &event.data, offset: 0 };
    let time_ns = reader.u64()?;
    let index = reader.u64()?;
    let due_ns = reader.u64()?;
    let elapsed = reader.u64()?;
    let kind = reader.byte()?;
    let port = reader.text(false)?;
    let record = reader.record()?;
    if index == 0 || elapsed == 0 || due_ns > time_ns { return Err(invalid("invalid CH/2 activation context")); }
    let event = match kind { 1 => EventKind::Message, 2 => EventKind::Timeout,
        _ => return Err(invalid("unknown CH/2 activation event")) };
    if (event == EventKind::Message) != record.is_some() || event == EventKind::Message && elapsed != 1 {
        return Err(invalid("activation record does not match event"));
    }
    if record.as_ref().is_some_and(|r| r.received_ns > time_ns) { return Err(invalid("activation message is from the future")); }
    Ok(Activation { time_ns, index, due_ns, elapsed, port, event, record })
}

fn text(data: &mut Vec<u8>, value: &str) -> io::Result<()> {
    if value.is_empty() || value.len() > u16::MAX as usize { return Err(invalid("invalid CH/2 text length")); }
    data.extend_from_slice(&(value.len() as u16).to_be_bytes()); data.extend_from_slice(value.as_bytes());
    Ok(())
}
fn optional_id(data: &mut Vec<u8>, value: Option<u64>) {
    data.push(u8::from(value.is_some()));
    if let Some(value) = value { data.extend_from_slice(&value.to_be_bytes()); }
}

pub fn publication_data(port: &str, payload: &[u8], id: Option<u64>) -> io::Result<Vec<u8>> {
    if payload.is_empty() || payload.len() >= MAX_SIZE { return Err(invalid("invalid CH/2 publication size")); }
    let mut data = Vec::new(); text(&mut data, port)?; optional_id(&mut data, id);
    if payload.len() >= MAX_SIZE - data.len() { return Err(invalid("IPC too large")); }
    data.extend_from_slice(payload); Ok(data)
}
pub fn publish(port: &str, payload: &[u8], id: Option<u64>) -> io::Result<()> {
    send(b'P', &publication_data(port, payload, id)?)
}

fn request_data(operation: u8, port: &str, selector: Selector<'_>) -> io::Result<Vec<u8>> {
    let mut data = vec![operation]; text(&mut data, port)?;
    data.push(u8::from(selector.id.is_some()) | (u8::from(selector.handle.is_some()) << 1)
        | (u8::from(selector.source.is_some()) << 2));
    if let Some(id) = selector.id { data.extend_from_slice(&id.to_be_bytes()); }
    if let Some(handle) = selector.handle { text(&mut data, handle)?; }
    if let Some(source) = selector.source { text(&mut data, source)?; }
    Ok(data)
}
fn request(data: &[u8]) -> io::Result<Vec<u8>> {
    send(b'Q', data)?;
    let reply = receive()?;
    if reply.op != b'A' { return Err(invalid("expected CH/2 input reply")); }
    match reply.data.first() {
        Some(1) => Ok(reply.data[1..].to_vec()),
        Some(0) if reply.data.len() <= 4097 => {
            let error = std::str::from_utf8(&reply.data[1..]).map_err(|_| invalid("input reply error UTF-8"))?;
            Err(io::Error::new(io::ErrorKind::InvalidInput, error.to_owned()))
        }
        _ => Err(invalid("invalid CH/2 input reply")),
    }
}
pub fn get(port: &str, selector: Selector<'_>) -> io::Result<Option<Record>> {
    let data = request(&request_data(1, port, selector)?)?;
    Reader { data: &data, offset: 0 }.record()
}
pub fn pull(port: &str, selector: Selector<'_>) -> io::Result<Option<Record>> {
    let data = request(&request_data(2, port, selector)?)?;
    Reader { data: &data, offset: 0 }.record()
}
pub fn remove(port: &str, selector: Selector<'_>) -> io::Result<bool> {
    match request(&request_data(3, port, selector)?)?.as_slice() {
        [0] => Ok(false), [1] => Ok(true), _ => Err(invalid("invalid CH/2 removal reply")),
    }
}
pub fn arm(port: &str, delay: Option<Duration>) -> io::Result<()> {
    let delay = delay.map(|value| u64::try_from(value.as_nanos()).ok().filter(|n| *n != 0)
        .ok_or_else(|| invalid("timer delay must be positive uint64 nanoseconds"))).transpose()?;
    let mut data = vec![4]; text(&mut data, port)?; optional_id(&mut data, delay);
    if !request(&data)?.is_empty() { return Err(invalid("unexpected timer reply body")); }
    Ok(())
}
pub fn disarm(port: &str) -> io::Result<()> {
    let mut data = vec![5]; text(&mut data, port)?;
    if !request(&data)?.is_empty() { return Err(invalid("unexpected timer reply body")); }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn timeout_activation_and_all_truncations() {
        let data = [10u64.to_be_bytes().as_slice(), 1u64.to_be_bytes().as_slice(),
            8u64.to_be_bytes().as_slice(), 2u64.to_be_bytes().as_slice(), b"\x02\0\x05clock\0"].concat();
        let value = activation(&Event { op: b'T', data: data.clone() }).unwrap();
        assert_eq!((value.port.as_str(), value.time_ns, value.elapsed), ("clock", 10, 2));
        assert!(value.reference::<()>(None).timed_out());
        for length in 0..data.len() { assert!(activation(&Event { op: b'T', data: data[..length].to_vec() }).is_err()); }
        let mut bad = data.clone(); bad[32] = 1;
        assert!(activation(&Event { op: b'T', data: bad }).is_err());
    }
    #[test]
    fn exact_optional_ids_selectors_and_publication() {
        assert_eq!(request_data(2, "in", Selector::default().id(u64::MAX).handle("h").source("s")).unwrap(),
            [b"\x02\0\x02in\x07".as_slice(), &u64::MAX.to_be_bytes(), b"\0\x01h\0\x01s"].concat());
        assert_eq!(publication_data("out", &[0, 255], Some(0)).unwrap(),
            [b"\0\x03out\x01".as_slice(), &0u64.to_be_bytes(), &[0, 255]].concat());
        assert_eq!(publication_data("out", &[0, 255], None).unwrap(), b"\0\x03out\0\0\xff");
        assert!(request_data(1, "in", Selector::default().handle("")).is_err());
        assert!(publication_data("out", &[], None).is_err());
        assert!(arm("clock", Some(Duration::ZERO)).is_err());
        assert!(arm("clock", Some(Duration::from_secs(u64::MAX))).is_err());
    }
    #[test]
    fn records_preserve_catalogues_and_opaque_envelopes() {
        let mut data = vec![1]; text(&mut data, "h").unwrap(); text(&mut data, "source.out").unwrap();
        optional_id(&mut data, Some(u64::MAX)); data.extend(3u64.to_be_bytes());
        data.extend(4u32.to_be_bytes()); data.extend(b"SDL2"); data.extend(b"S2PS\0\xff");
        let record = Reader { data: &data, offset: 0 }.record().unwrap().unwrap();
        assert_eq!(record.app_id, Some(u64::MAX)); assert_eq!(record.catalogue, "SDL2");
        assert_eq!(record.envelope, b"S2PS\0\xff");
        for length in 0..data.len() - 6 { assert!(Reader { data: &data[..length], offset: 0 }.record().is_err()); }
        assert!(Reader { data: &[0, 1], offset: 0 }.record().is_err());
    }
}
