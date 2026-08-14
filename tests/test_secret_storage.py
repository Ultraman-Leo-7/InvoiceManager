import os

from app import protect_secret, unprotect_secret


def test_secret_storage_round_trip_does_not_store_plaintext():
    secret = "1234567890abcdef"

    protected = protect_secret(secret)

    assert protected
    assert secret not in protected
    assert unprotect_secret(protected) == secret

    if os.name == "nt":
        assert protected.startswith("dpapi:")
    else:
        assert protected.startswith("plain:")
