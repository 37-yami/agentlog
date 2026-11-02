# -*- coding: utf-8 -*-
# agentlog local proxy.
#
# Sits between CLI agents and their model APIs. The agent is pointed at
# http://127.0.0.1:<port>/<agent>[/<upstream-host>] and every request and
# response is forwarded unchanged while one turn (user prompt + assistant
# reply) is appended to capture/<agent>/<YYYYMMDD>.jsonl, normalized.
#
# Routing:
#   /<agent>/<host>/<api-path>  -> forward to https://<host>/<api-path>
#   /<agent>/<api-path>         -> forward to the agent default upstream
#                                  (DEFAULT_UPSTREAMS), 404 if none.
#
# Recording covers:
#   Anthropic  POST .../v1/messages          (stream + non-stream)
#   OpenAI     POST .../v1/chat/completions  (stream + non-stream)
# Everything else is forwarded untouched. Stdlib only.
#
# Standalone:  python agentlog_proxy.py [port]
# Or run inside the agentlog daemon (started in a thread automatically).
import http.client
import json
import os
import re
import ssl
import sys
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
CAPTURE_DIR = os.path.join(HERE, "capture")
PROXY_PORT = int(os.environ.get("AGENTLOG_PROXY_PORT", "8765"))

# Per-agent default upstream hosts (used when the URL has no host segment).
# Use the /<agent>/<host>/... URL form for relays or non-default providers.
DEFAULT_UPSTREAMS = {
    "claude": "api.anthropic.com",
    "codebuddy": None,
    "opencode": None,
    "pi": None,
}

HOST_RE = re.compile(r"^[A-Za-z0-9._-]+(:\d+)?$")
# Working directory as stated by the agent inside its system prompt
# (Claude Code style "Working directory: E:\path", markdown headings and
# <cwd> tags included).
CWD_RE = re.compile(
    r"(?:Working directory|Current (?:working )?directory|工作目录)"
    r"[:：]\s*([A-Za-z]:[^\r\n\s\"'`]*|/[^\r\n\s\"'`]*)"
)
CWD_TAG_RE = re.compile(r"<cwd>([^<\r\n]+)</cwd>")

_write_lock = threading.Lock()


def norm_cwd(s):
    """Normalize a working-directory string so the same project always maps
    to the same key regardless of separator style ('E:/a/b' vs 'E:\\a\\b')
    or a trailing slash. Platform-aware: POSIX paths keep forward slashes.
    Keep agentlog.norm_cwd in sync."""
    if not s:
        return s
    if os.name == "nt":
        s = s.replace("/", "\\")
        s = os.path.normpath(s)
        return s.rstrip("\\")
    s = os.path.normpath(s)
    return s


# ---------------------------------------------------------------------------
# capture store
# ---------------------------------------------------------------------------
def _extract_cwd(body):
    # Best-effort working directory from the request payload.
    texts = []
    sysv = body.get("system")
    if isinstance(sysv, str):
        texts.append(sysv)
    elif isinstance(sysv, list):
        for b in sysv:
            if isinstance(b, dict) and b.get("text"):
                texts.append(str(b["text"]))
    for m in body.get("messages") or []:
        if isinstance(m, dict) and m.get("role") == "system":
            c = m.get("content")
            if isinstance(c, str):
                texts.append(c)
            elif isinstance(c, list):
                for b in c:
                    if isinstance(b, dict) and b.get("text"):
                        texts.append(str(b["text"]))
    for t in texts:
        m = CWD_RE.search(t) or CWD_TAG_RE.search(t)
        if m:
            return norm_cwd(m.group(1))
    return None


def _last_user_text(body):
    for m in reversed(body.get("messages") or []):
        if not isinstance(m, dict) or m.get("role") != "user":
            continue
        c = m.get("content")
        if isinstance(c, str):
            return c.strip()
        if isinstance(c, list):
            parts = [b.get("text", "") for b in c
                     if isinstance(b, dict) and b.get("type") in ("text", "input_text")]
            t = "\n".join(p for p in parts if p).strip()
            if t:
                return t
    return ""


def record_turn(agent, cwd, user_text, assistant_text, model, minor):
    # Append one normalized Q/A turn to capture/<agent>/<YYYYMMDD>.jsonl.
    if not user_text and not assistant_text:
        return
    try:
        adir = os.path.join(CAPTURE_DIR, agent)
        os.makedirs(adir, exist_ok=True)
        day = datetime.now().strftime("%Y%m%d")
        ev = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "agent": agent,
            "cwd": cwd,
            "model": model,
            "minor": bool(minor),
            "user": user_text,
            "assistant": assistant_text,
        }
        line = json.dumps(ev, ensure_ascii=False) + "\n"
        with _write_lock:
            with open(os.path.join(adir, day + ".jsonl"), "a", encoding="utf-8") as fh:
                fh.write(line)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# response body parsers (accumulate while streaming through)
