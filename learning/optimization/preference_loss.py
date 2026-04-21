from typing import Union

from textgrad.variable import Variable
from textgrad.autograd import LLMCall, Module
from textgrad.engine import EngineLM
from textgrad.config import validate_engine_or_get_default


PREFERENCE_LOSS_INSTRUCTION_TEMPLATE = """# Role
你是一名「用户记忆」优化专家。你需要基于**当前的 memory.md 文档**和**一次完整的用户交互轨迹**进行更新。

# Context
- 交互轮数：共 {num_rounds} 轮（包含 Plan/Act/Feedback 循环）。
- 任务目标：从轨迹中提炼**用户偏好**、**任务/成片习惯**、**显式反馈与纠偏**，写入 memory 的三个一级标题下；不要改写 Planner 编排规则（那是 meta-skill 的职责）。

# Instructions
请仔细阅读下方轨迹，执行：
1. **诊断**：哪些轮次体现了用户满意/不满意或具体纠正。
2. **提炼**：可跨任务复用的偏好（如画幅、节奏、是否先看素材、禁忌等）与纠偏要点。
3. **优化**：更新 memory.md 正文，使三个章节反映上述发现。

# Constraints
- **结构维持**：必须保留且仅保留这三个一级标题行（逐字一致）：
  - # 用户偏好
  - # 任务与成片习惯
  - # 交互纠偏摘要
- **最小化修改**：在保留结构前提下增量更新各节内容；可用「- 」列表或短段落。
- **禁止**：大段粘贴 plan JSON；与 meta-skill 重复的通用编排说明应放在 meta-skill 而非本文件。

## 轨迹数据（完整交互）
{trajectory_text}
"""


class PreferenceLoss(Module):
    """
    输入 memory Variable 和 preference_view 文本（Prism-Trace 偏好视图），
    backward 时将 LLM 建议写入 memory.gradients，供 TGD.step()。
    """

    def __init__(
        self,
        trajectory_text: str,
        num_rounds: int,
        engine: Union[EngineLM, str, None] = None,
    ):
        super().__init__()
        self.trajectory_text = trajectory_text
        self.num_rounds = num_rounds
        self.engine = validate_engine_or_get_default(engine)
        instruction = PREFERENCE_LOSS_INSTRUCTION_TEMPLATE.format(
            num_rounds=num_rounds,
            trajectory_text=trajectory_text,
        )
        self.eval_prompt = Variable(
            instruction,
            requires_grad=False,
            role_description="evaluation instruction for user memory optimization",
        )
        self.llm_call = LLMCall(self.engine, self.eval_prompt)

    def forward(self, user_memory: Variable) -> Variable:
        return self.llm_call(user_memory)
