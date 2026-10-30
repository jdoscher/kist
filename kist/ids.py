from ulid import ULID


def new_ulid() -> str:
    return str(ULID())
