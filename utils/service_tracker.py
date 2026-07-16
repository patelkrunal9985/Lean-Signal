_api_contacts: dict[str, int] = {}


def report_api_contact(service: str):
    _api_contacts[service] = _api_contacts.get(service, 0) + 1


def get_api_contacts() -> dict:
    return dict(_api_contacts)
