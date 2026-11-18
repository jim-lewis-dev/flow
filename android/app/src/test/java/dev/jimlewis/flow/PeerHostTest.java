package dev.jimlewis.flow;

import org.junit.Rule;
import org.junit.Test;
import org.junit.rules.TemporaryFolder;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.KeyFactory;
import java.security.PrivateKey;
import java.security.Signature;
import java.security.cert.CertificateFactory;
import java.security.cert.X509Certificate;
import java.security.spec.PKCS8EncodedKeySpec;
import java.util.Base64;
import static org.junit.Assert.*;

public class PeerHostTest {
    @Rule public TemporaryFolder folder = new TemporaryFolder();

    @Test public void identityIsValidAndThePrivateKeyMatches() throws Exception {
        Path root = folder.getRoot().toPath(), cert = root.resolve("cert.pem"), key = root.resolve("key.pem");
        PeerHost.createIdentity(cert.toString(), key.toString());
        X509Certificate c;
        try (java.io.InputStream in = Files.newInputStream(cert)) {
            c = (X509Certificate) CertificateFactory.getInstance("X.509").generateCertificate(in);
        }
        c.checkValidity();
        c.verify(c.getPublicKey());
        String pem = new String(Files.readAllBytes(key), StandardCharsets.US_ASCII).replaceAll("-----[^\n]+-----", "");
        PrivateKey k = KeyFactory.getInstance("RSA").generatePrivate(new PKCS8EncodedKeySpec(Base64.getMimeDecoder().decode(pem)));
        byte[] input = new byte[]{1, 2, 3};
        Signature s = Signature.getInstance("SHA256withRSA");
        s.initSign(k); s.update(input); byte[] signed = s.sign();
        s.initVerify(c.getPublicKey()); s.update(input);
        assertTrue(s.verify(signed));
    }
}
