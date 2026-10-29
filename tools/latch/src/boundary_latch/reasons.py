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
    Code.ALLOWED: "The call passed every check and may run.",
    Code.NEEDS_APPROVAL: "The call passed every check but another authorized user must approve it first.",
    Code.APPROVED: "An authorized user approved the call and it may run once.",
    Code.DENIED_BY_APPROVER: "An authorized user denied the call, so it will not run.",
    Code.UNKNOWN_TOOL: "No tool with that name is registered.",
    Code.NO_POLICY: "The policy has no entry for this tool, so Latch blocks it.",
    Code.INVALID_ARGUMENTS: "The arguments do not match what the tool accepts.",
    Code.MISSING_PERMISSION: "The user does not hold the permission this tool requires.",
    Code.RECIPIENT_NOT_ALLOWED: "That recipient is not on the allowed list for this tool.",
    Code.ACCESS_DENIED: "The app's access check said this user may not act on this record.",
    Code.ACCESS_HOOK_FAILED: "The app's access check raised an error, so Latch blocks the call.",
    Code.RUN_LIMIT: "This run has used all the tool calls the policy allows.",
    Code.RUN_EXPIRED: "This run is older than the policy allows, so start a new run.",
    Code.APPROVAL_REQUIRED: "This call needs an approval before it can run.",
    Code.APPROVAL_EXPIRED: "The approval is too old to use, so ask for a new one.",
    Code.APPROVAL_USED: "That approval was already used and cannot run the call again.",
    Code.APPROVER_NOT_ALLOWED: (
        "The approver must be another user in the same org who holds the approve permission."
    ),
    Code.IDENTITY_MISMATCH: "The user running the call is not the user who prepared it.",
    Code.OPERATION_CONFLICT: "That operation id was already used for a different request.",
    Code.ALREADY_RECORDED: "This operation already ran, so Latch returns the recorded outcome.",
    Code.UNCERTAIN: (
        "The tool may have run before it timed out or crashed, so Latch will not run it again."
    ),
    Code.EXECUTED: "The tool ran and reported success.",
    Code.FAILED: "The tool reported that the action did not happen.",
}


def explain(code: Code) -> str:
    return EXPLANATIONS[code]
