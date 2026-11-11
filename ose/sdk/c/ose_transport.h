/* Private POSIX transport shared by the C SDK and the Java JNI bridge. */
#ifndef OSE_TRANSPORT_H
#define OSE_TRANSPORT_H
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdlib.h>
#include <sys/socket.h>
#include <unistd.h>

static inline int ose_transport_open(void) {
    const char *value = getenv("OSE_IPC_FD");
    char *end;
    long number;
    int fd, flags, type;
    socklen_t size = sizeof(type);
    struct sockaddr_storage address;
    socklen_t length = sizeof(address);
    if (!value || !*value) { errno = EINVAL; return -1; }
    for (const char *p = value; *p; ++p)
        if (*p < '0' || *p > '9') { errno = EINVAL; return -1; }
    errno = 0;
    number = strtol(value, &end, 10);
    if (errno || *end || number < 3 || number > INT_MAX) { errno = EINVAL; return -1; }
    fd = (int)number;
    if (getsockopt(fd, SOL_SOCKET, SO_TYPE, &type, &size) < 0 || type != SOCK_STREAM ||
        getpeername(fd, (struct sockaddr *)&address, &length) < 0 || address.ss_family != AF_UNIX) {
        errno = EINVAL; return -1;
    }
    flags = fcntl(fd, F_GETFD);
    if (flags < 0 || fcntl(fd, F_SETFD, flags | FD_CLOEXEC) < 0) return -1;
#ifdef SO_NOSIGPIPE
    { int enabled = 1;
      if (setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &enabled, sizeof(enabled)) < 0) return -1; }
#endif
    return fd;
}

static inline ssize_t ose_transport_read(int fd, void *data, size_t size) {
    ssize_t count;
    do { count = recv(fd, data, size, 0); } while (count < 0 && errno == EINTR);
    return count;
}

static inline ssize_t ose_transport_write(int fd, const void *data, size_t size) {
    ssize_t count;
#ifdef MSG_NOSIGNAL
    const int flags = MSG_NOSIGNAL;
#else
    const int flags = 0;
#endif
    do { count = send(fd, data, size, flags); } while (count < 0 && errno == EINTR);
    return count;
}
#endif
