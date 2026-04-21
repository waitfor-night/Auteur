# ExecutionLoss：基于 execution_view（Prism-Trace 执行视图），用 LLM 生成对 meta-skill 的修改建议作为文本梯度。

from typing import Union

from textgrad.variable import Variable
from textgrad.autograd import LLMCall, Module
from textgrad.engine import EngineLM
from textgrad.config import validate_engine_or_get_default


EXECUTION_LOSS_INSTRUCTION_TEMPLATE = """# Role
你是一名 Meta-Skill 优化专家。你需要基于**当前的 Meta-Skill 文档**和**一次完整的用户交互轨迹**来进行自我迭代。

# Context
- 交互轮数：共 {num_rounds} 轮（包含 Plan/Act/Feedback 循环）。
- 任务目标：通过分析轨迹，提炼用户的隐性偏好（如节奏、语气、格式）和显性纠正，将经验内化到 Meta-Skill 中。

# Instructions
请仔细阅读下方的交互轨迹，执行以下步骤：
1. **诊断 (Diagnosis)**：识别轨迹中用户表现出满意或不满意的具体节点，分析 Agent 的 Plan/Act 是否符合用户预期。
2. **提炼 (Extraction)**：总结用户的个性化偏好（例如：用户是否倾向于更紧凑的步骤？是否偏好特定的代码风格？）。
3. **优化 (Optimization)**：修改 Meta-Skill 文档以适配上述发现。

# Constraints
- **结构维持**：严禁重写整个文档。必须严格保持初始 Meta-Skill 的 Markdown 结构、标题层级和关键字段。
- **最小化修改**：仅修改需要调整的段落，保留表现良好的指令部分。
- **一致性**：确保新增的规则不与文档中其他核心原则冲突。

## 轨迹数据（完整交互）
{trajectory_text}
"""


class ExecutionLoss(Module):
    """
    输入 meta-skill Variable 和 execution_view 文本（Prism-Trace 执行视图），
    用 backward engine 的 LLM 生成修改建议作为文本梯度；
    backward 时该建议写回 meta_skill.gradients，供 TGD.step() 使用。
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
        instruction = EXECUTION_LOSS_INSTRUCTION_TEMPLATE.format(
            num_rounds=num_rounds,
            trajectory_text=trajectory_text,
        )
        self.eval_prompt = Variable(
            instruction,
            requires_grad=False,
            role_description="evaluation instruction for meta-skill optimization",
        )
        self.llm_call = LLMCall(self.engine, self.eval_prompt)

    def forward(self, meta_skill: Variable) -> Variable:
        return self.llm_call(meta_skill)
