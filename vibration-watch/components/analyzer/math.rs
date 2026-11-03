pub fn summarize(samples: &[f32]) -> Result<(f64, f64), &'static str> {
    if samples.is_empty() {
        return Err("empty signal block");
    }
    let mut squares = 0.0;
    let mut peak: f64 = 0.0;
    for &sample in samples {
        let value = f64::from(sample);
        if !value.is_finite() {
            return Err("non-finite signal sample");
        }
        squares += value * value;
        peak = peak.max(value.abs());
    }
    Ok(((squares / samples.len() as f64).sqrt(), peak))
}

#[cfg(test)]
mod tests {
    use super::summarize;
    #[test]
    fn signed_samples_and_peak() {
        assert_eq!(summarize(&[-3.0, 3.0]), Ok((3.0, 3.0)));
        assert_eq!(summarize(&[0.0, 0.0]), Ok((0.0, 0.0)));
    }
    #[test]
    fn rejects_invalid_blocks() {
        assert!(summarize(&[]).is_err());
        assert!(summarize(&[f32::NAN]).is_err());
        assert!(summarize(&[f32::INFINITY]).is_err());
    }
}
