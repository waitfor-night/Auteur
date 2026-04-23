# 存储 Planner 提交的 Video Execution Plan，供 Runner/Actor 在规划结束后读取。

LAST_VIDEO_EXECUTION_PLAN = None


def set_plan(plan: dict) -> None:
    global LAST_VIDEO_EXECUTION_PLAN
    LAST_VIDEO_EXECUTION_PLAN = plan


def get_plan():
    return LAST_VIDEO_EXECUTION_PLAN


def clear_plan() -> None:
    global LAST_VIDEO_EXECUTION_PLAN
    LAST_VIDEO_EXECUTION_PLAN = None
