"""
Rules for new passwords set from the command line (P0-7).

bcrypt only uses the first 72 bytes of a password, and bcrypt 5 refuses
anything longer, so the limit is checked here with a clear message instead of
failing with an error deep inside hashing. Bytes, not characters: accented or
non-Latin letters take 2 to 4 bytes each.
"""

MIN_LENGTH = 12
MAX_BYTES = 72


class PasswordRejected(ValueError):
    """The new password doesn't meet the rules. The message never contains it."""


def check_new_password(password: str, repeated: str, username: str) -> None:
    if password != repeated:
        raise PasswordRejected("The two passwords don't match.")
    if len(password) < MIN_LENGTH:
        raise PasswordRejected(f"The password must be at least {MIN_LENGTH} characters.")
    if len(password.encode("utf-8")) > MAX_BYTES:
        raise PasswordRejected(f"The password is too long: bcrypt uses at most {MAX_BYTES} bytes "
                               "(about 72 letters, fewer with accented or non-Latin characters).")
    if password.strip() != password:
        raise PasswordRejected("The password can't start or end with a space.")
    if password.lower() == username.lower():
        raise PasswordRejected("The password can't be the same as the username.")
