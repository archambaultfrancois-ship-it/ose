"""Private AF_UNIX stream transport. Standard streams belong to the application."""
import os
import socket


def component_socket():
    """Take ownership of the launcher-provided descriptor; never fall back to stdio."""
    raw = os.environ.pop('OSE_IPC_FD', None)
    if raw is None or not raw.isascii() or not raw.isdecimal() or int(raw) < 3:
        raise ValueError('OSE_IPC_FD must identify an inherited Unix stream socket; rebuild/run with OSE')
    channel = socket.socket(fileno=int(raw))
    try:
        if channel.family != socket.AF_UNIX or channel.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE) != socket.SOCK_STREAM:
            raise ValueError('OSE_IPC_FD is not a Unix stream socket')
        channel.getpeername()
        channel.set_inheritable(False)
        channel.setblocking(True)
        return channel
    except BaseException:
        channel.close()
        raise
