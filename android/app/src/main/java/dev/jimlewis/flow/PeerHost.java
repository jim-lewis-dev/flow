package dev.jimlewis.flow;

import android.content.Context;
import android.net.wifi.WifiManager;
import android.os.Build;
import java.io.ByteArrayOutputStream;
import java.math.BigInteger;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Paths;
import java.nio.file.StandardOpenOption;
import java.security.KeyPair;
import java.security.KeyPairGenerator;
import java.security.SecureRandom;
import java.security.Signature;
import java.text.SimpleDateFormat;
import java.util.Base64;
import java.util.Calendar;
import java.util.Date;
import java.util.Locale;
import java.util.TimeZone;
import javax.security.auth.x500.X500Principal;

/** Platform services called from the private Python peer worker. */
public final class PeerHost {
    private PeerHost() { }
    private static Context context;
    private static WifiManager.MulticastLock multicast;
    static synchronized void initialize(Context activity) {
        context = activity.getApplicationContext();
    }
    public static String deviceName() {
        return Build.MODEL == null || Build.MODEL.trim().isEmpty() ? "Flow phone" : Build.MODEL;
    }
    public static synchronized void discovery(boolean enable) {
        if (context == null) return;
        try {
            if (enable && multicast == null) {
                WifiManager wifi = context.getSystemService(WifiManager.class);
                if (wifi != null) {
                    WifiManager.MulticastLock lock = wifi.createMulticastLock("Flow:paired-devices");
                    lock.setReferenceCounted(false);
                    lock.acquire();
                    multicast = lock;
                }
            } else if (!enable && multicast != null) {
                if (multicast.isHeld()) multicast.release();
                multicast = null;
            }
        } catch (SecurityException ignored) {
            // Discovery is optional; direct address entry still works.
            multicast = null;
        }
    }

    // Only the fixed certificate envelope is encoded here. Android's JCA
    // provider generates the key, signs the certificate, and encodes both keys.
    private static byte[] der(int tag, byte[]... parts) {
        int size = 0;
        for (byte[] part : parts) size += part.length;
        ByteArrayOutputStream result = new ByteArrayOutputStream();
        result.write(tag);
        if (size < 128) result.write(size);
        else {
            int bytes = (32 - Integer.numberOfLeadingZeros(size) + 7) / 8;
            result.write(0x80 | bytes);
            for (int i = bytes - 1; i >= 0; i--) result.write(size >>> (8 * i));
        }
        for (byte[] part : parts) result.write(part, 0, part.length);
        return result.toByteArray();
    }
    private static byte[] time(long millis) {
        Calendar calendar = Calendar.getInstance(TimeZone.getTimeZone("UTC"), Locale.US);
        calendar.setTimeInMillis(millis);
        int year = calendar.get(Calendar.YEAR);
        boolean utcTime = year >= 1950 && year < 2050;
        SimpleDateFormat f = new SimpleDateFormat(utcTime ? "yyMMddHHmmss'Z'" : "yyyyMMddHHmmss'Z'", Locale.US);
        f.setTimeZone(calendar.getTimeZone());
        return der(utcTime ? 0x17 : 0x18, f.format(new Date(millis)).getBytes(StandardCharsets.US_ASCII));
    }
    private static void pem(String path, String type, byte[] encoded) throws Exception {
        String body = Base64.getMimeEncoder(64, new byte[]{10}).encodeToString(encoded);
        Files.write(Paths.get(path), ("-----BEGIN " + type + "-----\n" + body + "\n-----END " + type
            + "-----\n").getBytes(StandardCharsets.US_ASCII), StandardOpenOption.CREATE_NEW, StandardOpenOption.WRITE);
    }
    /** The caller holds the installation identity lock and supplies unused
     * names inside a mode-0700 temp directory. It validates and publishes both. */
    public static void createIdentity(String certificatePath, String keyPath) throws Exception {
        SecureRandom random = new SecureRandom();
        KeyPairGenerator generator = KeyPairGenerator.getInstance("RSA");
        generator.initialize(2048, random);
        KeyPair key = generator.generateKeyPair();
        byte[] algorithm = der(0x30, der(0x06, new byte[]{42, (byte)134, 72, (byte)134,
            (byte)247, 13, 1, 1, 11}), der(0x05)); // sha256WithRSAEncryption
        byte[] name = new X500Principal("CN=Flow peer").getEncoded();
        long now = System.currentTimeMillis();
        byte[] tbs = der(0x30, der(2, new BigInteger(127, random).add(BigInteger.ONE).toByteArray()),
            algorithm, name, der(0x30, time(now - 86400000L), time(now + 315576000000L)),
            name, key.getPublic().getEncoded());
        Signature signer = Signature.getInstance("SHA256withRSA");
        signer.initSign(key.getPrivate(), random);
        signer.update(tbs);
        pem(keyPath, "PRIVATE KEY", key.getPrivate().getEncoded());
        pem(certificatePath, "CERTIFICATE", der(0x30, tbs, algorithm, der(3, new byte[]{0}, signer.sign())));
    }
}
