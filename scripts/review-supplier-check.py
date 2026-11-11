#!/usr/bin/env python3
"""Order15 review-supplier check: a review task MUST be executed by a model
from a different supplier than the producer model. Tier-2 allowed for review.
Usage: review-supplier-check.py <producer_model_id> <reviewer_model_id>
Exit 0 = different suppliers (OK); exit 1 = same supplier or unknown mapping.
Supplier mapping is prefix-based and documented; unknown models fail closed.
"""
import sys

def supplier(mid):
    m = (mid or "").lower()
    if m.startswith("glm-") or "zhipu" in m:
        return "Zhipu"
    if m.startswith("gemini-"):
        return "Google"
    if m.startswith("qwen"):
        return "Alibaba-Bailian"
    if "nemotron" in m:
        return "Nvidia"
    if m.startswith("agnes-"):
        return "Agnes AI"
    if m.startswith("deepseek-"):
        return "DeepSeek"
    if m.startswith("llama-") or m.startswith("probe-cf-") or m.startswith("gpt-oss"):
        return "Cloudflare Workers AI"
    if "/" in m:
        return "OpenRouter:" + m.split("/")[0]
    return "UNKNOWN"

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("usage: review-supplier-check.py <producer> <reviewer>")
        sys.exit(2)
    p, r = sys.argv[1], sys.argv[2]
    sp, sr = supplier(p), supplier(r)
    print(f"producer={p} supplier={sp}; reviewer={r} supplier={sr}")
    if sp == "UNKNOWN" or sr == "UNKNOWN" or sp == sr:
        print("FAIL: reviewer must be from a different supplier than producer")
        sys.exit(1)
    print("OK: different suppliers")
