from typing import Union

from textgrad.variable import Variable
from textgrad.autograd import LLMCall, Module
from textgrad.engine import EngineLM
from textgrad.config import validate_engine_or_get_default


CONTENT_STRATEGY_LOSS_INSTRUCTION_TEMPLATE = """# Role
你是一名「内容策略」优化专家。基于**当前的 content_strategy.md 文档**和**一条视频的真实社媒受众反馈**，持续提炼"什么内容在这个账号上有传播力"的规律。

# Context
- 数据来源：视频发布后的真实平台数据（标题、标签、发布时间、各平台点赞/收藏/评论/分享及增长趋势）。
- 任务目标：从受众反馈中提炼可跨视频复用的内容规律，更新账号内容策略；不涉及 Planner 的编排/执行逻辑。

# Instructions
请仔细阅读下方受众反馈数据，执行：
1. **诊断 (Diagnosis)**：各平台核心指标（点赞、收藏、评论、分享）哪些较强？哪些疲软？增长趋势是否健康？不同平台之间是否存在显著差异？
2. **提炼 (Extraction)**：哪类话题角度/标题模式/标签组合与好数据相关联？视频时长、发布时间是否有规律？
3. **优化 (Optimization)**：将上述规律增量写入 content_strategy.md，使各章节反映最新发现。

# Constraints
- **结构维持**：必须保留且仅保留以下六个二级标题（逐字一致，顺序不变）：
  - ## 账号定位
  - ## 内容方向
  - ## 视觉风格
  - ## 禁止内容
  - ## 发布节奏
  - ## 标题与标签规律
- **最小化修改**：在保留已有策略的前提下增量更新；用「- 」列表写具体规律，不要清空现有内容。
- **数据驱动**：每条新策略应能对应受众数据中的某个可观测信号，禁止纯主观猜测。
- **禁止**：不要在本文档记录 Planner 编排规则（那是 meta-skill 的职责）；不要粘贴原始数据表格。

## 受众反馈数据（audience_view）
{audience_view}
"""


class ContentStrategyLoss(Module):
    """
    输入 content_strategy Variable 和 audience_view 文本（Prism-Trace 受众视图），
    backward 时将 LLM 建议写入 content_strategy.gradients，供 TGD.step()。
    """

    def __init__(
        self,
        audience_view: str,
        engine: Union[EngineLM, str, None] = None,
    ):
        super().__init__()
        self.audience_view = audience_view
        self.engine = validate_engine_or_get_default(engine)
        instruction = CONTENT_STRATEGY_LOSS_INSTRUCTION_TEMPLATE.format(
            audience_view=audience_view,
        )
        self.eval_prompt = Variable(
            instruction,
            requires_grad=False,
            role_description="evaluation instruction for content strategy optimization",
        )
        self.llm_call = LLMCall(self.engine, self.eval_prompt)

    def forward(self, content_strategy: Variable) -> Variable:
        return self.llm_call(content_strategy)
