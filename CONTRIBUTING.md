# Contributing

欢迎提 Issue 与 PR。先读 README 的“部署门禁（必读）”与“已知问题”。

## 提 PR 之前

1. **绝不提交真实凭据、真实姓名、内网地址、个人邮箱。**
   凭据只放环境变量或外部凭据文件；文档里只称“董事长”，不写真实姓名；
   内网地址用环境变量或 `<占位符>`。
2. 跑检查：
   ```bash
   python3 -m py_compile scripts/<改过的文件>.py
   bash -n scripts/<改过的脚本>.sh
   python3 scripts/dename-lint.py --strict <改过的文件>
   ```
   `dename-lint.py` 的真实姓名检查由环境变量 `CHAIRMAN_REAL_NAME` 指定；
   未设置时只做模式检查（邮箱/手机号/身份证号/明文凭据赋值）。
   注意：该脚本的“疑似明文凭据赋值”是宽松模式匹配，`token = os.environ.get(...)`、
   `token = open(...).read()` 这类读环境/读文件的写法是误报；只有写死的值才算违规。
3. 改动门禁（`scripts/gate-svc.py`、`ceo-seat/.opencode/plugins/gate.js`）
   的 PR 须在描述里写清：改了什么校验、回滚怎么做、验收步骤是什么。
   安全件不接受“顺手改”。
4. 新增技能包须在 PR 里写明来源与许可证；来源不明或许可证与 MIT 冲突的
   不进主仓（见 `THIRD_PARTY_NOTICES.md`）。

## Issue 规范

- 缺陷：贴复现步骤 + 期望行为 + 实际行为，附日志关键行（先脱敏）。
- 功能建议：写清解决什么问题、不用它会怎样、验收标准是什么。
- 安全问题不要开公开 Issue，见 `SECURITY.md`。

## 发布流程（维护者）

新版本 = 打 tag + 写 CHANGELOG（改了什么、已知问题变了没有）。
推公开仓库前按 README 的检查清单复核：无凭据、无 `.bak`、无运行数据、
`git ls-files` 与脱敏 grep 抽查通过。
