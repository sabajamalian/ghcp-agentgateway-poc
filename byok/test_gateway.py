import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import http.client
import json
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import time
import unittest

from configure import configuration

GATEWAY = None
KEY = "fixture-gateway-key-0000000000000000000000"
UPSTREAM_KEY = "fixture-provider-key"
ALIAS = "foundry-team-model"


class Fixture(BaseHTTPRequestHandler):
    seen = []

    def log_message(self, *_):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        request = json.loads(body)
        self.seen.append((self.path, dict(self.headers), request))
        content_type = "application/json"
        if self.path.endswith("/responses"):
            response = json.dumps({
                "id": "resp_fixture", "object": "response", "created_at": 0,
                "model": "fixture-deployment", "status": "completed", "output": [],
                "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            }).encode()
        elif request.get("stream"):
            content_type = "text/event-stream"
            chunk = {
                "id": "chatcmpl_fixture", "object": "chat.completion.chunk", "created": 0,
                "model": "fixture-deployment", "choices": [{
                    "index": 0, "delta": {"role": "assistant", "content": "OK"},
                    "finish_reason": None,
                }],
            }
            response = ("data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n").encode()
        else:
            response = json.dumps({
                "id": "chatcmpl_fixture", "object": "chat.completion", "created": 0,
                "model": "fixture-deployment",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "OK"},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }).encode()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)


class ConfigurationTests(unittest.TestCase):
    def test_foundry_requires_a_project(self):
        with self.assertRaisesRegex(ValueError, "project"):
            configuration("fixture-resource", "model", KEY, resource_type="foundry")

    def test_resource_name_cannot_be_an_endpoint_url(self):
        with self.assertRaisesRegex(ValueError, "resource name"):
            configuration("https://untrusted.example", "model", KEY)


class GatewayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if GATEWAY is None:
            raise unittest.SkipTest("Pass --gateway to run real-binary fixtures.")
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            cls.port = reservation.getsockname()[1]
        config = configuration("fixture-resource", "fixture-deployment", KEY)
        config["gateways"]["byok-api"]["port"] = cls.port
        config["gateways"]["byok-api"]["bindAddress"] = "127.0.0.1"
        ai = config["routes"][1]["backends"][0]["ai"]
        ai["hostOverride"] = f"127.0.0.1:{cls.server.server_port}"
        ai["policies"].pop("backendTLS")
        ai["policies"]["backendAuth"]["key"]["value"] = UPSTREAM_KEY
        path = cls.root / "config.json"
        path.write_text(json.dumps(config))
        cls.log_path = cls.root / "gateway.log"
        cls.log = cls.log_path.open("w")
        cls.process = subprocess.Popen([GATEWAY, "-f", str(path)], stdout=cls.log, stderr=subprocess.STDOUT)
        for _ in range(100):
            if cls.process.poll() is not None:
                raise RuntimeError(cls.log_path.read_text())
            try:
                with socket.create_connection(("127.0.0.1", cls.port), timeout=0.1):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError("Gateway did not become ready.")

    @classmethod
    def tearDownClass(cls):
        cls.process.terminate()
        cls.process.wait(timeout=10)
        cls.log.close()
        cls.server.shutdown()
        cls.server.server_close()
        cls.temp.cleanup()

    def call(self, *, key=KEY, model=ALIAS, path="/v1/chat/completions", method="POST", stream=False):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {"Content-Type": "application/json", "api-key": "untrusted-client-key"}
        if key is not None:
            headers["Authorization"] = f"Bearer {key}"
        payload = {
            "model": model, "messages": [{"role": "user", "content": "FIXTURE_PROMPT"}],
            "tools": [{"type": "function", "function": {
                "name": "fixture_tool", "parameters": {"type": "object", "properties": {}},
            }}],
        }
        if path.endswith("/responses"):
            payload = {"model": model, "input": "FIXTURE_PROMPT"}
        if stream:
            payload["stream"] = True
        body = None if method == "GET" else json.dumps(payload)
        try:
            conn.request(method, path, body, headers)
            response = conn.getresponse()
            return response.status, response.read()
        finally:
            conn.close()

    def test_missing_and_wrong_gateway_keys_are_rejected(self):
        before = len(Fixture.seen)
        for key in (None, "wrong-gateway-key"):
            with self.subTest(key=key):
                status, _ = self.call(key=key)
                self.assertEqual(status, 401)
        self.assertEqual(len(Fixture.seen), before)

    def test_caller_cannot_change_the_pinned_upstream_deployment(self):
        status, _ = self.call(model="not-allowed")
        self.assertEqual(status, 200)
        self.assertEqual(Fixture.seen[-1][2]["model"], "fixture-deployment")

    def test_model_catalog(self):
        status, body = self.call(method="GET", path="/v1/models")
        self.assertEqual(status, 200, body)
        self.assertEqual(json.loads(body)["data"][0]["id"], ALIAS)

    def test_responses_api(self):
        status, body = self.call(path="/v1/responses")
        self.assertEqual(status, 200, body)
        self.assertEqual(json.loads(body)["object"], "response")
        self.assertEqual(Fixture.seen[-1][2]["model"], "fixture-deployment")

    def test_streaming_completions(self):
        status, body = self.call(stream=True)
        self.assertEqual(status, 200, body)
        self.assertIn(b"data:", body)
        self.assertIn(b"[DONE]", body)

    def test_maps_model_and_replaces_auth_without_persisting_content(self):
        status, body = self.call()
        self.assertEqual(status, 200, body)
        path, raw_headers, request = Fixture.seen[-1]
        headers = {key.lower(): value for key, value in raw_headers.items()}
        self.assertEqual(path, "/v1/chat/completions")
        self.assertEqual(request["model"], "fixture-deployment")
        self.assertEqual(request["tools"][0]["function"]["name"], "fixture_tool")
        self.assertNotIn("authorization", headers)
        self.assertEqual(headers["api-key"], UPSTREAM_KEY)
        for _ in range(50):
            logs = self.log_path.read_text()
            if '"upstream_requested_model"' in logs:
                break
            time.sleep(0.1)
        self.assertIn(ALIAS, logs)
        self.assertIn('"upstream_requested_model":"fixture-deployment"', logs)
        for value in (KEY, UPSTREAM_KEY, "untrusted-client-key", "FIXTURE_PROMPT"):
            self.assertNotIn(value, logs)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--gateway", required=True)
    args = parser.parse_args()
    GATEWAY = str(Path(args.gateway).resolve())
    unittest.main(argv=[__file__], verbosity=2)
