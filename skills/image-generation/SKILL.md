---
name: image-generation
description: 小红书封面图自动生成：把内容流水线岗位 3 产出的生图 Prompt 变成真实封面图并落盘，对齐契约证据格式。主路线 Pollinations（免费无 key），降级无图不阻塞；待升级 newapi/CF flux 直连。触发词：配图、封面、生图、cover、image-generation、给日记配图。
---

# Image Generation（自动配图）

来源：2026-10-20 经"外部技能吸收与同化流水线"落地。刚需触发类型=新需求匹配（日记流水线只有生图 Prompt、没有出图能力）。

## 调用方式（契约对齐）
- 输入：生图 Prompt（由 content-pipeline 岗位 3 产出，须遵守其纪律：禁发明道具、机器人只写"圆润可爱的小机器人"、孩子年龄写"学龄前"）+ 目标路径。
- 执行：`python3 ai-team/scripts/gen_cover.py --prompt "<提示词>" --out <artifacts/cover-<task_id>.jpg>`
- 输出：一行 JSON（ok/path/bytes/engine）；证据回执按 verify.py 硬规格记 file_exists + sha256。
- 失败：退出码 2 → 调用方走降级（见下），不许重试超过脚本内建的 1 次。

## 路线与体检记录（2026-10-20 沙箱实测）
- **主路线 Pollinations**（image.pollinations.ai）：免费、无需 key。沙箱实测 HTTP 200、真实 JPEG（魔数 ffd8ff、52KB）。防投毒：本地不运行任何第三方代码；脚本不接触任何凭据；唯一出站数据是提示词文本本身——因此**只许传画面描述，严禁把孩子照片、真实姓名、聊天记录发给该服务**。
- **降级兜底**：主路线失败 → 脚本内重试 1 次 → 仍失败则无图交付并在回执标注"封面缺失"，**绝不阻塞日记发布**。
- **待组装升级（需董事长点头）**：newapi 渠道 #3（CF Workers AI）的模型列表加上 `@cf/black-forest-labs/flux-1-schnell` 后，主路线切自家基建（key 走工人现行 NEWAPI_KEY 注入，实测 key 有效、当前仅差模型未挂载，报错 model_not_found），Pollinations 降为兜底。切换后更新本节。

## 隔离与安全
- 首次实测在枢纽 /tmp/imgtest 沙箱完成，未碰主环境。
- gen_cover.py 已过 code-review 第 2/6 维自查：无凭据、无写盘范围外操作（只写 --out 指定路径）、有超时（120s）与重试上限。

## 不做什么
- 不做图生图、不处理真人照片（儿童隐私红线）；不生成带文字的封面（模型文字渲染不可靠，标题文字由排版环节另行处理）。

## 已知问题（2026-10-20 首测样品）
- 提示词遵循度有限：双主体（孩子+机器人）时机器人可能被弱化甚至缺失——出图后必须人工/视觉验收，不合格就换提示词重出（把机器人描述提前并加重，如"画面中央一个圆润的白色小机器人"）。
- Pollinations 免费通道右下角带水印（nologo 参数实测未生效）：发布前裁掉底部约 8% 条带，或等 flux 主路线切换后消除。
