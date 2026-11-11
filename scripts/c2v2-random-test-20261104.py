#!/usr/bin/env python3
"""Random event sequence invariant test, TEST DB ONLY, schema c2v2."""
import json, hashlib, random, sys
sys.path.insert(0, "/tmp")
# reuse DSN logic by reading test script's get_dsn via exec of its header part
import importlib.util
# simpler: duplicate DSN parsing (no printing)
def get_dsn():
    for line in open("/home/ubuntu/ai-team/state/.cq-dsn"):
        line=line.strip()
        if line.startswith("CQ_DSN="):
            v=line[7:].strip().strip('"').strip("'")
            return v.replace("dbname=central_queue ","dbname=central_queue_test ").replace("/central_queue","/central_queue_test")
    raise SystemExit("no DSN")
import psycopg
def sha(b): return hashlib.sha256(b).hexdigest()
rng=random.Random(7)
dsn=get_dsn()
violations=[]; illegal_blocked=0; steps=0; rework_created=0
with psycopg.connect(dsn) as conn:
    with conn.cursor() as cur:
        cur.execute("TRUNCATE c2v2.incidents,c2v2.fence_history,c2v2.events,c2v2.leases,c2v2.tasks,c2v2.workers RESTART IDENTITY CASCADE")
        cur.execute("INSERT INTO c2v2.workers VALUES ('w-main','[\"shell\"]',ARRAY['infra','biz'])")
        ids=[]
        for i in range(30):
            tt = "deliverable" if i%3==0 else "script"
            lane = "biz" if tt=="deliverable" else "infra"
            payload = {"type":"llm_doc","rubric":"r1"} if tt=="deliverable" else {"type":"script","command":"echo x"}
            cur.execute("INSERT INTO c2v2.tasks(title,task_type,payload,requires,lane,min_bytes) VALUES (%s,%s,%s,%s,%s,1) RETURNING task_id",(f"rand-{i}",tt,json.dumps(payload),json.dumps({"capabilities":["shell"]}),lane))
            ids.append(cur.fetchone()[0])
        # dependency case: child waiting on parent
        cur.execute("INSERT INTO c2v2.tasks(title,task_type,payload,lane,status,depends_on) VALUES ('dep-child','script',%s,'infra','waiting',ARRAY[%s]) RETURNING task_id",(json.dumps({"type":"script","command":"echo d"}),ids[0])); dep_child=cur.fetchone()[0]
        conn.commit()
        def check(step):
            cur.execute("SELECT count(*) FROM c2v2.tasks WHERE status='done' AND verified_by IS NULL")
            if cur.fetchone()[0]: violations.append((step,"done_without_verified"))
            cur.execute("SELECT count(*) FROM c2v2.tasks"); total=cur.fetchone()[0]
            cur.execute("SELECT sum(c) FROM (SELECT count(*) c FROM c2v2.tasks GROUP BY status) s")
            if cur.fetchone()[0]!=total: violations.append((step,"status_sum"))
            cur.execute("SELECT count(*) FROM (SELECT task_id FROM c2v2.leases WHERE status='ACTIVE' GROUP BY 1 HAVING count(*)>1) s")
            if cur.fetchone()[0]: violations.append((step,"multi_lease"))
            cur.execute("SELECT count(*) FROM c2v2.fence_history fh WHERE EXISTS (SELECT 1 FROM c2v2.fence_history fh2 WHERE fh2.task_id=fh.task_id AND fh2.seen_at<fh.seen_at AND fh2.fencing_token>fh.fencing_token)")
            if cur.fetchone()[0]: violations.append((step,"fence_decrease"))
        for step in range(200):
            steps+=1
            tid=rng.choice(ids)
            cur.execute("SELECT status,task_type,parent_task_id,attempt FROM c2v2.tasks WHERE task_id=%s",(tid,)); row=cur.fetchone()
            if not row: continue
            st,tt,parent,att=row
            action=rng.choice(["claim","submit","verify","lease_expire","review_fail","cancel","pause","resume","illegal"])
            try:
                if action=="claim" and st=="pending":
                    cur.execute("UPDATE c2v2.tasks SET status='running',current_fencing_token=current_fencing_token+1 WHERE task_id=%s",(tid,)); cur.execute("SELECT current_fencing_token FROM c2v2.tasks WHERE task_id=%s",(tid,)); tok=cur.fetchone()[0]
                    cur.execute("INSERT INTO c2v2.leases(task_id,worker_id,fencing_token,expires_at) VALUES (%s,'w-main',%s,now()+interval '600 seconds')",(tid,tok))
                elif action=="submit" and st=="running":
                    cur.execute("UPDATE c2v2.tasks SET status='submitted',artifact_sha=%s,artifact_bytes=10,executor_supplier='supplier-A' WHERE task_id=%s",(sha(b"0123456789"),tid))
                elif action=="verify" and st=="submitted":
                    cur.execute("UPDATE c2v2.tasks SET status='verifying' WHERE task_id=%s",(tid,))
                    if tt=="deliverable":
                        cur.execute("UPDATE c2v2.tasks SET status='reviewing',reviewer_supplier='supplier-B' WHERE task_id=%s",(tid,))
                        if rng.random()<0.5:
                            cur.execute("UPDATE c2v2.tasks SET verified_by='c2-verifier',status='done' WHERE task_id=%s",(tid,))
                        else:
                            if att>=2:
                                cur.execute("UPDATE c2v2.tasks SET status='failed' WHERE task_id=%s",(tid,)); cur.execute("UPDATE c2v2.tasks SET status='dead' WHERE task_id=%s",(tid,)); cur.execute("INSERT INTO c2v2.incidents(parent_task_id,reason,attempts) VALUES (%s,'rework exhausted',2)",(tid,))
                            else:
                                cur.execute("INSERT INTO c2v2.tasks(title,task_type,payload,lane,parent_task_id,feedback,attempt) VALUES ('rework-rand','deliverable',%s,'biz',%s,'rand feedback',%s) RETURNING task_id",(json.dumps({"type":"llm_doc","rubric":"r1"}),tid,att+1)); rework_created+=1
                                cur.execute("UPDATE c2v2.tasks SET status='superseded' WHERE task_id=%s",(tid,))
                    else:
                        cur.execute("UPDATE c2v2.tasks SET verified_by='c2-verifier',status='done' WHERE task_id=%s",(tid,))
                    cur.execute("UPDATE c2v2.leases SET status='RELEASED' WHERE task_id=%s AND status='ACTIVE'",(tid,))
                elif action=="lease_expire" and st=="running":
                    cur.execute("UPDATE c2v2.leases SET status='EXPIRED' WHERE task_id=%s AND status='ACTIVE'",(tid,)); cur.execute("UPDATE c2v2.tasks SET loss_count=loss_count+1,status='retry' WHERE task_id=%s",(tid,))
                elif action=="cancel" and st in ("pending","waiting","paused"):
                    cur.execute("UPDATE c2v2.tasks SET status='cancelled' WHERE task_id=%s",(tid,))
                elif action=="pause" and st=="pending":
                    cur.execute("UPDATE c2v2.tasks SET status='paused' WHERE task_id=%s",(tid,))
                elif action=="resume" and st=="paused":
                    cur.execute("UPDATE c2v2.tasks SET status='pending' WHERE task_id=%s",(tid,))
                elif action=="illegal":
                    # try a forbidden transition, e.g. pending->done directly; must be blocked by trigger
                    try:
                        cur.execute("UPDATE c2v2.tasks SET status='done' WHERE task_id=%s",(tid,)); conn.commit()
                    except Exception:
                        conn.rollback(); illegal_blocked+=1
                    continue
                conn.commit()
            except Exception as e:
                conn.rollback()
                # rejected transitions / lease conflicts are expected in random test, not violations
                illegal_blocked+=1
            check(step)
        # dependency: parent done? if ids[0] done, child waiting->pending must be allowed; if not, child stays waiting (not a violation)
        cur.execute("SELECT status FROM c2v2.tasks WHERE task_id=%s",(ids[0],)); parent_status=cur.fetchone()[0]
        cur.execute("SELECT status FROM c2v2.tasks WHERE task_id=%s",(dep_child,)); dep_status=cur.fetchone()[0]
        if parent_status=="done":
            try:
                cur.execute("UPDATE c2v2.tasks SET status='pending' WHERE task_id=%s",(dep_child,)); conn.commit(); dep_status="pending-after-parent-done"
            except Exception as e:
                conn.rollback(); dep_status=f"blocked:{e}"
        cur.execute("SELECT status,count(*) FROM c2v2.tasks GROUP BY 1 ORDER BY 1"); counts=dict(cur.fetchall())
        cur.execute("SELECT count(*) FROM c2v2.incidents"); inc=cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM c2v2.tasks WHERE status='done' AND verified_by IS NULL"); dv=cur.fetchone()[0]
        print(json.dumps({"steps":steps,"violations":violations,"illegal_blocked":illegal_blocked,"rework_created":rework_created,"parent_status":parent_status,"dep_child_status":dep_status,"status_counts":counts,"incidents":inc,"done_without_verified":dv},ensure_ascii=False,indent=2))
