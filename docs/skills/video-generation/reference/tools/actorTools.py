"""
Actor 使用的工具集中入口。

从各子模块 import * 后，在 ACTOR_TOOLS 中显式列出要绑定到 Actor Agent 的工具，
便于按需调整顺序与增减。
"""

from .physicsEditTools.physicsEditTools import *
from .generationTools.generationTools import *
from .mmUnderstandingTools.multimodalUnderstandingTools import *
from .submitPlanTools.submitPlanTools import *
from .userTools.userTools import *
from .logicSplitTools.logicSplitTools import *
from .imageGen import generate_image_tool
from .ioTools import read_tool, write_tool
from .novelPipeline import (
    novel_analyze_tool,
    novel_split_clips_tool,
    novel_convert_clips_tool,
    novel_merge_screenplay_tool,
    novel_script_to_storyboard_tool,
)

# Actor Agent 绑定的工具列表（顺序可调），actor 里直接用 tools=ACTOR_TOOLS
ACTOR_TOOLS = [
    # 基础视频信息 & 编辑/生成
    get_video_metadata,
    video_generate_tool,
    batch_video_generate_tool,
    merge_video_tool,
    # 文件读写 & 小说转剧本/分镜（NOVEL_TO_SCREENPLAY 阶段）
    read_tool,
    write_tool,
    novel_analyze_tool,
    novel_split_clips_tool,
    novel_convert_clips_tool,
    novel_merge_screenplay_tool,
    novel_script_to_storyboard_tool,
   
    generate_image_tool,
    # 多阶段 Plan 读写工具
    get_multi_stage_plan_summary_tool,
    get_current_stage_plan_tool,
    get_stage_outputs_tool,
    set_stage_outputs_tool,
    # 其他工具（按需添加）
    #getUserMessageTool,
    # analyze_segment_relevance_tool,
    # video_scene_logic_split_tool,
    video_split_tool,
    split_video_by_duration_tool,
    detect_and_resegment_shots_tool,
    video_understanding_tool,
    image_understanding_tool,
    Multi_model_understanding_tool,
]
