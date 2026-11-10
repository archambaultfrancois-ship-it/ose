//! CENV/2 binary metadata. Message bodies remain opaque, owned bytes.
use crate::{json::Json, Error, Packet, Result};
use std::collections::BTreeMap;

const MAGIC: &[u8] = b"CE\x02";
pub(crate) const PREFIX_SIZE: usize = 8;
const MAX_METADATA: usize = 65536;
const MAX_DEPTH: usize = 64;
type Fields = &'static [(&'static str, bool)]; // true = text/u16; false = u64
type Schema = (u8, &'static str, Fields, Fields);
const SCHEMAS: &[Schema] = &[
    (1, "data", &[("subscription", true), ("id", true), ("epoch", true),
        ("sequence", false), ("topic", true)], &[("app_id", false)]),
    (2, "ack", &[("subscription", true), ("id", true)], &[]),
    (3, "publish", &[("request", false), ("topic", true)], &[("app_id", false)]),
    (4, "result", &[("request", false)], &[("content", true), ("error", true), ("error_code", true)]),
    (5, "deliver", &[("request", false), ("subscription", true), ("id", true),
        ("epoch", true), ("sequence", false), ("source_token", true),
        ("target_token", true), ("source", true), ("topic", true)], &[("app_id", false)]),
];

fn bad(message: &str) -> Error { Error::new("protocol", message) }

fn header(value: &Json) -> Result<&BTreeMap<String, Json>> {
    let Json::Object(fields) = value else { return Err(bad("metadata must be an object")); };
    let op = fields.get("op").ok_or_else(|| bad("missing metadata operation"))?.text()?;
    if op.is_empty() { return Err(bad("empty metadata operation")); }
    if ["data", "publish", "deliver"].contains(&op) {
        if let Some(id) = fields.get("app_id") { id.uint()?; }
    }
    Ok(fields)
}

struct Writer(Vec<u8>);
impl Writer {
    fn add(&mut self, data: &[u8]) -> Result<()> {
        if data.len() > MAX_METADATA - self.0.len() {
            return Err(Error::new("oversize", "metadata exceeds 64 KiB"));
        }
        self.0.extend_from_slice(data);
        Ok(())
    }
    fn text(&mut self, value: &str, compact: bool) -> Result<()> {
        let size = value.len();
        if size > if compact { 65535 } else { MAX_METADATA } {
            return Err(Error::new("oversize", "metadata text exceeds size limit"));
        }
        if compact { self.add(&(size as u16).to_be_bytes())?; }
        else { self.add(&(size as u32).to_be_bytes())?; }
        self.add(value.as_bytes())
    }
    fn value(&mut self, value: &Json, depth: usize) -> Result<()> {
        if depth > MAX_DEPTH { return Err(bad("metadata nesting exceeds 64 levels")); }
        match value {
            Json::Null => self.add(&[0]),
            Json::Bool(value) => self.add(&[if *value { 2 } else { 1 }]),
            Json::Number(value) => {
                if !value.contains(['.', 'e', 'E']) {
                    if let Ok(number) = value.parse::<u64>() {
                        self.add(&[3])?;
                        return self.add(&number.to_be_bytes());
                    }
                    let number = value.parse::<i64>().map_err(|_| bad("integer outside int64/uint64"))?;
                    if number == 0 {
                        self.add(&[3])?;
                        return self.add(&0u64.to_be_bytes());
                    }
                    self.add(&[4])?;
                    self.add(&number.to_be_bytes())
                } else {
                    let number = value.parse::<f64>().map_err(|_| bad("invalid float"))?;
                    if !number.is_finite() { return Err(bad("nonfinite metadata number")); }
                    self.add(&[5])?;
                    self.add(&number.to_be_bytes())
                }
            }
            Json::String(value) => { self.add(&[6])?; self.text(value, false) }
            Json::Array(values) => {
                if values.len() > MAX_METADATA { return Err(bad("array exceeds size limit")); }
                self.add(&[7])?;
                self.add(&(values.len() as u32).to_be_bytes())?;
                for value in values { self.value(value, depth + 1)?; }
                Ok(())
            }
            Json::Object(values) => {
                if values.len() > MAX_METADATA { return Err(bad("object exceeds size limit")); }
                self.add(&[8])?;
                self.add(&(values.len() as u32).to_be_bytes())?;
                for (key, value) in values { self.text(key, false)?; self.value(value, depth + 1)?; }
                Ok(())
            }
        }
    }
}

struct Reader<'a> { data: &'a [u8], offset: usize }
impl<'a> Reader<'a> {
    fn take(&mut self, size: usize) -> Result<&'a [u8]> {
        if size > self.data.len() - self.offset { return Err(bad("truncated CENV/2 metadata")); }
        let start = self.offset;
        self.offset += size;
        Ok(&self.data[start..self.offset])
    }
    fn byte(&mut self) -> Result<u8> { Ok(self.take(1)?[0]) }
    fn u32(&mut self) -> Result<u32> { Ok(u32::from_be_bytes(self.take(4)?.try_into().unwrap())) }
    fn u64(&mut self) -> Result<u64> { Ok(u64::from_be_bytes(self.take(8)?.try_into().unwrap())) }
    fn text(&mut self, compact: bool) -> Result<String> {
        let size = if compact { u16::from_be_bytes(self.take(2)?.try_into().unwrap()) as usize }
            else { self.u32()? as usize };
        String::from_utf8(self.take(size)?.to_vec()).map_err(|_| bad("invalid metadata UTF-8"))
    }
    fn value(&mut self, depth: usize) -> Result<Json> {
        if depth > MAX_DEPTH { return Err(bad("metadata nesting exceeds 64 levels")); }
        match self.byte()? {
            0 => Ok(Json::Null),
            1 => Ok(Json::Bool(false)),
            2 => Ok(Json::Bool(true)),
            3 => Ok(self.u64()?.into()),
            4 => {
                let value = i64::from_be_bytes(self.take(8)?.try_into().unwrap());
                if value >= 0 { return Err(bad("nonnegative signed metadata number")); }
                Ok(Json::Number(value.to_string()))
            }
            5 => {
                let value = f64::from_be_bytes(self.take(8)?.try_into().unwrap());
                if !value.is_finite() { return Err(bad("nonfinite metadata number")); }
                Ok(Json::Number(format!("{value:?}")))
            }
            6 => Ok(self.text(false)?.into()),
            tag @ (7 | 8) => {
                let count = self.u32()? as usize;
                if count > (self.data.len() - self.offset) / if tag == 8 { 5 } else { 1 } {
                    return Err(bad("invalid metadata container length"));
                }
                if tag == 7 {
                    let mut result = Vec::with_capacity(count);
                    for _ in 0..count { result.push(self.value(depth + 1)?); }
                    Ok(Json::Array(result))
                } else {
                    let mut result = BTreeMap::new();
                    for _ in 0..count {
                        let key = self.text(false)?;
                        if result.contains_key(&key) { return Err(bad("duplicate metadata key")); }
                        result.insert(key, self.value(depth + 1)?);
                    }
                    Ok(Json::Object(result))
                }
            }
            _ => Err(bad("unknown CENV/2 value tag")),
        }
    }
}

