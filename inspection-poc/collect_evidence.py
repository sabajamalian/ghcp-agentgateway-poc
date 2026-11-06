#!/usr/bin/env python3
"""Snapshot only the pilot's allowlisted model metadata from Log Analytics."""

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import uuid

from management_bootstrap import azure


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--subscription", default=os.environ.get("SUBSCRIPTION"))
    args = parser.parse_args()
    if not args.subscription:
        parser.error("Set SUBSCRIPTION or pass --subscription.")
    os.umask(0o077)
    root = Path(__file__).resolve().parent
    state = root.parent / ".secrets" / "inspection-poc"
    outputs = json.loads((state / "outputs.json").read_text())
    query = """
CopilotModel_CL
| where TimeGenerated > ago(1d)
| extend ExchangeId = tostring(Metadata.exchange_id)
| summarize arg_max(TimeGenerated, Metadata) by ExchangeId
| project TimeGenerated, ExchangeId,
          RunId=tostring(Metadata.run_id), Client=tostring(Metadata.client),
          ClientVersion=tostring(Metadata.client_version),
          RequestedModel=tostring(Metadata.requested_model_raw),
          ResponseModel=tostring(Metadata.response_model_raw),
          CaptureStatus=tostring(Metadata.model_capture_status),
          ModelSource=tostring(Metadata.model_source),
          ContentEncoding=tostring(Metadata.content_encoding),
          Host=tostring(Metadata.host), Method=tostring(Metadata.method),
          Path=tostring(Metadata.path),
          Status=toint(Metadata.status)
| order by TimeGenerated asc
"""
    records = json.loads(azure([
        "monitor", "log-analytics", "query",
        "--subscription", args.subscription,
        "-w", outputs["workspaceId"]["value"], "--analytics-query", query, "-o", "json",
    ]))
    for row in records:
        row.pop("TableName", None)
    report = {
        "observed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "window": "preceding 24 hours",
        "source": "agw-inspection-logs/CopilotModel_CL",
        "records": records,
        "unique_decoded_exchanges_observed": len(records),
        "synthetic_model_captures": sum(
            row["Client"] == "curl-fixture" and row["CaptureStatus"] == "captured"
            for row in records
        ),
        "real_cli_exchanges_observed": sum(row["Client"] == "cli" for row in records),
        "desktop_exchanges_observed": sum(row["Client"] == "desktop" for row in records),
        "real_copilot_model_capture_verdict": (
            "requires client-matrix review"
            if any(row["Client"] in {"cli", "desktop"} and row["CaptureStatus"] == "captured"
                   for row in records)
            else "not demonstrated"
        ),
    }
    evidence = state / "evidence"
    evidence.mkdir(mode=0o700, exist_ok=True)
    target = evidence / f"azure-{uuid.uuid4()}.json"
    target.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({key: value for key, value in report.items() if key != "records"}, indent=2))
    print(f"Protected metadata snapshot: {target.relative_to(root.parent)}")
