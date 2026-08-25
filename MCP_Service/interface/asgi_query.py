from urllib.parse import parse_qs


def get_query_param(scope, name: str) -> str | None:
    params = parse_qs(scope.get("query_string", b"").decode())
    values = params.get(name)
    return values[0] if values else None
