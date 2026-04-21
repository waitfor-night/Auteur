# Planner 使用的工具：从各子模块 import * 引用所有工具

from .generationTools.generationTools import *
from .mmUnderstandingTools.multimodalUnderstandingTools import *
from .physicsEditTools.physicsEditTools import *
from .logicSplitTools.logicSplitTools import *
from .skillTools.skillTools import *
from .submitPlanTools.submitPlanTools import *
from .userTools.userTools import *
from .ioTools import read_tool

# Planner Agent 绑定的工具列表（顺序可调），planner 里直接用 tools=PLANNER_TOOLS
# PLANNER_TOOLS = [
#     load_skill_tool,
#     get_video_metadata,
#     video_understanding_tool,
#     video_scene_logic_split_tool,
#     detect_and_resegment_shots_tool,
#     video_split_tool,
#     split_video_by_duration_tool,
#     analyze_segment_relevance_tool,
#     Multi_model_understanding_tool,
#     image_understanding_tool,
#     getUserMessageTool,  # 缺信息时可向用户询问，否则会报 Function getUserMessageTool not found
#     submit_video_execution_plan,
#     extract_script_entities_tool,
#     generate_image_tool,
# ]
PLANNER_TOOLS = [
    load_skill_tool,
    read_tool,
    video_understanding_tool,
    image_understanding_tool,
    submit_multi_stage_plan,
    video_scene_logic_split_tool,
    detect_and_resegment_shots_tool,
]