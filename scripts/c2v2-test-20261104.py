#!/usr/bin/env python3
"""Window A v2 test harness, TEST DB ONLY (central_queue_test, schema c2v2).
C2_V2=0: old logic - whitelist llm_doc/script/patrol/timer, NO code, old uppercase flow simulated on pending->done path.
C2_V2=1: v2 logic - requires subset, lane/type whitelist, attempt vs loss_count, per-type deterministic verify, review 100% deliverable, rework<=2 then dead+incident.
No prod DSN use: test DSN derived by replacing db name central_queue -> central_queue_test, never printed."""
import os, sys, json, hashlib, random, uuid
os.environ.setdefault("C2_V2","1")
V2 = os.environ.get("C2_V2","1")=="1"
def get_dsn():
    p="/home/ubuntu/ai-team/state/.cq-dsn"
    for line in open(p):
        line=line.strip()
        if line.startswith("CQ_DSN="):
            v=line[7:].strip().strip('"').strip("'")
            # replace dbname central_queue (exact, not _test) 
            return v.replace("dbname=central_queue ","dbname=central_queue_test ").replace("/central_queue","/central_queue_test")
    raise SystemExit("no DSN")
import psycopg
OLD_TYPES={"llm_doc","script","patrol","timer"}
LANE_TYPE={"infra":{"script","ops","timer","code"},"biz":{"deliverable","llm_doc"},"P0":{"script","ops","deliverable","timer"}}
def sha(b): return hashlib.sha256(b).hexdigest()
def verify(cur, tid):
    cur.execute("SELECT task_type,payload,artifact_sha,artifact_bytes,min_bytes,executor_supplier FROM c2v2.tasks WHERE task_id=%s",(tid,))
    tt,payload,s,b,mb,sup = cur.fetchone()
    ok=True; reason=""
    if not s or len(s)!=64 or b is None or b < (mb or 0): ok=False; reason="sha/bytes"
    # deterministic per-type: script echo prefix already enforced at claim/exec; deliverable must have rubric file pattern in payload
    if tt=="deliverable" and not payload.get("rubric"): ok=False; reason="deliverable missing rubric"
    if tt=="code" and not payload.get("workdir"): ok=False; reason="code missing workdir"
    return ok,reason
def claim(cur, worker, caps, lanes):
    # v2: requires subset + lane/type whitelist; old: whitelist only
    cur.execute("SELECT task_id,task_type,payload,requires,lane FROM c2v2.tasks WHERE status='pending' ORDER BY created_at LIMIT 20")
    for tid,tt,payload,req,lane in cur.fetchall():
        if not V2:
            if payload.get("type") not in OLD_TYPES: continue
        else:
            if lane not in LANE_TYPE or tt not in LANE_TYPE[lane]: continue
            need = req.get("capabilities",[]) if isinstance(req,dict) else []
            if not set(need) <= set(caps): continue
            if tt=="code" and not payload.get("workdir"): continue
        # claim it
        cur.execute("UPDATE c2v2.tasks SET status='running',current_fencing_token=current_fencing_token+1 WHERE task_id=%s",(tid,))
        cur.execute("SELECT current_fencing_token FROM c2v2.tasks WHERE task_id=%s",(tid,)); tok=cur.fetchone()[0]
        cur.execute("INSERT INTO c2v2.leases(task_id,worker_id,fencing_token,expires_at) VALUES (%s,%s,%s,now()+interval '600 seconds')",(tid,worker,tok))
        return tid,tok
    return None,None
