"""Loopback stub: Anthropic Messages SSE + Bedrock ConverseStream (AWS event-stream).

First tool-bearing request streams a valid prefix then " \\n \\n" deltas at ~400 chars/s for
RUNAWAY_S seconds, then ends with stop_reason max_tokens. Later requests answer normally:
a clean `terminal` call, then a final text once a tool result is present.
Every request is logged as one JSON line to LOG.
"""
import binascii, json, struct, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT, LOG = int(sys.argv[1]), sys.argv[2]
RUNAWAY_S = float(sys.argv[3]) if len(sys.argv) > 3 else 60.0
MODE = sys.argv[4] if len(sys.argv) > 4 else "runaway"
# control: 5,000 spaces INSIDE a JSON string (valid content), streamed 4 chars at a time.
CONTROL = ['{"command": "echo \'a'] + ["    "] * 1250 + ['b\' | wc -c"}']
PREFIX = '{"command": "echo hi"'
CHUNK, PER_S = " \n \n", 100  # 4 chars x 100/s = 400 chars/s
lock = threading.Lock()
state = {"n": 0, "runaway_done": False}


def log(rec):
    with lock, open(LOG, "a") as f:
        f.write(json.dumps(rec) + "\n")


def has_tool_result_anthropic(body):
    return any(isinstance(m.get("content"), list) and any(b.get("type") == "tool_result" for b in m["content"])
               for m in body.get("messages", []))


def has_tool_result_converse(body):
    return any(any("toolResult" in b for b in m.get("content", [])) for m in body.get("messages", []))


def tool_result_text(body, key):
    for m in body.get("messages", []):
        for b in m.get("content", []) if isinstance(m.get("content"), list) else []:
            if key == "anthropic" and b.get("type") == "tool_result":
                c = b.get("content")
                return c if isinstance(c, str) else json.dumps(c)[:200]
            if key == "converse" and "toolResult" in b:
                return json.dumps(b["toolResult"].get("content"))[:200]
    return ""


# ---------- AWS event-stream encoding ----------
def _hdr(name, value):
    n, v = name.encode(), value.encode()
    return struct.pack("B", len(n)) + n + b"\x07" + struct.pack(">H", len(v)) + v


