# B 级定案请求（待大管家席位定案，2026-11-02）

状态：已提交席位重试环后台取得定案（仿 stage2 run-seat-loop 模式，锁空闲时自动获得席位，最多 60 轮）；本文件为请求原文，定案全文见最终审计目录 b-decision-20261102.md（取得后补）。

定案事项（实施前必须全部定案，含备份、明确回滚、2 小时窗口、实测、独立复核）：
1. l3_claim.py 增加 fencing：更新任务状态时必须校验 claim token，旧 token 覆写必须被拒绝；已 CLAIMED 任务的重复领取必须被拒绝。现有两条 DONE/token None 记录（L3-004-review3、L3-OP-UGC-001）为历史事实，禁止事后伪造 token。
2. 席位锁架构：ceo-opencode.sh 从“长会话持锁”改为“短锁领单 + 独立执行 + 短锁回写”；holder 文件加进程启动时间校验，防 PID 重用。
3. workers.json 纠正：2026-11-02 13:44 InstaBox API 只读实测只返回 5 台机器（worker-1 至 worker-5），worker-6 不在列表中；定案 worker-6 条目的处理方式（删除/标注未验证/保留更正记录），不得直接删除历史记录而不留更正记录。
4. supervisor.py docstring 与代码不一致的修正：aging、heartbeat、fencing、flock 四处 docstring 宣称与代码实际不符，定案时确认以代码为准还是补实现（aging 与 fencing 属功能缺失，须单独定案是否补实现）。

约束：Zen 通道 2026-11-02 13:34 起 DEAD，定案须如实注明实际脑与通道，不得假装用 Zen；60 轮内锁未空闲则如实报告未取得定案，不抢锁、不杀进程。
