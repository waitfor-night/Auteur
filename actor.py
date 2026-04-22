import asyncio
from prompts import ACTOR_AGENT_PROMPT
from agno.agent import Agent
from agno.tools import tool
from agno.models.openai import OpenAIResponses
from openai import OpenAI, PermissionDeniedError
from volcenginesdkarkruntime import Ark
import os
from agno.utils import pprint
# 与 planner 一致：从 tools.actorTools 获取工具列表
from tools.actorTools import ACTOR_TOOLS

actor_agent = Agent(
    name="multi-shot-multi-object-long-video-edit-actor",
    description=ACTOR_AGENT_PROMPT,
    tools=ACTOR_TOOLS,
    model=OpenAIResponses(
        id=os.environ.get("ACTOR_MODEL", "doubao-seed-2-0-pro-260215"),
        base_url="https://ark.cn-beijing.volces.com/api/v3",
        api_key=os.environ.get("ARK_API_KEY")
    ),
)

# 示例计划：与 Planner 输出一致（split → analyze_segment_relevance → fill_reference_images），使用 reference_images
Test_Plan = """
[
          {
            "segment_id": 1,
            "start_time": "00:00:00.000",
            "end_time": "00:00:03.533",
            "segment_name": "rawdata/100/1月12日(6)-28-Scene-001.mp4",
            "frame_num": [
              0,
              106
            ],
            "file_id": "file-20260209224637-pg6d8",
            "event": "男生低头、抬头，口中说话",
            "main_subject": "穿白衬衫蓝领带的男生",
            "orientation": "坐着，身体靠墙",
            "editing_required": true,
            "editing_prompt": "将视频里的白衬衫蓝领带男生替换成参考图片（图（图中人物：银发盘成发髻、单眼眨眼的老年女性，身着浅卡其色开衫内搭灰T恤、深灰卷边长裤与棕色鞋子，直立于花园中）（图（图中人物：银发盘成发髻、单眼眨眼的老年女性，身着浅卡其色开衫内搭灰T恤、深灰卷边长裤与棕色鞋子，直立于花园中）中人物：银发盘成发髻、单眼眨眼的老年女性，身着浅卡其色开衫内搭灰T恤、深灰卷边长裤与棕色鞋子，直立于花园中）中的人物：银发盘成发髻、单眼眨眼的老年女性，身着浅卡其色开衫内搭灰T恤、深灰卷边长裤与棕色鞋子，直立于花园中）中的男生，保持背景、动作姿态和原视频场景不变",
            "reference_images": [
              "rawdata/100/f581363980a547fe0514faeefc8c1a6f.jpg"
            ]
          },
          {
            "segment_id": 2,
            "start_time": "00:00:03.533",
            "end_time": "00:00:07.533",
            "segment_name": "rawdata/100/1月12日(6)-28-Scene-002.mp4",
            "frame_num": [
              106,
              226
            ],
            "file_id": "file-20260209224637-4f4rr",
            "event": "女生双手捧着容器坐在台阶上，身体微微前倾低头",
            "main_subject": "穿深色制服的女生",
            "orientation": "正面（坐着朝向镜头）",
            "editing_required": false,
            "editing_prompt": "",
            "reference_images": []
          },
          {
            "segment_id": 3,
            "start_time": "00:00:07.533",
            "end_time": "00:00:09.467",
            "segment_name": "rawdata/100/1月12日(6)-28-Scene-003.mp4",
            "frame_num": [
              226,
              284
            ],
            "file_id": "file-20260209224638-qwhbx",
            "event": "男生坐着并转头",
            "main_subject": "穿着白衬衫蓝领带的男生",
            "orientation": "正面偏向一侧",
            "editing_required": true,
            "editing_prompt": "将视频里的男生修改成参考图片（图（图中人物：银发盘成发髻、单眼眨眼的老年女性，身着浅卡其色开衫内搭灰T恤、深灰卷边长裤与棕色鞋子，直立于花园中）（图（图中人物：银发盘成发髻、单眼眨眼的老年女性，身着浅卡其色开衫内搭灰T恤、深灰卷边长裤与棕色鞋子，直立于花园中）中人物：银发盘成发髻、单眼眨眼的老年女性，身着浅卡其色开衫内搭灰T恤、深灰卷边长裤与棕色鞋子，直立于花园中）中的人物：银发盘成发髻、单眼眨眼的老年女性，身着浅卡其色开衫内搭灰T恤、深灰卷边长裤与棕色鞋子，直立于花园中）中的男生，背景等其他内容保持不变",
            "reference_images": [
              "rawdata/100/f581363980a547fe0514faeefc8c1a6f.jpg"
            ]
          },
          {
            "segment_id": 4,
            "start_time": "00:00:09.467",
            "end_time": "00:00:10.700",
            "segment_name": "rawdata/100/1月12日(6)-28-Scene-004.mp4",
            "frame_num": [
              284,
              321
            ],
            "file_id": "file-20260209224638-zh9q7",
            "event": "男生坐在绿色棚子的座位上，女生从右侧走向男生",
            "main_subject": "视频中的男生",
            "orientation": "坐姿",
            "editing_required": true,
            "editing_prompt": "将视频里的男生修改成参考图片（图（图中人物：银发盘成发髻、单眼眨眼的老年女性，身着浅卡其色开衫内搭灰T恤、深灰卷边长裤与棕色鞋子，直立于花园中）（图（图中人物：银发盘成发髻、单眼眨眼的老年女性，身着浅卡其色开衫内搭灰T恤、深灰卷边长裤与棕色鞋子，直立于花园中）中人物：银发盘成发髻、单眼眨眼的老年女性，身着浅卡其色开衫内搭灰T恤、深灰卷边长裤与棕色鞋子，直立于花园中）中的人物：银发盘成发髻、单眼眨眼的老年女性，身着浅卡其色开衫内搭灰T恤、深灰卷边长裤与棕色鞋子，直立于花园中）中的，背景、女生等其他内容保持不变",
            "reference_images": [
              "rawdata/100/f581363980a547fe0514faeefc8c1a6f.jpg"
            ]
          }
        ]
"""
# 说明：Plan 格式与 Planner 输出一致（video_split → analyze_segment_relevance → fill_reference_images）。
# 支持 reference_images 或 reference_image；editing_prompt 可为已增强的「参考图N（图中人物：xxx）」格式。

async def main():
    response = await actor_agent.arun(Test_Plan)
    pprint(response.content)


if __name__ == "__main__":
    asyncio.run(actor_agent.aprint_response(Test_Plan))