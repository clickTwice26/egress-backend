"""Weight-based roles and privileges.

Every role carries an integer weight. Privilege checks compare *weights*, never
role names: "can this account moderate?" is answered by `weight >= 60`, not by
`role == "moderator"`. That means a new role can be added at any point on the
scale — including between two existing ones — and every existing check keeps
working without being edited.

Weights are spaced ten apart so a new role can be slotted between two existing
ones without renumbering the ones already stored against user rows.
"""

from dataclasses import dataclass

# The gap left between adjacent default roles. Kept as a named constant so the
# spacing intent survives someone adding a role later.
WEIGHT_STEP = 10


@dataclass(frozen=True)
class RoleSpec:
    slug: str
    label: str
    weight: int


# The roles seeded on first boot. Adding one here is enough — no check needs to
# learn its name. Weights, not order, decide what an account can do.
DEFAULT_ROLES: tuple[RoleSpec, ...] = (
    RoleSpec("user", "User", 10),
    RoleSpec("student", "Student", 20),
    RoleSpec("pro", "Pro", 30),
    RoleSpec("moderator", "Moderator", 60),
    RoleSpec("admin", "Admin", 90),
)

# Every new account starts here.
DEFAULT_ROLE_SLUG = "user"


# What each privilege costs, in weight. This is the only place a capability is
# tied to a number; call sites ask for the capability and the guard does the
# arithmetic. Raising or lowering a bar is a one-line change here, and a role
# added between two thresholds automatically lands on the right side of both.
CAPABILITY_WEIGHTS: dict[str, int] = {
    # Anything a signed-in account may do.
    "plan.manage": 10,
    # Paid tiers: unlimited mocks and scored writing submissions.
    "practice.unlimited": 30,
    # Community moderation: hide posts, resolve reports.
    "content.moderate": 60,
    # Change other people's roles, see the roster.
    "roles.assign": 90,
}


def weight_for(capability: str) -> int:
    """The minimum weight a capability needs.

    Unknown capabilities are a programming error, not a permission question, so
    this raises rather than defaulting to open (or to closed, which would hide
    the typo until someone reported a mysterious 403).
    """
    try:
        return CAPABILITY_WEIGHTS[capability]
    except KeyError:  # pragma: no cover - guards against typos at import time
        raise KeyError(f"Unknown capability {capability!r}") from None


def allows(weight: int, capability: str) -> bool:
    """Pure weight comparison — the whole privilege model in one line."""
    return weight >= weight_for(capability)
