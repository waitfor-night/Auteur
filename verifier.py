#from prompts import VERIFIER_AGENT_PROMPT
from agno.agent import Agent
from agno.tools import tool
from agno.models.openai import OpenAIResponses
from openai import OpenAI, PermissionDeniedError
from volcenginesdkarkruntime import Ark
import os
import asyncio
from agno.utils import pprint
from tools import (
    verify_video_tool,
    get_video_metadata,
    get_video_frame_tool,
    frame_similarity_tool,
)

VERIFIER_AGENT_PROMPT = """
# Role: Video Verifier
You are a video verifier. You will be given a video and a reference image. You will need to verify if the video is edited correctly.

# Task
You will be given a video and a reference image. You will need to verify if the video is edited correctly.
# Tools
- **get_video_metadata**: Get the metadata of the video.
- **get_video_frame_tool**: Get the frame of the video.
- **frame_similarity_tool**: Calculate the similarity between the frame and the reference image.
- **verify_video_tool**: Verify the video is edited correctly.
# Input
- video: The video to be verified.
- reference_image: The reference image to be used for verification.
# Output
- The verification result.
"""
verifier_agent = Agent(
    name="multi-shot-multi-object-long-video-edit-verifier",
    description=VERIFIER_AGENT_PROMPT,
    tools=[
        get_video_metadata,
        get_video_frame_tool,
        frame_similarity_tool,
        verify_video_tool,
    ],
    model=OpenAIResponses(
        id="doubao-seed-1-8-251228",
        base_url="https://ark.cn-beijing.volces.com/api/v3",
        api_key=os.environ.get("ARK_API_KEY"),
    ),
)

TEST_USER_INPUT = """
### Runtime Inputs
- video_path: rawdata/1/1月12日(14)-1.mp4
- time_length: 20
- edit_prompt: 只讲视频里的女生修改成参考图片中的角色，其他人物动作，人物形象和环境背景保持不变。
- image_paths: rawdata/1/BHGIDJIJEFJAA-Q4TlGAkehk.png
"""

TEST_PLANNER_OUTPUT = """
[                                                                                                       
   {                                                                                                     
     "segment_id": 1,                                                                                    
     "start_time": "00:00",                                                                              
     "end_time": "00:06",                                                                                
     "event":"在落地窗边的室内场景里，戴眼镜的中年男士坐在桌前低头说话，字幕显示其话语“爸爸是来向你承认错误的”；对面穿黄色西装外套的中年女士看向一侧，随后字幕依次显示她的回应“那我可承受不起”“您能有什么错啊”，过程中女士头部有转动。",
     "main_subject": "戴眼镜的中年男士、穿黄色西装外套的中年女士",
     "orientation": "男士位于画面左侧，低头面向下方；女士位于画面右侧，头部转向一侧",
     "editing_required": true,
     "editing_prompt":"将视频中的男人替换为参考图1中的女人（整个形象，包括衣着），将视频中的女人替换为参考图2中的男人（整个形 象，包括衣着），视频中的其他部分保持不变",
     "segment_name": "rawdata/90/8-1_segment_1.mp4",
     "reference_images": ["rawdata/90/参考图1.png", "rawdata/90/参考图2.png"]
   },
   {
     "segment_id": 2,
     "start_time": "00:06",
     "end_time": "00:09",
     "event":"在落地窗边的室内场景里，戴眼镜的中年男士坐在桌前低头说话，字幕显示其话语“爸爸是来向你承认错误的”；对面 穿黄色西装外套的中年女士看向一侧，随后字幕依次显示她的回应“那我可承受不起”“您能有什么错啊”，过程中女士  头部有转动。",
     "main_subject": "戴眼镜的中年男士、穿黄色西装外套的中年女士",
     "orientation": "男士位于画面左侧，低头面向下方；女士位于画面右侧，头部转向一侧",
     "editing_required": true,
     "editing_prompt":"将视频中的男人替换为参考图1中的女人（完整形象包含衣着），将视频中的女人替换为参考图2中的男人（完整形象 包含衣着），视频中的场景、物品、背景等其他部分保持不变",
     "segment_name": "rawdata/90/8-1_segment_2.mp4",
     "reference_images": ["rawdata/90/参考图1.png", "rawdata/90/参考图2.png"]
   }
 ]
"""

TEST_ACTOR_OUTPUT = """{
"actions": [
  {
    "original_video": "rawdata/100/1月12日(6)-28-Scene-001.mp4",
    "original_images": ["/root/work/multi-shot-multi-object-long-video-edit/rawdata/100/f581363980a547fe0514faeefc8c1a6f.jpg"],
    "edited_video": "rawdata/100/1月12日(6)-28-Scene-001_edited.mp4",
    "edited_video_keyframes": "rawdata/100/1月12日(6)-28-Scene-001_edited_keyframe.jpg",
    "editing_prompt": "将视频里的男生修改成参考图片中的人物(包括服装)，背景等其他内容保持不变"
  },
  {
    "original_video": "rawdata/100/1月12日(6)-28-Scene-003.mp4",
    "original_images": ["/root/work/multi-shot-multi-object-long-video-edit/rawdata/100/f581363980a547fe0514faeefc8c1a6f.jpg"],
    "edited_video": "rawdata/100/1月12日(6)-28-Scene-003_edited.mp4",
    "edited_video_keyframes": "rawdata/100/1月12日(6)-28-Scene-003_edited_keyframe.jpg",
    "editing_prompt": "将视频里的男生修改成参考图片中的人物(包括服装)，背景等其他内容保持不变"
  },
  {
    "original_video": "rawdata/100/1月12日(6)-28-Scene-004.mp4",
    "original_images": ["/root/work/multi-shot-multi-object-long-video-edit/rawdata/100/f581363980a547fe0514faeefc8c1a6f.jpg"],
    "edited_video": "rawdata/100/1月12日(6)-28-Scene-004_edited.mp4",
    "edited_video_keyframes": "rawdata/100/1月12日(6)-28-Scene-004_edited_keyframe.jpg",
    "editing_prompt": "将视频里的男生修改成参考图片中的人物(包括服装)，背景等其他内容保持不变"
  }
],
"result_video": "/root/work/multi-shot-multi-object-long-video-edit/rawdata/100/final_merged_long_video.mp4"
}"""


async def main():
    response = await verifier_agent.arun(TEST_VERIFIER_INPUT)
    pprint(response.content)


if __name__ == "__main__":
    asyncio.run(main())