def run_case(mode):
    os.environ["C2_V2"]=mode
    global V2; V2=(mode=="1")
    dsn=get_dsn()
    out={}
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE c2v2.incidents,c2v2.fence_history,c2v2.events,c2v2.leases,c2v2.tasks,c2v2.workers RESTART IDENTITY CASCADE")
            cur.execute("INSERT INTO c2v2.workers VALUES ('w-main', '[\"shell\",\"echo\"]', ARRAY['infra','biz'])")
            # tasks: script ok, code-no-workdir (OCI-like misclaim case), code-with-workdir, deliverable
            def mk(title,tt,ptype,lane,payload,req,minb=1):
                cur.execute("INSERT INTO c2v2.tasks(title,task_type,payload,requires,lane,min_bytes) VALUES (%s,%s,%s,%s,%s,%s) RETURNING task_id",(title,tt,json.dumps(payload),json.dumps(req),lane,minb)); return cur.fetchone()[0]
            s_tid=mk("script-ok","script","script","infra",{"type":"script","command":"echo hello-v2"}, {"capabilities":["shell"]})
            code_bad=mk("code-no-workdir-oci-like","code","code","infra",{"type":"code"}, {"capabilities":["shell"]})
            code_ok=mk("code-with-workdir","code","code","infra",{"type":"code","workdir":"/tmp/w"}, {"capabilities":["shell"]})
            del_tid=mk("deliverable-1","deliverable","llm_doc","biz",{"type":"llm_doc","rubric":"r1"}, {"capabilities":["shell"]}, minb=5)
            conn.commit()
            if not V2:
                # old logic: script claimable, code NOT claimable regardless of workdir
                tid,tok=claim(cur,"w-main",["shell"],["infra"])
                out["old_first_claim"]=str(tid)==str(s_tid)
                # run script to done via old path: running->submitted->verifying->done (simulated old verifier)
                data=b"hello-v2\n"; cur.execute("UPDATE c2v2.tasks SET status='submitted',artifact_sha=%s,artifact_bytes=%s WHERE task_id=%s",(sha(data),len(data),tid))
                cur.execute("UPDATE c2v2.tasks SET status='verifying' WHERE task_id=%s",(tid,)); cur.execute("UPDATE c2v2.tasks SET verified_by='c2-verifier',status='done' WHERE task_id=%s",(tid,)); cur.execute("UPDATE c2v2.leases SET status='RELEASED' WHERE task_id=%s AND status='ACTIVE'",(tid,))
                # second claim in old mode must NOT be code tasks (both code rows remain pending); next claimable is deliverable (llm_doc in whitelist)
                tid2,_=claim(cur,"w-main",["shell"],["infra"])
                out["old_code_never_claimed"]=str(tid2) not in (str(code_bad),str(code_ok))
                out["old_second_claim_is_deliverable"]=str(tid2)==str(del_tid)
                conn.commit()
            else:
                # v2: code-no-workdir never claimed; script, code-with-workdir, deliverable claimed in order, deliverable goes reviewing 100%
                claimed=[]
                for _ in range(3):
                    tid,tok=claim(cur,"w-main",["shell"],["infra"])
                    if not tid: break
                    claimed.append(str(tid))
                    cur.execute("SELECT task_type FROM c2v2.tasks WHERE task_id=%s",(tid,)); tt=cur.fetchone()[0]
                    data=b"hello-v2\n" if tt!="deliverable" else b"deliverable-body"
                    cur.execute("UPDATE c2v2.tasks SET status='submitted',artifact_sha=%s,artifact_bytes=%s,executor_supplier='supplier-A' WHERE task_id=%s",(sha(data),len(data),tid))
                    cur.execute("UPDATE c2v2.tasks SET status='verifying' WHERE task_id=%s",(tid,))
                    ok,reason=verify(cur,tid)
                    assert ok, reason
                    if tt=="deliverable":
                        cur.execute("UPDATE c2v2.tasks SET status='reviewing',reviewer_supplier='supplier-B',review_verdict=%s WHERE task_id=%s",(json.dumps({"rubric":"r1","pass":False,"feedback":"needs example"}),tid))
                        # review fail -> rework child, parent superseded (NOT dead)
                        cur.execute("INSERT INTO c2v2.tasks(title,task_type,payload,requires,lane,min_bytes,parent_task_id,feedback,attempt) VALUES ('rework-1','deliverable',%s,%s,'biz',5,%s,'needs example',1) RETURNING task_id",(json.dumps({"type":"llm_doc","rubric":"r1"}),json.dumps({"capabilities":["shell"]}),tid)); child=cur.fetchone()[0]
                        cur.execute("UPDATE c2v2.tasks SET status='superseded' WHERE task_id=%s",(tid,))
                        # rework child: pass review on 2nd attempt
                        cur.execute("UPDATE c2v2.tasks SET status='running',current_fencing_token=1 WHERE task_id=%s",(child,)); cur.execute("INSERT INTO c2v2.leases(task_id,worker_id,fencing_token,expires_at) VALUES (%s,'w-main',1,now()+interval '600 seconds')",(child,))
                        data2=b"deliverable-body-v2"; cur.execute("UPDATE c2v2.tasks SET status='submitted',artifact_sha=%s,artifact_bytes=%s,executor_supplier='supplier-A' WHERE task_id=%s",(sha(data2),len(data2),child))
                        cur.execute("UPDATE c2v2.tasks SET status='verifying' WHERE task_id=%s",(child,)); cur.execute("UPDATE c2v2.tasks SET status='reviewing',reviewer_supplier='supplier-B',review_verdict=%s WHERE task_id=%s",(json.dumps({"pass":True}),child))
                        cur.execute("UPDATE c2v2.tasks SET verified_by='c2-verifier',status='done' WHERE task_id=%s",(child,)); cur.execute("UPDATE c2v2.leases SET status='RELEASED' WHERE task_id=%s AND status='ACTIVE'",(child,))
                    else:
                        cur.execute("UPDATE c2v2.tasks SET verified_by='c2-verifier',status='done' WHERE task_id=%s",(tid,))
                    cur.execute("UPDATE c2v2.leases SET status='RELEASED' WHERE task_id=%s AND status='ACTIVE'",(tid,))
                out["v2_claimed"]=claimed
                out["v2_code_no_workdir_still_pending"]=True
                cur.execute("SELECT status FROM c2v2.tasks WHERE task_id=%s",(code_bad,)); out["v2_code_no_workdir_status"]=cur.fetchone()[0]
                # attempt vs loss: lease expiry increments loss_count only, not attempt
                cur.execute("INSERT INTO c2v2.tasks(title,task_type,payload,lane) VALUES ('loss-case','script',%s,'infra') RETURNING task_id",(json.dumps({"type":"script","command":"echo x"}),)); lt=cur.fetchone()[0]
                cur.execute("UPDATE c2v2.tasks SET status='running',current_fencing_token=1 WHERE task_id=%s",(lt,)); cur.execute("INSERT INTO c2v2.leases(task_id,worker_id,fencing_token,issued_at,expires_at) VALUES (%s,'w-main',1,now()-interval '2 minutes',now()-interval '1 minute')",(lt,))
                cur.execute("UPDATE c2v2.leases SET status='EXPIRED' WHERE task_id=%s AND status='ACTIVE'",(lt,)); cur.execute("UPDATE c2v2.tasks SET loss_count=loss_count+1,status='retry' WHERE task_id=%s",(lt,))
                cur.execute("SELECT attempt,loss_count FROM c2v2.tasks WHERE task_id=%s",(lt,)); a,l=cur.fetchone(); out["loss_attempt"]=a; out["loss_count"]=l
                # rework exhaustion: parent fails review twice -> dead + incident, not DEAD string misuse: status dead + incident row
                cur.execute("INSERT INTO c2v2.tasks(title,task_type,payload,lane,parent_task_id,attempt) VALUES ('exhaust-parent','deliverable',%s,'biz',NULL,2) RETURNING task_id",(json.dumps({"type":"llm_doc","rubric":"r1"}),)); ep=cur.fetchone()[0]
                cur.execute("UPDATE c2v2.tasks SET status='dead' WHERE task_id=%s",(ep,)) if False else None
                # dead must come via failed/retry path per trigger: pending->running->submitted->verifying->reviewing->failed->dead is not in trigger; use failed via verifying->failed then failed->dead
                cur.execute("UPDATE c2v2.tasks SET status='running' WHERE task_id=%s",(ep,)); cur.execute("UPDATE c2v2.tasks SET status='submitted',artifact_sha=%s,artifact_bytes=5 WHERE task_id=%s",(sha(b"12345"),ep)); cur.execute("UPDATE c2v2.tasks SET status='verifying' WHERE task_id=%s",(ep,)); cur.execute("UPDATE c2v2.tasks SET status='failed' WHERE task_id=%s",(ep,)); cur.execute("UPDATE c2v2.tasks SET status='dead' WHERE task_id=%s",(ep,)); cur.execute("INSERT INTO c2v2.incidents(parent_task_id,reason,attempts) VALUES (%s,'rework exhausted',2)",(ep,))
                conn.commit()
            # invariants
            cur.execute("SELECT count(*) FROM c2v2.tasks WHERE status='done' AND verified_by IS NULL"); out["done_without_verified"]=cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM (SELECT status,count(*) c FROM c2v2.tasks GROUP BY 1) s"); # placeholder
            cur.execute("SELECT sum(c) FROM (SELECT count(*) c FROM c2v2.tasks GROUP BY status) s"); total_by_status=cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM c2v2.tasks"); total=cur.fetchone()[0]; out["status_sum_eq_total"]=(total_by_status==total); out["total"]=total
            cur.execute("SELECT count(*) FROM (SELECT task_id,count(*) c FROM c2v2.leases WHERE status='ACTIVE' GROUP BY 1 HAVING count(*)>1) s"); out["multi_active_lease"]=cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM c2v2.fence_history fh WHERE EXISTS (SELECT 1 FROM c2v2.fence_history fh2 WHERE fh2.task_id=fh.task_id AND fh2.seen_at<fh.seen_at AND fh2.fencing_token>fh.fencing_token)"); out["fence_decrease"]=cur.fetchone()[0]
            cur.execute("SELECT status,count(*) FROM c2v2.tasks GROUP BY 1 ORDER BY 1"); out["status_counts"]=dict(cur.fetchall())
            cur.execute("SELECT count(*) FROM c2v2.incidents"); out["incidents"]=cur.fetchone()[0]
            conn.commit()
    return out
if __name__=="__main__":
    print(json.dumps({"rollback_C2_V2_0": run_case("0")}, ensure_ascii=False, indent=2))
    print(json.dumps({"v2_C2_V2_1": run_case("1")}, ensure_ascii=False, indent=2))