pub(crate) fn packet(h: &Json, body: &[u8], limit: usize) -> Result<Vec<u8>> {
    let fields = header(h)?;
    let operation = h.get("op")?.text()?;
    let mut writer = Writer(Vec::new());
    let mut code = 0;
    if let Some(&(candidate, _, required, optional)) = SCHEMAS.iter().find(|s| s.1 == operation) {
        let fits = |name: &str, text: bool| -> bool { fields.get(name).map_or(false, |v|
            if text { v.text().map_or(false, |s| s.len() <= 65535) } else { v.uint().is_ok() }) };
        if required.iter().all(|&(name, text)| fits(name, text)) &&
            fields.keys().all(|name| name == "op" || required.iter().chain(optional).any(|f| f.0 == name)) &&
            optional.iter().all(|&(name, text)| !fields.contains_key(name) || fits(name, text)) {
            code = candidate;
            if !optional.is_empty() {
                let flags = optional.iter().enumerate().fold(0u8, |f, (i, (name, _))|
                    f | if fields.contains_key(*name) { 1 << i } else { 0 });
                writer.add(&[flags])?;
            }
            for &(name, text) in required.iter().chain(optional) {
                if let Some(value) = fields.get(name) {
                    if text { writer.text(value.text()?, true)?; }
                    else { writer.add(&value.uint()?.to_be_bytes())?; }
                }
            }
        }
    }
    if code == 0 { writer.value(h, 0)?; }
    if PREFIX_SIZE + writer.0.len() + body.len() > limit {
        return Err(Error::new("oversize", "frame limit exceeded"));
    }
    let mut result = Vec::with_capacity(PREFIX_SIZE + writer.0.len() + body.len());
    result.extend_from_slice(MAGIC);
    result.push(code);
    result.extend_from_slice(&(writer.0.len() as u32).to_be_bytes());
    result.extend(writer.0);
    result.extend_from_slice(body);
    Ok(result)
}

