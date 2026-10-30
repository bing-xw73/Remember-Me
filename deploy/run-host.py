# SPDX-License-Identifier: CPAL-1.0
"""Load the Header-only host secret from the sole persistent nginx config."""

import os
from pathlib import Path
import re
import sys


def main():
    config = (Path(os.environ["CREDENTIALS_DIRECTORY"]) / "nginx-mcp.conf").read_text()
    matches = re.findall(r'proxy_set_header Authorization "Bearer ([A-Za-z0-9_-]{43,})";', config)
    if len(matches) != 1:
        raise SystemExit("Invalid nginx credential configuration")
    os.environ.pop("REMEMBER_ME_AUTH_TOKEN_FILE", None)
    os.environ["REMEMBER_ME_AUTH_TOKEN"] = matches[0]
    os.execv(sys.executable, [sys.executable, "-m", "remember_me", "serve"])


if __name__ == "__main__":
    main()
