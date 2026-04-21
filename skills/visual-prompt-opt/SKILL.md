---
name: visual-prompt-opt
description: 面向视觉效果优化，为调用 Seedream 与 Seedance 的 execution_prompt 提供书写规范（占位符、资产映射、镜头与动作描述、负向约束等），提升生成画面质量与一致性；本规范同时适用于两种模型。
---

# 视觉提示词优化（VISUAL_PROMPT_OPT）

## 适用模型

本 Skill 的 execution_prompt 书写规范**同时适用于 Seedream 与 Seedance**。填写时无需区分具体调用哪个模型，统一按本规范即可。

## 何时使用

本 Skill 在规划**会调用 video_generate_tool 或 batch_video_generate_tool** 的阶段时配合使用（无论底层为 Seedream 还是 Seedance）。填写该阶段 timeline 中每条的 `execution_prompt` 前，请先通过 load_skill_tool(skill_type=VISUAL_PROMPT_OPT) 加载本 Skill，并按下列规范书写。

## 占位符与顺序

- `image_paths` 与 `video_path` 的顺序与文案中的引用**必须一致**。
- 在 execution_prompt 中使用：`[图1]`、`[图2]`、`[图3]` 对应 reference_resources 中提取出的**图片**顺序；`[视频1]`、`[视频2]` 对应**视频**顺序。
- 禁止在文案中写「图1」却让 image_paths 顺序与描述错位；禁止只写占位符而不说明该图/视频的用途。

## 推荐结构（每段 execution_prompt）

对每个 timeline 项的 execution_prompt，建议包含以下部分（可合并为一段，但逻辑清晰）：

### 1. Assets Mapping（资产映射）

明确 [图1]/[图2]/[视频1] 各自用途，例如：
- [图1]：角色身份锚点（保持面部特征、服装一致）
- [图2]：场景/环境参考（光线、风格）
- [视频1]：镜头语言与动作节奏参考
- 若为段间连续： [图1] 为上一镜尾帧，用于接续

### 2. Final Prompt（主描述）

按时间或镜头写清**动作、景别、光线、镜头运动**：
- 用**具体镜头与动作描述**，避免笼统用语（如「好看」「生动」）。
- 镜头与动作优先：场景为骨、动作为魂；多用「缓慢移动」「轻微转头」等微动作，少用「跳舞」「奔跑」等宏观词。
- 可按时段分段写，例如：0–3s：…；3–7s：…；7–10s：…。
- 段间连续时显式写「从 [图1] 结尾画面自然接续」「保持角色/场景一致」。

### 3. Generation Settings（可选）

若需在文案中强调：时长、比例等，与 timeline 的 technical_params 一致即可（如 10s、16:9）。

## 书写原则

- **描述具体**：避免「更好看」「更生动」等模糊词；写出景别（特写/中景/全景）、光线（顺光/侧光）、镜头运动（推/拉/摇/移）。
- **有参考图时**：必须在文案中写明各图用途（角色身份、场景、接续帧等）。
- **段间连续**：显式写「从上一镜结尾接续」「保持角色/场景一致」。
- **稳定性**：在 Final Prompt 中写明 stabilized、no jitter、face/structure consistency 等表述，减少抖动与失真。

## 示例

以下为符合上述结构的 execution_prompt 示例（单段、含 [图1][图2] 与负向约束）：

```
Assets Mapping: [图1] 角色身份锚点（保持面部与服装）；[图2] 场景与光线参考。
Final Prompt: 16:9，约 10 秒，电影感。0–3s：角色自画面左侧缓步入画，中景，顺光，镜头轻微跟随；3–7s：角色转身面向镜头，缓慢抬手，特写至中景；7–10s：保持 [图2] 场景光线，角色退向画面深处。从 [图1] 保持人物一致，动作平稳。stabilized, no jitter, face/structure consistency.
Generation Settings: Duration 10s, Aspect Ratio 16:9.
```

若该段使用多图且含上一镜尾帧，可将 [图1] 标为「上一镜尾帧，接续」，[图2] 为角色参考，[图3] 为场景参考，并在 Final Prompt 中写「从 [图1] 画面自然接续」。

---

## 风格化模板库

以下为可直接复用的风格化 execution_prompt 模板，按风格分类。使用时将 Assets Mapping / Generation Settings 替换为实际资产与参数。

### 数字CG·东方古风玄幻修仙

```
Assets Mapping: [图1] 角色身份锚点（保持面部特征与服装）；[图2] 场景/环境参考（光线、氛围）。
Final Prompt: 数字CG风格，UE5渲染，超广角构图。云雾缭绕，充满神秘感且细节繁复；前景柳叶飞溅点缀，动态模糊，张力十足，恢宏浩荡，极具压迫感。低饱和，强透视，极具视觉冲击力；光影交错，真实光线反射，墨绿灰白色调——白处冷亮锐利，灰处沉郁如墨。东方古风玄幻修仙氛围，8K摄影画质。从 [图1] 保持人物一致，动作平稳。
Generation Settings: Duration 10s, Aspect Ratio 16:9.
```

**适用场景**：修仙/玄幻题材的宏景、仙境远景、BOSS 登场、门派驻地等大场面镜头。

**关键词拆解**：
- 渲染质感：数字CG风格 / UE5渲染 / 8K摄影画质
- 构图张力：超广角构图 / 强透视 / 极具视觉冲击力
- 氛围细节：云雾缭绕 / 柳叶飞溅 / 动态模糊 / 恢宏浩荡 / 极具压迫感
- 色调：低饱和 / 墨绿灰白 / 白处冷亮锐利 / 灰处沉郁如墨
- 光影：光影交错 / 真实光线反射
- 题材基调：东方古风 / 玄幻修仙
