"""A pretend SMTP server on 127.0.0.1 for the tests: nothing ever leaves the machine.

It speaks just enough SMTP for smtplib: EHLO, STARTTLS (or TLS from the start), AUTH PLAIN/LOGIN,
MAIL, RCPT, DATA, QUIT. Everything it hears is kept: ``messages`` (parsed), ``plain_lines`` (what
came before the connection was encrypted) and ``logins``."""
from __future__ import annotations

import base64
import datetime
import ipaddress
import socket
import socketserver
import ssl
import threading
from email import message_from_bytes, policy
from pathlib import Path


def make_cert(folder: Path) -> tuple[Path, Path]:
    """A self-signed certificate for 127.0.0.1: (cert.pem, key.pem)."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=2))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), False)
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), True)
            .sign(key, hashes.SHA256()))
    cert_pem, key_pem = folder / "cert.pem", folder / "key.pem"
    cert_pem.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_pem.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                          serialization.NoEncryption()))
    return cert_pem, key_pem


class FakeSMTP:
    def __init__(self, folder: Path, *, user: str = "coach@example.test", password: str = "app-pass-123",
                 implicit_tls: bool = False, starttls: bool = True, refuse_rcpt: bool = False) -> None:
        self.user, self.password = user, password
        self.implicit_tls, self.starttls, self.refuse_rcpt = implicit_tls, starttls, refuse_rcpt
        self.cert, key = make_cert(folder)
        self.server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.server_ctx.load_cert_chain(self.cert, key)
        self.messages: list = []
        self.envelopes: list[tuple[str, list[str]]] = []
        self.plain_lines: list[bytes] = []
        self.logins: list[tuple[str, str]] = []
        outer = self

        class Handler(socketserver.BaseRequestHandler):
            def handle(self) -> None:
                outer._serve(self.request)

        self._srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
        self._srv.daemon_threads = True
        self.port = self._srv.server_address[1]
        self._thread = threading.Thread(target=self._srv.serve_forever, daemon=True)

    def client_context(self) -> ssl.SSLContext:
        """What a client needs to trust this server (instead of the system's certificates)."""
        return ssl.create_default_context(cafile=str(self.cert))

    def __enter__(self) -> "FakeSMTP":
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._srv.shutdown()
        self._srv.server_close()

    def _serve(self, sock: socket.socket) -> None:
        secure = False
        try:
            if self.implicit_tls:
                sock = self.server_ctx.wrap_socket(sock, server_side=True)
                secure = True
            f = sock.makefile("rb")
            send = lambda s: sock.sendall(s.encode() + b"\r\n")     # noqa: E731
            send("220 fake ESMTP")
            authed, sender, rcpts = False, "", []
            while True:
                line = f.readline()
                if not line:
                    return
                if not secure:
                    self.plain_lines.append(line)
                cmd = line.strip().decode("utf-8", "replace")
                up = cmd.upper()
                if up.startswith(("EHLO", "HELO")):
                    ext = ["250-fake"] + (["250-STARTTLS"] if self.starttls and not secure else [])
                    send("\r\n".join(ext + ["250 AUTH PLAIN LOGIN"]))
                elif up == "STARTTLS" and self.starttls and not secure:
                    send("220 go ahead")
                    sock = self.server_ctx.wrap_socket(sock, server_side=True)
                    f, secure = sock.makefile("rb"), True
                elif up.startswith("AUTH PLAIN"):
                    _, user, pw = base64.b64decode(cmd.split()[2]).decode().split("\0")
                    self.logins.append((user, pw))
                    authed = (user, pw) == (self.user, self.password)
                    send("235 ok" if authed else "535 5.7.8 Username and Password not accepted")
                elif up.startswith("MAIL FROM"):
                    if not authed:
                        send("530 5.7.0 Authentication required")
                        continue
                    sender, rcpts = cmd.split(":", 1)[1].split()[0].strip("<>"), []
                    send("250 ok")
                elif up.startswith("RCPT TO"):
                    if self.refuse_rcpt:
                        send("550 5.1.1 no such user")
                        continue
                    rcpts.append(cmd.split(":", 1)[1].strip().strip("<>"))
                    send("250 ok")
                elif up == "DATA":
                    send("354 end with .")
                    data = b""
                    while True:
                        ln = f.readline()
                        if ln in (b".\r\n", b""):
                            break
                        data += ln[1:] if ln.startswith(b"..") else ln
                    self.messages.append(message_from_bytes(data, policy=policy.default))
                    self.envelopes.append((sender, rcpts))
                    send("250 queued")
                elif up == "QUIT":
                    send("221 bye")
                    return
                elif up == "RSET":
                    send("250 ok")
                else:
                    send("502 not implemented")
        except (OSError, ssl.SSLError, ValueError):
            return
        finally:
            try:
                sock.close()
            except OSError:
                pass
