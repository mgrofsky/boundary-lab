from enum import StrEnum


class Code(StrEnum):
    ALLOWED = "ALLOWED"
    NEEDS_APPROVAL = "NEEDS_APPROVAL"
    APPROVED = "APPROVED"
    DENIED_BY_APPROVER = "DENIED_BY_APPROVER"
    UNKNOWN_TOOL = "UNKNOWN_TOOL"
    NO_POLICY = "NO_POLICY"
    INVALID_ARGUMENTS = "INVALID_ARGUMENTS"
    MISSING_PERMISSION = "MISSING_PERMISSION"
    RECIPIENT_NOT_ALLOWED = "RECIPIENT_NOT_ALLOWED"
    ACCESS_DENIED = "ACCESS_DENIED"
    ACCESS_HOOK_FAILED = "ACCESS_HOOK_FAILED"
    RUN_LIMIT = "RUN_LIMIT"
    RUN_EXPIRED = "RUN_EXPIRED"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    APPROVAL_EXPIRED = "APPROVAL_EXPIRED"
    APPROVAL_USED = "APPROVAL_USED"
    APPROVER_NOT_ALLOWED = "APPROVER_NOT_ALLOWED"
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
    OPERATION_CONFLICT = "OPERATION_CONFLICT"
    ALREADY_RECORDED = "ALREADY_RECORDED"
    UNCERTAIN = "UNCERTAIN"
    EXECUTED = "EXECUTED"
    FAILED = "FAILED"


EXPLANATIONS: dict[Code, str] = {
    Code.ALLOWED: "The request passed the checks so far and is ready to run.",
    Code.NEEDS_APPROVAL: (
        "The request passed the checks so far but needs another user's approval before it can run."
    ),
    Code.APPROVED: (
        "A user with approval permission approved this request. "
        "It can run once if the checks still pass."
    ),
    Code.DENIED_BY_APPROVER: (
        "A user with approval permission denied the request, so it will not run."
    ),
    Code.UNKNOWN_TOOL: "No tool with that name has been added to Latch.",
    Code.NO_POLICY: "The policy has no rules for this tool, so Latch blocks it.",
    Code.INVALID_ARGUMENTS: "The request's inputs do not match what this tool accepts.",
    Code.MISSING_PERMISSION: "The user does not have the permission this tool requires.",
    Code.RECIPIENT_NOT_ALLOWED: "That recipient is not on the allowed list for this tool.",
    Code.ACCESS_DENIED: "The app's access check says this user cannot use this record.",
    Code.ACCESS_HOOK_FAILED: "The app's access check failed with an error, so Latch blocks the call.",
    Code.RUN_LIMIT: "This run has reached its tool request limit.",
    Code.RUN_EXPIRED: "This run has reached its time limit. Start a new run.",
    Code.APPROVAL_REQUIRED: "This request needs approval before it can run.",
    Code.APPROVAL_EXPIRED: "The approval has expired. Prepare a new request and get approval again.",
    Code.APPROVAL_USED: (
        "An approval decision is already recorded for this operation, so it cannot be replaced."
    ),
    Code.APPROVER_NOT_ALLOWED: (
        "The approver must be another user in the same organization "
        "with this tool's approval permission."
    ),
    Code.IDENTITY_MISMATCH: "The user, organization, or run does not match the prepared request.",
    Code.OPERATION_CONFLICT: (
        "The request, tool, policy, or saved record does not match this operation ID."
    ),
    Code.ALREADY_RECORDED: (
        "Latch already recorded an attempt for this operation and will not start it again."
    ),
    Code.UNCERTAIN: (
        "Latch could not confirm the result after a timeout or error, "
        "so it will not run this operation again."
    ),
    Code.EXECUTED: "The tool ran and reported success.",
    Code.FAILED: "The tool reported that the action did not happen.",
}


def explain(code: Code) -> str:
    return EXPLANATIONS[code]
