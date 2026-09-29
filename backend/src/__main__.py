import uvicorn

from .core.config import load_port
from .core.logging import configure_logging


def main() -> None:
    configure_logging()
    uvicorn.run(
        "src.main:app",
        host="0.0.0.0",
        port=load_port(),
        log_config=None,
        access_log=False,
    )


if __name__ == "__main__":
    main()
