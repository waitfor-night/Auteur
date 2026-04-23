#!/usr/bin/env python3
"""
长视频编辑对外 HTTP API（FastAPI）。
供 nanobot 或其它服务通过 POST 调用：完整工作流、仅 Planner、仅 Actor。

启动方式（在项目根目录）:
    export ARK_API_KEY="your-key"
    uvicorn api:app --host 0.0.0.0 --port 8000
"""

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

# 项目根加入 path，保证可导入 planner / actor / utils.*
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

try:
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import JSONResponse
    from pydantic import BaseModel, Field
except ImportError:
    print("请安装: pip install fastapi uvicorn pydantic")
    sys.exit(1)

app = FastAPI(
    title="Long Video Edit API",
    description="长视频编辑：Planner + Actor 工作流",
    version="0.1.0",
)


# ---------- 请求/响应模型 ----------

class WorkflowRunRequest(BaseModel):
    """完整工作流入参（与 run_long_video_edit_workflow 一致）。"""
    init_user_message: str = Field(..., description="编辑指令 / 用户需求描述")
    video_path: str = Field("", description="输入视频本地路径，空表示无视频（纯生成等）")
    image_paths: list[str] = Field(default_factory=list, description="参考图路径列表")
    time_length: int | None = Field(default=15, ge=1, le=300, description="单片段最大时长(秒)，不传或为 null 时默认为 15")
    use_auto_split_pipeline: bool = Field(True, description="是否使用 auto_video_splite 分镜管道")
    allow_interactive: bool = Field(False, description="是否允许交互式用户输入（API 建议 False）")


class PlannerOnlyRequest(BaseModel):
    """仅跑 Planner 的入参。"""
    init_user_message: str = Field(..., description="编辑指令")
    video_path: str = Field("", description="输入视频路径")
    image_paths: list[str] = Field(default_factory=list)
    time_length: int | None = Field(default=15, ge=1, le=300, description="单片段最大时长(秒)，不传或为 null 时默认为 15")
    use_auto_split_pipeline: bool = True
    allow_interactive: bool = False


class ActorOnlyRequest(BaseModel):
    """仅跑 Actor 的入参（需提供已生成的 Plan）。"""
    plan: dict[str, Any] = Field(..., description="Video Execution Plan 对象（含 task_metadata / timeline）")


# ---------- 业务逻辑在子线程中执行，避免阻塞事件循环 ----------

def _run_workflow_sync(
    video_path: str,
    init_user_message: str,
    image_paths: list[str],
    time_length: int,
    use_auto_split_pipeline: bool,
    allow_interactive: bool,
) -> str:
    from utils.long_video_edit_workflow import run_long_video_edit_workflow
    return run_long_video_edit_workflow(
        video_path=video_path,
        InitUserMessage=init_user_message,
        image_paths=image_paths,
        time_length=time_length,
        use_auto_split_pipeline=use_auto_split_pipeline,
        allow_interactive=allow_interactive,
    )


def _run_planner_sync(
    init_user_message: str,
    video_path: str,
    image_paths: list[str],
    time_length: int,
    use_auto_split_pipeline: bool,
    allow_interactive: bool,
) -> dict[str, Any]:
    from planner import run_planner_with_user_input
    user_message = (
        "### Runtime Inputs\n"
        + f"- InitUserMessage: {init_user_message}\n"
        + f"- time_length: {time_length}\n"
        + f"- use_auto_split_pipeline: {use_auto_split_pipeline}\n"
        + (f"- video_path: {video_path}\n" if video_path else "- video_path: (无)\n")
        + f"- image_paths: {image_paths}\n"
    )
    run_response = run_planner_with_user_input(
        user_message,
        stream=False,
        use_print_response=False,
        use_task_specific_prompt=True,
        allow_interactive=allow_interactive,
        video_path=video_path or None,
        image_paths=image_paths,
        time_length=time_length,
        InitUserMessage=init_user_message,
        use_auto_split_pipeline=use_auto_split_pipeline,
    )
    plan = None
    try:
        from utils.execution_plan_store import get_plan
        plan = get_plan()
    except Exception:
        pass
    if plan is None and run_response and getattr(run_response, "content", None):
        from utils.long_video_edit_workflow import extract_plan_json
        raw = (run_response.content or "").strip()
        extracted = extract_plan_json(raw)
        if extracted.startswith("{"):
            try:
                plan = json.loads(extracted)
            except json.JSONDecodeError:
                pass
    if plan is None:
        raise RuntimeError("Planner 未产出有效 Video Execution Plan")
    return plan


def _run_actor_sync(plan: dict[str, Any]) -> str:
    from utils.long_video_edit_workflow import execute_plan_with_actor
    return execute_plan_with_actor(plan=plan, stream=False)


# ---------- 路由 ----------

@app.post("/workflow/run")
async def workflow_run(body: WorkflowRunRequest) -> dict[str, Any]:
    """
    运行完整长视频编辑工作流：Planner 生成 Plan → Actor 执行并合并。
    返回最终输出视频路径。
    """
    time_length = 15 if body.time_length is None else body.time_length
    try:
        output_path = await asyncio.to_thread(
            _run_workflow_sync,
            body.video_path or "",
            body.init_user_message,
            body.image_paths,
            time_length,
            body.use_auto_split_pipeline,
            body.allow_interactive,
        )
        return {"success": True, "output_path": output_path, "error": None}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"success": False, "output_path": None, "error": str(e)},
        )


@app.post("/workflow/planner")
async def workflow_planner(body: PlannerOnlyRequest) -> dict[str, Any]:
    """
    仅运行 Planner，返回 Video Execution Plan（JSON 对象）。
    """
    time_length = 15 if body.time_length is None else body.time_length
    try:
        plan = await asyncio.to_thread(
            _run_planner_sync,
            body.init_user_message,
            body.video_path or "",
            body.image_paths,
            time_length,
            body.use_auto_split_pipeline,
            body.allow_interactive,
        )
        return {"success": True, "plan": plan, "error": None}
    except RuntimeError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"success": False, "plan": None, "error": str(e)},
        )


@app.post("/workflow/actor")
async def workflow_actor(body: ActorOnlyRequest) -> dict[str, Any]:
    """
    仅运行 Actor，传入已生成的 Plan，返回执行结果文本（通常含输出路径）。
    """
    try:
        result = await asyncio.to_thread(_run_actor_sync, body.plan)
        return {"success": True, "result": result, "error": None}
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"success": False, "result": None, "error": str(e)},
        )


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


if __name__ == "__main__":
    import os
    import uvicorn
    port = int(os.environ.get("PORT", "8001"))
    uvicorn.run(app, host="0.0.0.0", port=port)