# ---------------------------------------------------------------------------
class _AnthropicStream(object):
    def __init__(self):
        self.text = []
        self.done = False

    def feed_event(self, data):
        etype = data.get("type")
        if etype == "content_block_delta":
            d = data.get("delta") or {}
            if d.get("type") == "text_delta" and d.get("text"):
                self.text.append(d["text"])
        elif etype == "message_stop":
            self.done = True

    def result(self):
        return "".join(self.text)


class _OpenAIStream(object):
    def __init__(self):
        self.text = []
        self.done = False

    def feed_event(self, data):
        if data.get("choices"):
            d = (data["choices"][0] or {}).get("delta") or {}
            if d.get("content"):
                self.text.append(d["content"])
            if data["choices"][0].get("finish_reason"):
                self.done = True

    def result(self):
        return "".join(self.text)


def _parse_anthropic_request(body):
    # minor = background/utility call. Requires BOTH no tools and a tiny
    # message list: tools-less real conversations do exist, so the tools
    # flag alone would silently drop them.
    minor = (not body.get("tools")
             and len(body.get("messages") or []) < 3)
    return (_last_user_text(body), _extract_cwd(body),
            body.get("model"), minor)


def _parse_anthropic_response(body):
    parts = [b.get("text", "") for b in body.get("content") or []
             if isinstance(b, dict) and b.get("type") == "text"]
    return "".join(p for p in parts if p).strip()


def _parse_openai_request(body):
    cwd = _extract_cwd(body)
    # background/utility calls: no tools and few messages
    minor = not body.get("tools") and len(body.get("messages") or []) < 3
    return (_last_user_text(body), cwd, body.get("model"), minor)


def _parse_openai_response(body):
    try:
        return ((body["choices"][0].get("message") or {}).get("content") or "").strip()
    except (KeyError, IndexError, TypeError):
        return ""


def _split_event(buf):
    i = buf.index(b"\n\n")
    return buf[:i], buf[i + 2:]


