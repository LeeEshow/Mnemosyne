class ConfigurationError(Exception):
    pass


class DomainNotRegisteredError(Exception):
    def __init__(self, registered_domains: tuple[str, ...]) -> None:
        self.registered_domains = registered_domains
        super().__init__(_build_message(registered_domains))


def _build_message(registered_domains: tuple[str, ...]) -> str:
    if not registered_domains:
        return "此 domain 尚未註冊，且目前沒有任何已註冊的 domain。"
    joined = "、".join(registered_domains)
    return f"此 domain 尚未註冊。已註冊的 domain：{joined}"
