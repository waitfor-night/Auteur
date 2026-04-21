---
name: script-lens
description: 剧本解析与参考图生成。从剧本中抽取角色和场景实体，并为其生成参考图。
---

# c- 剧本解析与参考图生成

## 功能

1. **剧本解析**：从剧本文本中抽取核心角色和场景实体
2. **参考图生成**：为抽取的角色和场景生成参考图

## 剧本解析

### 工具

`extract_script_entities_tool`

### 输入

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| script | str | 是 | 剧本/分镜/故事脚本的完整文本 |

### 输出

```json
{
  "characters": [
    {"name": "角色名", "description": "简短描述"},
    ...
  ],
  "scenes": [
    "场景1描述",
    "场景2描述",
    ...
  ]
}
```

### 输出格式

```json
{
  "characters": [
    {"name": "<角色名>", "description": "<角色描述>"},
    ...
  ],
  "scenes": [
    "<场景描述>",
    ...
  ]
}
```

## 参考图生成

### 前置条件

- 如果有 `output_dir`，建议传给 `generate_image_tool`
- 如果已有 `image_paths`，可作为参考图输入给 `generate_image_tool`

### 为角色生成参考图

```python
generate_image_tool(
    text="<角色名> <角色描述>",
    filename_prefix="char_<角色名>",
    output_dir=output_dir,
    ref_image=<已有参考图路径>  # 可选
)
```

### 为场景生成参考图，如果涉及到角色活动场景建议参考角色图

```python
generate_image_tool(
    text="<场景描述>",
    filename_prefix="scene_<序号>",
    output_dir=output_dir,
    ref_image=<已有参考图路径>  # 可选
)
```

### 输出

角色图和场景图路径映射：

```json
{
  "characters": {
    "<角色名>": "<角色图路径>",
    ...
  },
  "scenes": {
    "scene_<序号>": "<场景图路径>",
    ...
  }
}
```
