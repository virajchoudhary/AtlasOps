"""Run the bug lab: ``python -m demo.bug_lab``.

Control room: http://127.0.0.1:8790/   Store: http://127.0.0.1:8790/shop/
"""

from __future__ import annotations

import argparse
import ipaddress
import os
import tempfile
from pathlib import Path

import uvicorn

from demo.bug_lab.engine import LabOptions
from demo.bug_lab.llm import DEFAULT_MODEL, OLLAMA_BASE_URL
from demo.bug_lab.server import LabConfig, create_app


def _loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m demo.bug_lab", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8790)
    parser.add_argument("--allow-remote", action="store_true",
                        help="allow binding to a non-loopback address (the lab has no login)")
    parser.add_argument("--workdir", type=Path, default=None,
                        help="new or empty directory for the editable copy (default: new temporary folder)")
    parser.add_argument("--allowed-host", action="append", default=[],
                        help="additional exact Host name for an explicitly allowed remote binding")
    parser.add_argument("--agents", choices=["auto", "rules", "llm"], default="auto",
                        help="auto uses a model if one answers, otherwise the rule engine")
    parser.add_argument("--llm-base-url", default=os.environ.get("BUG_LAB_LLM_BASE_URL"),
                        help=f"OpenAI-compatible endpoint (default {OLLAMA_BASE_URL})")
    parser.add_argument("--llm-model", default=os.environ.get("BUG_LAB_LLM_MODEL"),
                        help=f"model name (default {DEFAULT_MODEL})")
    parser.add_argument("--llm-timeout", type=float,
                        default=float(os.environ.get("BUG_LAB_LLM_TIMEOUT", "120")),
                        help="seconds to wait for one model answer; raise it for CPU-only models")
    parser.add_argument("--step-delay", type=float, default=0.6,
                        help="seconds between agent steps, so each step can be followed on screen")
    args = parser.parse_args(argv)
    if not _loopback(args.host) and not args.allow_remote:
        parser.error("refusing to bind to a non-loopback address without --allow-remote")
    if any("*" in host or "/" in host for host in args.allowed_host):
        parser.error("--allowed-host requires an exact host name, not a wildcard or URL")

    workdir = args.workdir or Path(tempfile.mkdtemp(prefix="atlasops-bug-lab-"))
    config = LabConfig(workdir=workdir, agents=args.agents, llm_base_url=args.llm_base_url,
                       llm_model=args.llm_model, llm_api_key=os.environ.get("BUG_LAB_LLM_API_KEY", ""),
                       llm_timeout=args.llm_timeout,
                       allowed_hosts=tuple(dict.fromkeys(
                           ("127.0.0.1", "localhost", "[::1]", args.host, *args.allowed_host)
                       )),
                       options=LabOptions(step_delay=args.step_delay))
    app = create_app(config)
    print(f"AtlasOps bug lab\n  Control room: http://{args.host}:{args.port}/\n"
          f"  Store:        http://{args.host}:{args.port}/shop/\n  Working copy: {workdir}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
