import os

NO_HOOKS_ENV = "CLAUDE_MEM_LITE_NO_HOOKS"


def hooks_disabled() -> bool:
    return os.environ.get(NO_HOOKS_ENV) == "1"
