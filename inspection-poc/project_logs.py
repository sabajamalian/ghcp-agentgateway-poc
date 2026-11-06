#!/usr/bin/env python3
"""Persist only explicitly permitted model metadata, never raw gateway output."""

import datetime as dt
import json
import os
from pathlib import Path
import re
import subprocess
import sys

STATUSES = {
    "captured", "not_present", "parse_error", "invalid_json_shape",
    "invalid_model_type", "unsupported_content_type", "capture_limit_unknown",
    "no_body", "compressed_body", "http_413_no_body_captured",
}
HOSTS = {"api.enterprise.githubcopilot.com", "api.business.githubcopilot.com"}
IDENTIFIER = re.compile(r"[A-Za-z0-9._:/-]{1,160}\Z")
UUID = re.compile(r"[a-f0-9-]{36}\Z")


def project(row, context):
    if row.get("poc_schema") != "copilot-model-v1":
        return None
    if row.get("host") not in HOSTS or not UUID.fullmatch(str(row.get("exchange_id", ""))):
        raise ValueError("Invalid metadata host or exchange identifier")
    status = row.get("model_capture_status", "expression_error")
    model = row.get("requested_model_raw")
    if status not in STATUSES:
        status, model = "expression_error", None
    if status == "captured" and (not isinstance(model, str) or not IDENTIFIER.fullmatch(model)):
        status, model = "invalid_model_identifier", None
    if status != "captured":
        model = None
    source = row.get("model_source")
    if status == "captured" and source not in {"llm.requestModel", "request.body.model"}:
        status, model, source = "expression_error", None, None
    response_model = row.get("response_model_raw")
    if not (isinstance(response_model, str) and IDENTIFIER.fullmatch(response_model)):
        response_model = None
    timestamp = row.get("time")
    if not isinstance(timestamp, str):
        raise ValueError("Missing gateway timestamp")
    dt.datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    path = str(row.get("path", "")).split("?", 1)[0]
    # Copilot APIs use fixed endpoint paths. Reject unexpected path text rather
    # than persisting arbitrary URI data, which might contain sensitive values.
    ctype = str(row.get("content_type", "")).split(";", 1)[0].strip()
    enc = str(row.get("content_encoding", ""))
    safe_path = path if re.fullmatch(r"/[A-Za-z0-9_./-]{0,200}", path) else "<redacted>"
    return {
        "poc_schema": "copilot-model-v1",
        "timestamp": timestamp,
        "gateway_version": "1.7.0",
        "exchange_id": row["exchange_id"],
        "host": row["host"],
        "method": row.get("method") if row.get("method") in {"POST", "GET", "OPTIONS"} else "OTHER",
        "path": safe_path,
        "status": int(row.get("status", 0)),
        "model_capture_status": status,
        "requested_model_raw": model,
        "model_source": source if status == "captured" else None,
        "response_model_raw": response_model,
        "content_type": ctype if re.fullmatch(r"[A-Za-z0-9!#$&^_.+/-]{0,80}", ctype) else "<redacted>",
        "content_encoding": enc if re.fullmatch(r"[A-Za-z0-9, -]{0,40}", enc) else "<redacted>",
        "body_bytes": int(row.get("body_bytes", -1) or 0),
        "run_id": str(context.get("run_id", "unassigned"))[:80],
        "client": str(context.get("client", "unassigned"))[:40],
        "client_version": str(context.get("client_version", "unassigned"))[:80],
    }


def run(command):
    os.umask(0o027)
    directory = Path("/var/log/inspection-poc")
    directory.mkdir(mode=0o750, exist_ok=True)
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        for raw in process.stdout:
            try:
                row = json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError):
                print("Suppressed non-JSON gateway diagnostic", file=sys.stderr, flush=True)
                continue
            context_path = Path("/etc/inspection-poc/run.json")
            try:
                context = json.loads(context_path.read_text()) if context_path.exists() else {}
                metadata = project(row, context)
            except (ValueError, TypeError, OSError):
                print("Rejected invalid gateway metadata", file=sys.stderr, flush=True)
                continue
            if metadata is None:
                if row.get("level") in {"warn", "error"}:
                    print("Suppressed gateway warning/error; inspect service health", file=sys.stderr, flush=True)
                continue
            today = dt.datetime.now(dt.timezone.utc).date().isoformat()
            with (directory / f"metadata-{today}.jsonl").open("a") as stream:
                # AMA JSON-file collection expects flat JSON records. Keep the
                # nullable metadata contract intact inside one string column.
                envelope = {
                    "TimeGenerated": metadata["timestamp"],
                    "Payload": json.dumps(metadata, separators=(",", ":")),
                }
                stream.write(json.dumps(envelope, separators=(",", ":")) + "\n")
                stream.flush()
    finally:
        if process.poll() is None:
            process.terminate()
        code = process.wait(timeout=20)
    return code


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("Usage: project_logs.py COMMAND [ARGUMENT ...]")
    raise SystemExit(run(sys.argv[1:]))
