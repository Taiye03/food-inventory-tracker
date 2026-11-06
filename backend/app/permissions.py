"""
Who may do what (P0-8). The ONE place roles are turned into permissions.

Every endpoint declares the permission it needs:

    @router.post("")
    def save_product(..., _: User = Depends(require_permission(Permission.MANAGE_STOCK))):

and tests/test_permissions.py checks that every route does, and that each
role gets exactly the access written down there. To change what a role can
do, change ROLE_PERMISSIONS below (and that test), never an endpoint.

Roles are the values stored in users.role: "Admin" (the owner) and "User"
(an employee). Matching is exact. A role that isn't listed here (a typo, a
different capitalisation, an empty value) has NO permissions: it can still
log in, but every endpoint refuses it with 403. Failing closed is safer than
guessing.

Checks always use the role stored in the database for the logged-in user
(get_current_user reloads it on every request), never the role written into
the login token, so a role change applies from the next request.
"""
from enum import Enum
from functools import cache

from fastapi import Depends, HTTPException, status

from app.auth import get_current_user
from app.models import User


class Permission(str, Enum):
    VIEW_OWN_ACCOUNT = "account.view"      # GET /auth/me
    VIEW_STOCK = "stock.view"              # list batches, one batch, expiry and low-stock alerts
    EXPORT_STOCK = "stock.export"          # download the stock list as CSV
    MANAGE_STOCK = "stock.manage"          # add, edit and delete batches
    VIEW_ORDERS = "orders.view"            # list sales (all of them, with balances)
    RECORD_SALE = "orders.create"          # record a new sale
    MANAGE_ORDERS = "orders.manage"        # edit and delete sales
    VIEW_REPORTS = "reports.view"          # sales and debts by month and by customer
    MANAGE_USERS = "users.manage"          # list and create user accounts


ADMIN = "Admin"
USER = "User"

ROLE_PERMISSIONS: dict[str, frozenset[Permission]] = {
    ADMIN: frozenset(Permission),          # the owner can do everything
    USER: frozenset({
        Permission.VIEW_OWN_ACCOUNT,
        Permission.VIEW_STOCK,
        Permission.EXPORT_STOCK,
        Permission.VIEW_ORDERS,
        Permission.RECORD_SALE,
    }),
}


def has_permission(role: str | None, permission: Permission) -> bool:
    return permission in ROLE_PERMISSIONS.get(role, frozenset()) if isinstance(role, str) else False


def _refusal(permission: Permission) -> str:
    """The 403 message. "Admins only" (unchanged) for what only admins may do."""
    if all(permission not in perms for role, perms in ROLE_PERMISSIONS.items() if role != ADMIN):
        return "Admins only"
    return "You don't have permission to do this."


@cache
def require_permission(permission: Permission):
    """A dependency: the logged-in user (401 if not logged in), who must hold
    `permission` (403 otherwise). Returns the User, like get_current_user."""
    def check(user: User = Depends(get_current_user)) -> User:
        if not has_permission(user.role, permission):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_refusal(permission))
        return user

    check.required_permission = permission          # lets tests (and docs) see what each route needs
    check.__name__ = f"require_{permission.name.lower()}"
    return check
