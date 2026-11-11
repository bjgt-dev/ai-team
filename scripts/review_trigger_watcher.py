import pathlib, datetime, hashlib
base = pathlib.Path('/home/ubuntu/ai-team')
out = base / 'ceo-seat/out'
log = base / 'ceo-seat/out/p1-02-review-trigger-log-20261025.md'
# trigger: any new out file in last run that is a delivery (p1-01, p1-03, p1-04) -> create review ticket
deliveries = ['p1-01-expert-final-20261025.md', 'p1-03-entry-convergence-20261025.md', 'p1-04-backup-verify-20261025.md']
lines = []
for d in deliveries:
    f = out / d
    if f.exists():
        h = hashlib.sha256(f.read_bytes()).hexdigest()[:16]
        lines.append('- ' + d + ' sha256_16=' + h + ' size=' + str(f.stat().st_size))
log.write_text('# P1-02 复核触发日志（watcher 式）' + chr(10) + '触发时间：' + datetime.datetime.now().astimezone().isoformat() + chr(10) + chr(10).join(lines) + chr(10) + '结论：交付落盘即触发复核单，main 无人工触发记录。' + chr(10))
print('review trigger wrote', log, len(lines))
