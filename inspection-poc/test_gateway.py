#!/usr/bin/env python3
"""Exercise the actual pinned gateway with local synthetic HTTP fixtures."""

import argparse
from contextlib import closing
import http.client
import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from configure import BUFFER_LIMIT, INSPECT_HOSTS, configuration

SENTINEL = "DO_NOT_PERSIST_SYNTHETIC_SECRET_8359"
ARGS = None


class Fixture(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def handle(self):
        try:
            super().handle()
        except ConnectionResetError:
            # The cancellation test intentionally resets a live stream.
            pass

    def log_message(self, *_):
        pass

    seen = []

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        Fixture.seen.append((self.path, {k.lower(): v for k, v in self.headers.items()}, body))
        if self.path == "/v1/messages":
            data = json.dumps({
                "id": "msg_fixture", "type": "message", "role": "assistant",
                "model": "fixture-served-claude", "content": [], "stop_reason": "end_turn",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        elif self.path == "/responses":
            data = json.dumps({
                "id": "resp_fixture", "object": "response", "created_at": 0,
                "model": "fixture-served-model", "status": "completed", "output": [],
                "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        elif self.path == "/stream":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            try:
                for _ in range(3):
                    data = b'data: {"content":"SYNTHETIC_RESPONSE"}\n\n'
                    self.wfile.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")
                    self.wfile.flush()
                    time.sleep(0.1)
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
        else:
            data = b'{"ok":true}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)


class GatewayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if ARGS is None:
            raise unittest.SkipTest("Run test_gateway.py with --gateway and --ca-dir for integration tests")
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.ca = Path(ARGS.ca_dir).resolve()
        config = configuration(str(cls.ca))
        config["binds"][0]["port"] = 23138
        config["config"]["statsAddr"] = "off"
        config["config"]["readinessAddr"] = "off"
        for listener in config["binds"][1]["listeners"][:-1]:
            ai = listener["routes"][0]["backends"][0]["ai"]
            ai["hostOverride"] = f"127.0.0.1:{cls.server.server_port}"
            ai.pop("policies")
        cls.config_file = cls.root / "fixture.json"
        cls.config_file.write_text(json.dumps(config))
        cls.log_path = cls.root / "gateway.log"
        cls.log = cls.log_path.open("w")
        cls.process = subprocess.Popen(
            [str(Path(ARGS.gateway).resolve()), "-f", str(cls.config_file)],
            stdout=cls.log,
            stderr=subprocess.STDOUT,
        )
        for _ in range(100):
            if cls.process.poll() is not None:
                raise RuntimeError("Gateway failed to start: " + cls.log_path.read_text()[:2000])
            try:
                with socket.create_connection(("127.0.0.1", 23138), timeout=0.1):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError("Gateway listener did not become ready")

    @classmethod
    def tearDownClass(cls):
        cls.process.terminate()
        cls.process.wait(timeout=10)
        cls.server.shutdown()
        cls.server.server_close()
        cls.log.close()
        cls.temp.cleanup()

    def connection(self, host=INSPECT_HOSTS[0], port=443):
        conn = http.client.HTTPSConnection(
            "127.0.0.1", 23138, timeout=8,
            context=ssl.create_default_context(cafile=str(self.ca / "ca.crt")),
        )
        conn.set_tunnel(host, port)
        return closing(conn)

    def exchange(self, body, content_type="application/json", path="/test", host_header=None, extra=None):
        with self.connection() as conn:
            headers = {"Content-Type": content_type, "Authorization": "Bearer " + SENTINEL, **(extra or {})}
            if host_header:
                headers["Host"] = host_header
            conn.request("POST", path, body=body, headers=headers)
            response = conn.getresponse()
            result = response.status
            response.read()
        return result

    def records(self):
        records = []
        for line in self.log_path.read_text().splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            # The pinned binary flattens custom fields in JSON logs.
            if row.get("poc_schema") == "copilot-model-v1":
                records.append(row)
        return records

    def last_record(self, before):
        for _ in range(60):
            rows = self.records()
            if len(rows) > before:
                return rows[-1]
            time.sleep(0.05)
        self.fail("No metadata record emitted")

    def assert_capture(self, payload, expected, model=None, content_type="application/json", extra=None):
        before = len(self.records())
        self.assertEqual(self.exchange(payload, content_type, extra=extra), 200)
        record = self.last_record(before)
        self.assertEqual(record.get("model_capture_status"), expected, record)
        self.assertEqual(record.get("requested_model_raw"), model, record)

    def test_present(self):
        self.assert_capture(json.dumps({"model": "fixture-exact-model", "messages": [SENTINEL]}),
                            "captured", "fixture-exact-model")

    def test_missing(self):
        self.assert_capture('{"messages":[]}', "not_present")

    def test_malformed(self):
        self.assert_capture('{"model":', "parse_error")

    def test_shape(self):
        self.assert_capture('["model"]', "invalid_json_shape")

    def test_json_null(self):
        self.assert_capture(" null ", "invalid_json_shape")

    def test_type(self):
        self.assert_capture('{"model":42}', "invalid_model_type")

    def test_content_type(self):
        self.assert_capture("fixture", "unsupported_content_type", content_type="text/plain")

    def test_compressed_body(self):
        import gzip
        self.assert_capture(gzip.compress(b'{"model":"fixture-gzip"}'), "compressed_body",
                            extra={"Content-Encoding": "gzip"})

    def assert_responses(self, raw, encoding=None):
        before = len(self.records())
        Fixture.seen.clear()
        extra = {"Content-Encoding": encoding} if encoding else None
        self.assertEqual(self.exchange(raw, path="/responses", extra=extra), 200)
        record = self.last_record(before)
        self.assertEqual(record.get("model_capture_status"), "captured", record)
        self.assertEqual(record.get("requested_model_raw"), "fixture-responses-model", record)
        self.assertEqual(record.get("model_source"), "llm.requestModel", record)
        self.assertEqual(record.get("response_model_raw"), "fixture-served-model", record)
        path, headers, body = Fixture.seen[-1]
        self.assertEqual(path, "/responses")
        self.assertEqual(headers.get("authorization"), "Bearer " + SENTINEL)
        self.assertEqual(json.loads(body)["model"], "fixture-responses-model")

    RESPONSES = json.dumps({"model": "fixture-responses-model", "input": SENTINEL, "stream": False}).encode()

    def test_responses_plain(self):
        self.assert_responses(self.RESPONSES)

    def test_responses_gzip(self):
        import gzip
        self.assert_responses(gzip.compress(self.RESPONSES), "gzip")

    def test_responses_zstd(self):
        import shutil
        if not shutil.which("zstd"):
            self.skipTest("zstd CLI not installed")
        raw = subprocess.run(["zstd", "-q", "-c"], input=self.RESPONSES, capture_output=True, check=True).stdout
        self.assert_responses(raw, "zstd")

    def responses_payload_of_size(self, size):
        prefix = b'{"model":"fixture-limit","input":"'
        suffix = b'","stream":false}'
        return prefix + b"x" * (size - len(prefix) - len(suffix)) + suffix

    def test_responses_at_buffer_limit(self):
        before = len(self.records())
        payload = self.responses_payload_of_size(BUFFER_LIMIT)
        self.assertEqual(len(payload), BUFFER_LIMIT)
        self.assertEqual(self.exchange(payload, path="/responses"), 200)
        record = self.last_record(before)
        self.assertEqual(record.get("model_capture_status"), "captured", record)
        self.assertEqual(record.get("requested_model_raw"), "fixture-limit", record)
        self.assertEqual(record.get("model_source"), "llm.requestModel", record)

    def assert_responses_over_limit(self, raw, extra=None):
        before = len(self.records())
        Fixture.seen.clear()
        self.assertEqual(self.exchange(raw, path="/responses", extra=extra), 413)
        record = self.last_record(before)
        self.assertEqual(record.get("model_capture_status"), "http_413_no_body_captured", record)
        self.assertIsNone(record.get("requested_model_raw"), record)
        self.assertFalse(Fixture.seen, "Oversized request reached the upstream fixture")

    def test_responses_over_buffer_limit(self):
        payload = self.responses_payload_of_size(BUFFER_LIMIT + 1)
        self.assert_responses_over_limit(payload)

    def test_responses_zstd_over_buffer_limit(self):
        import shutil
        if not shutil.which("zstd"):
            self.skipTest("zstd CLI not installed")
        payload = self.responses_payload_of_size(BUFFER_LIMIT + 1)
        raw = subprocess.run(["zstd", "-q", "-c"], input=payload, capture_output=True, check=True).stdout
        self.assert_responses_over_limit(raw, extra={"Content-Encoding": "zstd"})

    def test_messages_native_zstd(self):
        import shutil
        if not shutil.which("zstd"):
            self.skipTest("zstd CLI not installed")
        payload = {"model": "fixture-claude", "max_tokens": 8, "stream": False,
                   "messages": [{"role": "user", "content": SENTINEL}]}
        raw = subprocess.run(["zstd", "-q", "-c"], input=json.dumps(payload).encode(),
                             capture_output=True, check=True).stdout
        before = len(self.records())
        Fixture.seen.clear()
        self.assertEqual(self.exchange(raw, path="/v1/messages", extra={"Content-Encoding": "zstd"}), 200)
        record = self.last_record(before)
        self.assertEqual(record.get("requested_model_raw"), "fixture-claude", record)
        self.assertEqual(record.get("response_model_raw"), "fixture-served-claude", record)
        path, headers, body = Fixture.seen[-1]
        self.assertEqual(path, "/v1/messages")
        self.assertEqual(headers.get("authorization"), "Bearer " + SENTINEL)
        self.assertNotIn("x-api-key", headers)
        forwarded = json.loads(body)
        self.assertIn("messages", forwarded)
        self.assertNotIn("choices", forwarded)

    def test_empty_body(self):
        self.assert_capture("", "no_body")

    def test_below_limit(self):
        payload = '{"model":"fixture-limit","pad":"' + "x" * (BUFFER_LIMIT - 35) + '"}'
        self.assertLess(len(payload), BUFFER_LIMIT)
        self.assert_capture(payload, "captured", "fixture-limit")

    def test_over_limit(self):
        payload = '{"pad":"' + "x" * BUFFER_LIMIT + '","model":"fixture-overflow"}'
        self.assert_capture(payload, "capture_limit_unknown")

    def test_exact_limit_is_explicitly_uncertain(self):
        prefix, suffix = '{"model":"fixture-limit","pad":"', '"}'
        payload = prefix + "x" * (BUFFER_LIMIT - len(prefix) - len(suffix)) + suffix
        self.assertEqual(len(payload), BUFFER_LIMIT)
        self.assert_capture(payload, "capture_limit_unknown")

    def test_stream_and_keepalive(self):
        before = len(self.records())
        with self.connection() as conn:
            for path in ["/stream", "/second"]:
                conn.request("POST", path, '{"model":"fixture-stream"}',
                             {"Content-Type": "application/json"})
                response = conn.getresponse()
                self.assertEqual(response.status, 200)
                self.assertTrue(response.read())
        for _ in range(60):
            if len(self.records()) >= before + 2:
                break
            time.sleep(0.05)
        self.assertEqual(len(self.records()), before + 2)

    def test_cancel_stream(self):
        before = len(self.records())
        with self.connection() as conn:
            conn.request("POST", "/stream", '{"model":"fixture-cancel"}',
                         {"Content-Type": "application/json"})
            response = conn.getresponse()
            self.assertEqual(response.status, 200)
            self.assertTrue(response.read(5))
            response.close()
        row = self.last_record(before)
        self.assertEqual(row.get("requested_model_raw"), "fixture-cancel")

    def test_mismatched_authority(self):
        self.assertNotEqual(self.exchange('{"model":"bad"}', host_header="example.com"), 200)

    def test_unapproved_port(self):
        with self.connection(port=8443) as conn:
            with self.assertRaises((OSError, http.client.HTTPException)):
                conn.request("POST", "/", "{}")
                conn.getresponse()

    def test_unapproved_host(self):
        with self.connection(host="example.com") as conn:
            with self.assertRaises((OSError, http.client.HTTPException)):
                conn.request("POST", "/", "{}")
                conn.getresponse()

    def test_z_privacy(self):
        text = self.log_path.read_text()
        self.assertNotIn(SENTINEL, text)
        self.assertNotIn("SYNTHETIC_RESPONSE", text)
        self.assertNotIn("Bearer ", text)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gateway", required=True)
    parser.add_argument("--ca-dir", required=True)
    ARGS, remaining = parser.parse_known_args()
    unittest.main(argv=[__file__] + remaining, verbosity=2)
