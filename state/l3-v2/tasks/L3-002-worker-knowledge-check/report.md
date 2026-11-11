# L3-002 Worker-1 执行报告（修正版）
- 第一次 exec：exit 0，stdout 为 ls hub 路径失败 + count 0 + worker1-knowledge-check-ok。结论：worker-1 无 hub 文件系统访问权限，count 0 不是知识资产为 0。
- 第二次 exec：HTTP bad_request，request-id req_wm6fp2eydz2dtktahlfsyb4bai，retryable false，原因是我方 JSON 转义错误（payload 构造缺陷），不是 worker 离线或 409。
- 结论：worker-1 适合确定性脚本任务（嵌入式 payload，如 stage2 时的 base64 方式），不适合直接读 hub 文件；知识资产核查须经 hub-local 或嵌入式 payload。
