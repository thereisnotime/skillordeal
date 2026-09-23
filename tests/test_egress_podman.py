import subprocess

import pytest

from skillordeal.config import NetworkConfig
from skillordeal.egress import Egress

IMAGE = "localhost/skillordeal-runner:dev"

PROBE = (
    "const http=require('http');"
    "for (const h of ['api.anthropic.com:443','example.com:443','evil.test:80']) {"
    " const r=http.request({host:process.env.PX,port:3128,method:'CONNECT',path:h});"
    " r.on('connect',res=>{console.log(h,res.statusCode);res.socket.destroy()});"
    " r.on('error',e=>console.log(h,'err',e.code)); r.end() }"
    "fetch('https://example.com').then(r=>console.log('direct',r.status))"
    ".catch(e=>console.log('direct blocked',e.cause&&e.cause.code))"
)


@pytest.mark.podman
def test_allowlist_blocks_and_logs():
    cfg = NetworkConfig(internal_network="skillordeal-test-internal")
    with Egress("so-test-egress", IMAGE, cfg) as eg:
        args = eg.podman_args
        out = subprocess.run(
            ["podman", "run", "--rm", *args, "-e", f"PX={eg.name}", IMAGE, "node", "-e", PROBE],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        ).stdout
    assert "api.anthropic.com:443 200" in out
    assert "example.com:443 403" in out
    assert "direct blocked" in out
    s = eg.summary()
    assert s["allowed"] == {"api.anthropic.com:443": 1}
    assert s["denied"] == {"example.com:443": 1, "evil.test:80": 1}