def es_event(event_type, payload):
    headers = _hdr(":event-type", event_type) + _hdr(":content-type", "application/json") + _hdr(":message-type", "event")
    body = json.dumps(payload).encode()
    total = 12 + len(headers) + len(body) + 4
    prelude = struct.pack(">II", total, len(headers))
    prelude += struct.pack(">I", binascii.crc32(prelude) & 0xFFFFFFFF)
    msg = prelude + headers + body
    return msg + struct.pack(">I", binascii.crc32(msg) & 0xFFFFFFFF)


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, *a):
        pass

    def _body(self):
        return json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")

    def do_POST(self):
        body = self._body()
        with lock:
            state["n"] += 1
            n = state["n"]
        if self.path.startswith("/v1/messages"):
            self._anthropic(n, body)
        elif "/converse-stream" in self.path:
            self._converse(n, body)
        elif self.path.endswith("/converse"):
            out = json.dumps({"output": {"message": {"role": "assistant", "content": [{"text": "Stub title"}]}},
                              "stopReason": "end_turn", "usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2},
                              "metrics": {"latencyMs": 1}}).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out))); self.end_headers(); self.wfile.write(out)
            log({"n": n, "path": self.path, "kind": "aux_nonstream"})
        else:
            self.send_response(404); self.end_headers()
            log({"n": n, "path": self.path, "status": 404})

    # ---------- Anthropic ----------
    def _sse(self, ev):
        self.wfile.write(f"event: {ev['type']}\ndata: {json.dumps(ev)}\n\n".encode())
        self.wfile.flush()

    def _anthropic(self, n, body):
        tools, tres = bool(body.get("tools")), has_tool_result_anthropic(body)
        with lock:
            runaway = tools and not tres and not state["runaway_done"]
            if runaway:
                state["runaway_done"] = True
        kind = "runaway" if runaway else "final_text" if (tres or not tools) else "clean_tool"
        rec = {"n": n, "t": time.time(), "wire": "anthropic", "kind": kind, "tools": tools, "tool_result": tres,
               "stream": bool(body.get("stream"))}
        if tres:
            rec["tool_result_seen"] = tool_result_text(body, "anthropic")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        t0, streamed = time.time(), 0
        first = CONTROL[0] if (kind == "runaway" and MODE == "control") else PREFIX
        try:
            self._sse({"type": "message_start", "message": {"id": f"msg_{n}", "type": "message", "role": "assistant",
                       "model": body.get("model", "m"), "content": [], "stop_reason": None, "stop_sequence": None,
                       "usage": {"input_tokens": 10, "output_tokens": 1}}})
            if kind == "final_text":
                text = "done: " + (rec.get("tool_result_seen", "") or "ok")[:80] if tres else "Stub title"
                self._sse({"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}})
                self._sse({"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}})
                self._sse({"type": "content_block_stop", "index": 0})
                stop = "end_turn"
            else:
                self._sse({"type": "content_block_start", "index": 0, "content_block":
                           {"type": "tool_use", "id": f"toolu_{n}", "name": "terminal", "input": {}}})
                self._sse({"type": "content_block_delta", "index": 0,
                           "delta": {"type": "input_json_delta", "partial_json": first}})
                streamed = len(first)
                if kind == "runaway" and MODE == "control":
                    for d in CONTROL[1:]:
                        self._sse({"type": "content_block_delta", "index": 0,
                                   "delta": {"type": "input_json_delta", "partial_json": d}})
                        streamed += len(d)
                        time.sleep(1 / PER_S)
                    stop = "tool_use"
                elif kind == "runaway":
                    while time.time() - t0 < RUNAWAY_S:
                        self._sse({"type": "content_block_delta", "index": 0,
                                   "delta": {"type": "input_json_delta", "partial_json": CHUNK}})
                        streamed += len(CHUNK)
                        time.sleep(1 / PER_S)
                    stop = "max_tokens"
                else:
                    self._sse({"type": "content_block_delta", "index": 0,
                               "delta": {"type": "input_json_delta", "partial_json": "}"}})
                    streamed += 1
                    stop = "tool_use"
                self._sse({"type": "content_block_stop", "index": 0})
            self._sse({"type": "message_delta", "delta": {"stop_reason": stop, "stop_sequence": None},
                       "usage": {"output_tokens": 5}})
            self._sse({"type": "message_stop"})
            rec.update(outcome="completed", stop=stop)
        except (BrokenPipeError, ConnectionResetError) as e:
            rec.update(outcome=f"client_closed:{type(e).__name__}")
        rec.update(chars_streamed=streamed, seconds=round(time.time() - t0, 2))
        log(rec)

    # ---------- Bedrock Converse ----------
    def _converse(self, n, body):
        tools, tres = bool(body.get("toolConfig")), has_tool_result_converse(body)
        with lock:
            runaway = tools and not tres and not state["runaway_done"]
            if runaway:
                state["runaway_done"] = True
        kind = "runaway" if runaway else "final_text" if (tres or not tools) else "clean_tool"
        rec = {"n": n, "t": time.time(), "wire": "converse", "path": self.path, "kind": kind, "tools": tools,
               "tool_result": tres}
        if tres:
            rec["tool_result_seen"] = tool_result_text(body, "converse")
        self.send_response(200)
        self.send_header("Content-Type", "application/vnd.amazon.eventstream")
        self.end_headers()
        t0, streamed = time.time(), 0
        first = CONTROL[0] if (kind == "runaway" and MODE == "control") else PREFIX

        def ev(t, p):
            self.wfile.write(es_event(t, p)); self.wfile.flush()
        try:
            ev("messageStart", {"role": "assistant"})
            if kind == "final_text":
                text = "done: " + (rec.get("tool_result_seen", "") or "ok")[:80] if tres else "Stub title"
                ev("contentBlockDelta", {"contentBlockIndex": 0, "delta": {"text": text}})
                ev("contentBlockStop", {"contentBlockIndex": 0})
                stop = "end_turn"
            else:
                ev("contentBlockStart", {"contentBlockIndex": 0, "start": {"toolUse": {"toolUseId": f"tooluse_{n}", "name": "terminal"}}})
                ev("contentBlockDelta", {"contentBlockIndex": 0, "delta": {"toolUse": {"input": first}}})
                streamed = len(first)
                if kind == "runaway" and MODE == "control":
                    for d in CONTROL[1:]:
                        ev("contentBlockDelta", {"contentBlockIndex": 0, "delta": {"toolUse": {"input": d}}})
                        streamed += len(d)
                        time.sleep(1 / PER_S)
                    stop = "tool_use"
                elif kind == "runaway":
                    while time.time() - t0 < RUNAWAY_S:
                        ev("contentBlockDelta", {"contentBlockIndex": 0, "delta": {"toolUse": {"input": CHUNK}}})
                        streamed += len(CHUNK)
                        time.sleep(1 / PER_S)
                    stop = "max_tokens"
                else:
                    ev("contentBlockDelta", {"contentBlockIndex": 0, "delta": {"toolUse": {"input": "}"}}})
                    streamed += 1
                    stop = "tool_use"
                ev("contentBlockStop", {"contentBlockIndex": 0})
            ev("messageStop", {"stopReason": stop})
            ev("metadata", {"usage": {"inputTokens": 10, "outputTokens": 5, "totalTokens": 15}, "metrics": {"latencyMs": 1}})
            rec.update(outcome="completed", stop=stop)
        except (BrokenPipeError, ConnectionResetError) as e:
            rec.update(outcome=f"client_closed:{type(e).__name__}")
        rec.update(chars_streamed=streamed, seconds=round(time.time() - t0, 2))
        log(rec)


ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
