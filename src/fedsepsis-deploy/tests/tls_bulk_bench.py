"""CPACF benchmark on OUR OWN mutual-TLS settings (loopback, one process, no patient data).

Server side mirrors aggregator/aggregator.py main(): SSLContext(PROTOCOL_TLS_SERVER), minimum TLS 1.3,
load_cert_chain, verify_mode CERT_REQUIRED, load_verify_locations(client CA).
Client side mirrors hospital/client.py make_opener(): create_default_context(cafile=CA), minimum TLS 1.3,
hostname checked, load_cert_chain (the site's own certificate). Only the cert pin is left out.

Prints: negotiated version and cipher, bulk MB/s over REPS transfers (median), mTLS handshakes/s, and the
SHAKE-256 absorb / squeeze speed that fedsecure's masks use. Run it twice (default and OPENSSL_s390xcap masked)
with tests/cpacf_bench.sh and compare. It does not read any CPACF counter, so it only shows what the library
CAN do with CPACF, not that a federated round did.
"""
import hashlib
import os
import socket
import ssl
import statistics
import threading
import time

D = os.environ.get("CERT_DIR", "/certs")
MB = int(os.environ.get("BENCH_MB", "400"))
REPS = int(os.environ.get("BENCH_REPS", "3"))
HS = int(os.environ.get("BENCH_HANDSHAKES", "60"))
HOST = "aggregator"          # name in the server certificate's SAN

sctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
sctx.minimum_version = ssl.TLSVersion.TLSv1_3
sctx.load_cert_chain(D + "/aggregator.crt", D + "/aggregator.key")
sctx.verify_mode = ssl.CERT_REQUIRED
sctx.load_verify_locations(cafile=D + "/ca.crt")

cctx = ssl.create_default_context(cafile=D + "/ca.crt")
cctx.minimum_version = ssl.TLSVersion.TLSv1_3
cctx.load_cert_chain(D + "/client.crt", D + "/client.key")

lsock = socket.socket()
lsock.bind(("127.0.0.1", 0))
lsock.listen(8)
PORT = lsock.getsockname()[1]
BLOCK = b"x" * 65536


def serve():
    while True:
        try:
            c, _ = lsock.accept()
        except OSError:
            return
        threading.Thread(target=handle, args=(c,), daemon=True).start()


def handle(c):
    try:
        t = sctx.wrap_socket(c, server_side=True)
        want = int.from_bytes(t.recv(8), "big")
        sent = 0
        while sent < want:
            t.sendall(BLOCK)
            sent += len(BLOCK)
        t.close()
    except Exception:
        c.close()


threading.Thread(target=serve, daemon=True).start()


def connect():
    return cctx.wrap_socket(socket.create_connection(("127.0.0.1", PORT)), server_hostname=HOST)


def bulk(mb):
    t = connect()
    t.sendall((mb * 1024 * 1024).to_bytes(8, "big"))
    t0 = time.perf_counter()
    n = 0
    while True:
        b = t.recv(1 << 20)
        if not b:
            break
        n += len(b)
    dt = time.perf_counter() - t0
    t.close()
    return n / dt / 1e6


t = connect()
print("negotiated:", t.version(), t.cipher()[0], "| peer cert verified, client cert presented (mutual TLS)")
t.sendall((0).to_bytes(8, "big"))
t.close()
print("openssl:", ssl.OPENSSL_VERSION, "| OPENSSL_s390xcap =", os.environ.get("OPENSSL_s390xcap") or "(unset, CPACF as detected)")

bulk(16)   # warm up
rates = [bulk(MB) for _ in range(REPS)]
print("mTLS bulk %d MB x%d: %s MB/s  median %.0f MB/s" % (MB, REPS, ", ".join("%.0f" % r for r in rates), statistics.median(rates)))

t0 = time.perf_counter()
for _ in range(HS):
    c = connect()
    c.sendall((0).to_bytes(8, "big"))
    c.close()
print("mTLS handshakes: %.0f/s (%d new connections)" % (HS / (time.perf_counter() - t0), HS))

buf = bytes(64 * 1024 * 1024)
t0 = time.perf_counter()
hashlib.shake_256(buf).digest(32)
print("SHAKE-256 absorb: %.0f MB/s" % (64 / (time.perf_counter() - t0)))
t0 = time.perf_counter()
hashlib.shake_256(b"seed|tag").digest(64 * 1024 * 1024)
print("SHAKE-256 squeeze (mask stream): %.0f MB/s" % (64 / (time.perf_counter() - t0)))
