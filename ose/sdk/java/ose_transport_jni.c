/* Byte transport only: CH/2 framing and decoding stay in Java. */
#include <jni.h>
#include <string.h>
#include "../c/ose_transport.h"

static void failure(JNIEnv *env, const char *message) {
    jclass type = (*env)->FindClass(env, "java/io/IOException");
    if (type) (*env)->ThrowNew(env, type, message);
}

JNIEXPORT jint JNICALL Java_org_sdlose_NativeTransport_open0(JNIEnv *env, jclass type) {
    int fd = ose_transport_open();
    (void)type;
    if (fd < 0) failure(env, "OSE_IPC_FD: expected an inherited Unix stream socket; rebuild/run with OSE");
    return fd;
}

JNIEXPORT jint JNICALL Java_org_sdlose_NativeTransport_read0(
    JNIEnv *env, jclass type, jint fd, jbyteArray bytes, jint offset, jint length) {
    jbyte buffer[16384];
    ssize_t count;
    (void)type;
    if (length > (jint)sizeof(buffer)) length = sizeof(buffer);
    /* Never pin the Java heap across blocking I/O. */
    count = ose_transport_read(fd, buffer, (size_t)length);
    if (count < 0) { failure(env, strerror(errno)); return -1; }
    if (!count) return -1;
    (*env)->SetByteArrayRegion(env, bytes, offset, (jsize)count, buffer);
    return (jint)count;
}

JNIEXPORT jint JNICALL Java_org_sdlose_NativeTransport_write0(
    JNIEnv *env, jclass type, jint fd, jbyteArray bytes, jint offset, jint length) {
    jbyte buffer[16384];
    ssize_t count;
    (void)type;
    if (length > (jint)sizeof(buffer)) length = sizeof(buffer);
    (*env)->GetByteArrayRegion(env, bytes, offset, length, buffer);
    if ((*env)->ExceptionCheck(env)) return -1;
    count = ose_transport_write(fd, buffer, (size_t)length);
    if (count <= 0) { failure(env, count < 0 ? strerror(errno) : "component IPC closed"); return -1; }
    return (jint)count;
}

JNIEXPORT void JNICALL Java_org_sdlose_NativeTransport_shutdown0(JNIEnv *env, jclass type, jint fd) {
    (void)env; (void)type;
    (void)shutdown(fd, SHUT_RDWR);
}

JNIEXPORT void JNICALL Java_org_sdlose_NativeTransport_close0(JNIEnv *env, jclass type, jint fd) {
    (void)env; (void)type;
    /* Do not retry close on EINTR: the descriptor may already have been reused. */
    (void)close(fd);
}
