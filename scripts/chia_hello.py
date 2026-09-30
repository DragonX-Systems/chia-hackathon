"""CHIA hello: in-process call, then local Ray (no cluster YAML)."""

from chia.base.ChiaFunction import ChiaFunction, get
import ray


@ChiaFunction()
def hello() -> str:
    return "Hello World from CHIA"


def main() -> None:
    print("local:", hello())
    ray.init(ignore_reinit_error=True, include_dashboard=False)
    print("remote:", get(hello.chia_remote()))
    ray.shutdown()


if __name__ == "__main__":
    main()