pub(crate) fn unpack(packet: &[u8], limit: usize) -> Result<Packet> {
    if packet.len() < PREFIX_SIZE || packet.len() > limit { return Err(bad("invalid CENV/2 packet")); }
    if &packet[..3] != MAGIC { return Err(bad("unsupported CENV version; expected CENV/2")); }
    let code = packet[3];
    let size = u32::from_be_bytes(packet[4..8].try_into().unwrap()) as usize;
    if size > MAX_METADATA || size > packet.len() - PREFIX_SIZE { return Err(bad("metadata size")); }
    let end = PREFIX_SIZE + size;
    let mut reader = Reader { data: &packet[PREFIX_SIZE..end], offset: 0 };
    let result = if code == 0 { reader.value(0)? } else {
        let &(_, operation, required, optional) = SCHEMAS.iter().find(|s| s.0 == code)
            .ok_or_else(|| bad("unknown CENV/2 opcode"))?;
        let flags = if optional.is_empty() { 0 } else { reader.byte()? };
        if (flags as usize) >> optional.len() != 0 { return Err(bad("unknown CENV/2 flags")); }
        let mut fields = BTreeMap::new();
        fields.insert("op".into(), operation.into());
        for &(name, text) in required {
            fields.insert(name.into(), if text { reader.text(true)?.into() } else { reader.u64()?.into() });
        }
        for (index, &(name, text)) in optional.iter().enumerate() {
            if flags & (1 << index) != 0 {
                fields.insert(name.into(), if text { reader.text(true)?.into() } else { reader.u64()?.into() });
            }
        }
        Json::Object(fields)
    };
    if reader.offset != reader.data.len() { return Err(bad("trailing CENV/2 metadata")); }
    header(&result)?;
    Ok((result, packet[end..].to_vec()))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::json::{object, parse};

    fn hex(value: &str) -> Vec<u8> {
        value.as_bytes().chunks_exact(2).map(|pair|
            u8::from_str_radix(std::str::from_utf8(pair).unwrap(), 16).unwrap()).collect()
    }

    #[test]
    fn shared_python_rust_vectors_and_every_metadata_truncation() {
        let Json::Array(fixtures) = parse(include_bytes!("../tests/fixtures/cenv2.json")).unwrap()
            else { panic!("expected wire fixtures"); };
        for fixture in fixtures {
            let header = fixture.get("header").unwrap();
            let body = hex(fixture.get("body_hex").unwrap().text().unwrap());
            let expected = hex(fixture.get("packet_hex").unwrap().text().unwrap());
            assert_eq!(packet(header, &body, 1024 * 1024).unwrap(), expected);
            assert_eq!(unpack(&expected, 1024 * 1024).unwrap(), (header.clone(), body));
            let end = PREFIX_SIZE + u32::from_be_bytes(expected[4..8].try_into().unwrap()) as usize;
            for length in 0..end { assert!(unpack(&expected[..length], 1024 * 1024).is_err()); }
        }
    }

    fn frame(code: u8, metadata: &[u8]) -> Vec<u8> {
        let mut data = b"CE\x02".to_vec();
        data.push(code);
        data.extend((metadata.len() as u32).to_be_bytes());
        data.extend(metadata);
        data
    }

    #[test]
    fn rejects_obsolete_versions_unknown_flags_and_malformed_values() {
        let mut invalid = vec![b"CE\x01\0\0\0\x02{}".to_vec(), frame(99, &[]),
            frame(1, &[2]), frame(4, &[8]), frame(0, &[255]),
            frame(0, &[7, 255, 255, 255, 255]), frame(0, &[8, 255, 255, 255, 255]),
            frame(2, b"\0\0\0\0trailing")];
        let mut metadata = vec![5]; metadata.extend(f64::INFINITY.to_be_bytes());
        invalid.push(frame(0, &metadata));
        let mut metadata = vec![4]; metadata.extend(1i64.to_be_bytes());
        invalid.push(frame(0, &metadata));
        let mut metadata = vec![8, 0, 0, 0, 2];
        for _ in 0..2 { metadata.extend(b"\0\0\0\x02op\x06\0\0\0\x04test"); }
        invalid.push(frame(0, &metadata));
        let mut metadata = Vec::new();
        for _ in 0..66 { metadata.extend([7, 0, 0, 0, 1]); }
        metadata.push(0);
        invalid.push(frame(0, &metadata));
        for data in invalid { assert!(unpack(&data, 1024 * 1024).is_err(), "{data:?}"); }
    }

    #[test]
    fn whole_frame_limits_and_exact_application_ids() {
        for identity in [0, (1 << 53) + 1, u64::MAX] {
            let header = object([("op", "publish".into()), ("request", 1u64.into()),
                ("topic", "source.out".into()), ("app_id", identity.into())]);
            let bytes = packet(&header, &[0, 255], 1024).unwrap();
            assert_eq!(unpack(&bytes, bytes.len()).unwrap(), (header, vec![0, 255]));
            assert!(unpack(&bytes, bytes.len() - 1).is_err());
        }
        let header = object([("op", "publish".into()), ("request", 1u64.into()),
            ("topic", "source.out".into()), ("app_id", Json::Bool(true))]);
        assert!(packet(&header, &[], 1024).is_err());
        let header = object([("op", "control".into()), ("text", "x".repeat(MAX_METADATA - 36).into())]);
        let data = packet(&header, &[], 1024 * 1024).unwrap();
        assert_eq!(data.len(), MAX_METADATA + PREFIX_SIZE);
        assert!(packet(&header, &[0], data.len()).is_err());
    }
}
