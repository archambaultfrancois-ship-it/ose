use std::io;
use std::os::fd::{FromRawFd, RawFd};
use std::os::unix::net::UnixStream;
use std::sync::OnceLock;

static CHANNEL: OnceLock<Result<UnixStream, (io::ErrorKind, String)>> = OnceLock::new();

pub(crate) fn channel() -> io::Result<&'static UnixStream> {
    match CHANNEL.get_or_init(|| open().map_err(|error| (error.kind(), error.to_string()))) {
        Ok(stream) => Ok(stream),
        Err((kind, message)) => Err(io::Error::new(*kind, message.clone())),
    }
}

fn open() -> io::Result<UnixStream> {
    let raw = std::env::var("OSE_IPC_FD")
        .map_err(|_| io::Error::new(io::ErrorKind::InvalidInput, "missing OSE_IPC_FD; rebuild/run with OSE"))?;
    if raw.is_empty() || !raw.bytes().all(|b| b.is_ascii_digit()) {
        return Err(io::Error::new(io::ErrorKind::InvalidInput, "invalid OSE_IPC_FD"));
    }
    let fd: RawFd = raw.parse().ok().filter(|fd| *fd >= 3)
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "invalid OSE_IPC_FD"))?;
    // Reject stale/closed descriptors before assuming ownership (IO safety).
    unsafe extern "C" { fn fcntl(fd: i32, command: i32, ...) -> i32; }
    if unsafe { fcntl(fd, 1 /* F_GETFD */) } < 0 {
        return Err(io::Error::last_os_error());
    }
    // The launcher transfers ownership of exactly this descriptor. try_clone
    // duplicates it with CLOEXEC; dropping the original prevents exec leaks.
    let inherited = unsafe { UnixStream::from_raw_fd(fd) };
    inherited.peer_addr()?;
    let stream = inherited.try_clone()?;
    stream.set_nonblocking(false)?;
    Ok(stream)
}
