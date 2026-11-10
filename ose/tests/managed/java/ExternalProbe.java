import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.math.BigInteger;
import org.sdlose.client.Client;
import sdl.globaltypes.measurement.Measurement;
public class ExternalProbe {
 public static void main(String[] args) throws Exception {
  Client client = Client.connect("127.0.0.1", Integer.parseInt(args[0]), "java-probe");
  try {
   Client.Publisher<Measurement> publisher = client.publisher("java.\uD83D\uDE80", Measurement.class);
   for (boolean udp : new boolean[]{false, true}) {
    final CountDownLatch done = new CountDownLatch(1);
    Client.Options options = udp ? Client.Options.latest() : Client.Options.fifo();
    options.udp = udp; options.ackReceived = true; options.maxRetries = 2;
    Client.Subscription sub = client.subscribe("java.\uD83D\uDE80", Measurement.class, options, new Client.Callback<Measurement>() {
     public void onMessage(Measurement value, Client.MessageInfo info) throws Exception {
      if (info.sequence.bitLength() != 64 || value.sequence != -1 || value.value != 3.5 || !info.topic.equals("java.\uD83D\uDE80")) throw new Exception("incorrect decoded data");
      done.countDown();
     }
    });
    Measurement value = new Measurement(); value.sequence = -1; value.value = 3.5;
    publisher.publish(value);
    if (!done.await(5, TimeUnit.SECONDS)) throw new Exception("delivery timed out");
    sub.stats(); sub.unsubscribe();
    if (!sub.waitClosed(3000)) throw new Exception("workers did not stop"); sub.close(); sub.close();
   }
   BigInteger maximum = BigInteger.ONE.shiftLeft(64).subtract(BigInteger.ONE);
   BigInteger[] identities = {null, BigInteger.ZERO, BigInteger.ONE.shiftLeft(53).add(BigInteger.ONE), maximum};
   for (boolean udp : new boolean[]{false, true}) {
    for (int index = 0; index < identities.length; ++index) {
     final BigInteger expected = identities[index];
     String topic = "java.ids." + udp + "." + index;
     Client.Publisher<Measurement> ids = client.publisher(topic, Measurement.class);
     final CountDownLatch done = new CountDownLatch(1);
     Client.Options options = Client.Options.fifo();
     options.udp = udp; options.ackReceived = true; options.maxRetries = 2;
     Client.Subscription sub = client.subscribe(topic, Measurement.class, options, new Client.Callback<Measurement>() {
      public void onMessage(Measurement value, Client.MessageInfo info) throws Exception {
       if (expected == null ? info.appId != null : !expected.equals(info.appId)) throw new Exception("incorrect application ID");
       done.countDown();
      }
     });
     Measurement value = new Measurement(); value.sequence = -1; value.value = 3.5;
     if (expected == null) ids.publish(value); else ids.publish(value, expected);
     if (!done.await(5, TimeUnit.SECONDS)) throw new Exception("ID delivery timed out");
     sub.unsubscribe(); if (!sub.waitClosed(3000)) throw new Exception("ID workers did not stop");
     sub.close(); ids.close();
    }
   }
   Measurement invalid = new Measurement(); invalid.sequence = 1; invalid.value = 1.;
   for (BigInteger id : new BigInteger[]{BigInteger.valueOf(-1), BigInteger.ONE.shiftLeft(64)}) {
    try { publisher.publish(invalid, id); throw new AssertionError("invalid ID accepted"); }
    catch (java.io.IOException expected) {
     if (!expected.getMessage().contains("uint64")) throw expected;
    }
   }
   if (!client.listTopics().contains("java.")) throw new Exception("missing topic");
   Client broken = Client.connect("127.0.0.1", Integer.parseInt(args[0]), "java-failure");
   try {
    Client.Subscription sub = broken.subscribe("java.\uD83D\uDE80", Measurement.class, Client.Options.fifo(), new Client.Callback<Measurement>() {
     public void onMessage(Measurement value, Client.MessageInfo info) throws Exception { throw new Exception("expected callback failure"); }
    });
    Measurement value = new Measurement(); value.sequence = -1; value.value = 3.5; publisher.publish(value);
    try { broken.run(); throw new AssertionError("failure was not propagated"); }
    catch (java.io.IOException expected) {
     if (expected.getCause() == null || !expected.getCause().getMessage().equals("expected callback failure")) throw expected;
    }
    sub.unsubscribe(); if (!sub.waitClosed(3000)) throw new Exception("failed subscription did not stop"); sub.close();
   } finally { broken.close(); }
   final Client concurrent = Client.connect("127.0.0.1", Integer.parseInt(args[0]), "java-concurrent-close");
   final java.util.concurrent.atomic.AtomicReference<Throwable> error = new java.util.concurrent.atomic.AtomicReference<Throwable>();
   Runnable close = new Runnable() { public void run() { try { concurrent.close(); } catch (Throwable e) { error.set(e); } } };
   Thread first = new Thread(close), second = new Thread(close); first.start(); second.start(); first.join(); second.join();
   if (error.get() != null) throw new Exception(error.get());
   publisher.close(); publisher.close();
  } finally { client.close(); client.close(); }
  System.out.println("java external OK");
 }
}