# ---------------------------------------------------------------------------
# proxy handler
# ---------------------------------------------------------------------------
class ProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # quiet; daemon writes agentlog.log
        pass

    def _upstream_and_path(self):
        parts = self.path.lstrip("/").split("/")
        agent = parts[0] if parts else ""
        rest = parts[1:]
        upstream = None
        if rest and "." in rest[0] and HOST_RE.match(rest[0]):
            upstream = rest[0]
            rest = rest[1:]
        else:
            upstream = DEFAULT_UPSTREAMS.get(agent)
        upstream_port = 443
        if isinstance(upstream, str) and ":" in upstream:
            upstream, _, port = upstream.partition(":")
            upstream_port = int(port)
        return agent, upstream, upstream_port, "/" + "/".join(rest)

    def _read_body(self):
        te = (self.headers.get("Transfer-Encoding") or "").lower()
        if "chunked" in te:
            chunks = []
            while True:
                line = self.rfile.readline(65536).strip()
                try:
                    n = int(line.split(b";")[0], 16)
                except ValueError:
                    break
                if n == 0:
                    self.rfile.readline(65536)
                    break
                chunks.append(self.rfile.read(n))
                self.rfile.readline(65536)
            return b"".join(chunks)
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n > 0 else b""

    @staticmethod
    def _is_recordable(path):
        return (path.endswith("/v1/messages")
                or path.endswith("/v1/chat/completions"))

    @staticmethod
    def _is_anthropic(path):
        return path.endswith("/v1/messages")

    def do_POST(self):
        self._forward()

    def do_GET(self):
        self._forward()

    def do_PUT(self):
        self._forward()

    def do_DELETE(self):
        self._forward()

    def do_PATCH(self):
        self._forward()

    def do_OPTIONS(self):
        self._forward()

    def _forward(self):
        agent, upstream, upstream_port, fwd_path = self._upstream_and_path()
        body = self._read_body()
        if not upstream:
            self._respond_error(404, "agentlog proxy: no upstream for /"
                                + agent + "/ - use /" + agent + "/<host>/...")
            return

        # --- prep recording context ---
        rec = None
        if self.command == "POST" and self._is_recordable(fwd_path):
            try:
                req = json.loads(body.decode("utf-8"))
                if self._is_anthropic(fwd_path):
                    user, cwd, model, minor = _parse_anthropic_request(req)
                    parser = _AnthropicStream()
                else:
                    user, cwd, model, minor = _parse_openai_request(req)
                    parser = _OpenAIStream()
                rec = {"user": user, "cwd": cwd, "model": model,
                       "minor": minor, "parser": parser}
            except Exception:
                rec = None  # unparsable body: forward untouched

        # --- forward ---
        hdrs = {}
        for k, v in self.headers.items():
            if k.lower() in ("host", "connection", "accept-encoding",
                             "content-length", "transfer-encoding",
                             "proxy-connection", "keep-alive"):
                continue
            hdrs[k] = v
        hdrs["Host"] = upstream
        hdrs["Accept-Encoding"] = "identity"
        try:
            if upstream in ("127.0.0.1", "localhost"):
                # plain HTTP loopback: lets tests (or local gateways) plug in
                conn = http.client.HTTPConnection(
                    upstream, upstream_port, timeout=600)
            else:
                conn = http.client.HTTPSConnection(
                    upstream, upstream_port, timeout=600,
                    context=ssl.create_default_context())
            conn.request(self.command, fwd_path, body=body, headers=hdrs)
            resp = conn.getresponse()
        except Exception as e:
            self._respond_error(502, "agentlog proxy: upstream error: " + str(e))
            return

        # --- relay response (recording when applicable) ---
        resp_headers = [(k, v) for k, v in resp.getheaders()
                        if k.lower() not in ("connection", "content-length",
                                             "transfer-encoding", "keep-alive")]
        ctype = (resp.getheader("Content-Type") or "").lower()
        streaming = "text/event-stream" in ctype
        self.send_response(resp.status)
        for k, v in resp_headers:
            self.send_header(k, v)
        if streaming:
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
        else:
            data = resp.read()
            if rec is not None:
                data = self._record_buffered(agent, rec, fwd_path, data)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            try:
                conn.close()
            except Exception:
                pass
            return

        if streaming:
            buf = bytearray()
            try:
                while True:
                    chunk = resp.read1(65536)
                    if not chunk:
                        break
                    buf.extend(chunk)
                    self._write_chunk(bytes(chunk))
                    if rec is not None:
                        self._feed_sse(rec, buf)
            except Exception:
                pass
            self._write_chunk(b"")  # terminating chunk
            if rec is not None:
                self._finish_stream(agent, rec, buf)
        try:
            conn.close()
        except Exception:
            pass

    # -- chunked writer ----------------------------------------------------
    def _write_chunk(self, chunk):
        if chunk:
            self.wfile.write(("%X\r\n" % len(chunk)).encode() + chunk + b"\r\n")
        else:
            self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()

    # -- recording helpers ---------------------------------------------------
    def _feed_sse(self, rec, buf):
        # Parse complete SSE events out of buf and feed the stream parser.
        # Only new events are consumed; buf keeps the (possibly incomplete)
        # tail. buf is a bytearray, mutated in place.
        parser = rec["parser"]
        while b"\n\n" in buf:
            raw, rest = _split_event(buf)
            buf[:] = rest
            for line in raw.split(b"\n"):
                if line.startswith(b"data:"):
                    payload = line[5:].strip()
                    if not payload or payload == b"[DONE]":
                        continue
                    try:
                        parser.feed_event(json.loads(payload.decode("utf-8")))
                    except Exception:
                        pass

    def _finish_stream(self, agent, rec, buf):
        try:
            self._feed_sse(rec, buf + b"\n\n")  # trailing event w/o separator
        except Exception:
            pass
        record_turn(agent, rec["cwd"], rec["user"],
                    rec["parser"].result(), rec["model"], rec["minor"])

    def _record_buffered(self, agent, rec, fwd_path, data):
        try:
            if self._is_anthropic(fwd_path):
                text = _parse_anthropic_response(json.loads(data.decode("utf-8")))
            else:
                text = _parse_openai_response(json.loads(data.decode("utf-8")))
            record_turn(agent, rec["cwd"], rec["user"], text,
                        rec["model"], rec["minor"])
        except Exception:
            pass
        return data

    def _respond_error(self, code, msg):
        payload = json.dumps({"error": {"message": msg}}).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


_server = None


def serve(port=None):
    # Start the proxy server (blocking). Called from a daemon thread.
    global _server
    if _server is not None:
        return
    srv = ThreadingHTTPServer(("127.0.0.1", port or PROXY_PORT), ProxyHandler)
    srv.daemon_threads = True
    _server = srv
    srv.serve_forever(poll_interval=0.5)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else PROXY_PORT
    print("agentlog proxy listening on http://127.0.0.1:%d" % port)
    print("route form: http://127.0.0.1:%d/<agent>[/<upstream-host>]/..." % port)
    serve(port)
