"""
The one set of rules for new passwords (P0-7), used everywhere a password is
set: scripts/create_admin.py, scripts/reset_password.py and the API's
UserCreate form. Logging in is NOT checked against these rules, so existing
accounts keep working whatever their password.

bcrypt only uses the first 72 bytes of a password, and bcrypt 5 refuses
anything longer, so the limit is checked here with a clear message instead of
failing with an error deep inside hashing. Bytes, not characters: accented or
non-Latin letters take 2 to 4 bytes each.

No message here ever contains the password.
"""

MIN_LENGTH = 12
MAX_BYTES = 72


class PasswordRejected(ValueError):
    """The new password doesn't meet the rules. The message never contains it."""


def too_long_for_bcrypt(password: str) -> bool:
    return len(password.encode("utf-8")) > MAX_BYTES


def check_password_rules(password: str, username: str) -> None:
    """Rules for a new password. Raises PasswordRejected (a ValueError)."""
    if len(password) < MIN_LENGTH:
        raise PasswordRejected(f"The password must be at least {MIN_LENGTH} characters.")
    if too_long_for_bcrypt(password):
        raise PasswordRejected(f"The password is too long: bcrypt uses at most {MAX_BYTES} bytes "
                               "(about 72 letters, fewer with accented or non-Latin characters).")
    if password.strip() != password:
        raise PasswordRejected("The password can't start or end with a space.")
    if password.lower() == username.lower():
        raise PasswordRejected("The password can't be the same as the username.")


def check_new_password(password: str, repeated: str, username: str) -> None:
    """For the command-line tools, which ask for the password twice."""
    if password != repeated:
        raise PasswordRejected("The two passwords don't match.")
    check_password_rules(password, username)
