"""`flowsage-backend` console script: runs the API with uvicorn."""

from __future__ import annotations

import uvicorn


def main() -> None:
    uvicorn.run("flowsage_backend.main:create_app", factory=True, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
