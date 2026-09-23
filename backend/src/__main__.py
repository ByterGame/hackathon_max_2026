import uvicorn

from .core.config import load_port


def main() -> None:
    uvicorn.run("src.main:app", host="0.0.0.0", port=load_port())


if __name__ == "__main__":
    main()
