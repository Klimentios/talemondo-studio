#!/usr/bin/env python3
"""Compare installed dense BF16 SDPA kernels, without loading H3 or rendering.

Run with the H3 venv on the GPU host only when Comfy is idle. Synthetic results
identify kernel candidates; they do not establish full-render speed or quality.
"""
import argparse
import json
import time
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--comfy-url", default="http://127.0.0.1:18188")
    parser.add_argument("--tokens", type=int, default=4096)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    if not 256 <= args.tokens <= 16384 or not 1 <= args.repeats <= 10:
        parser.error("tokens: 256–16384; repeats: 1–10")
    with urllib.request.urlopen(args.comfy_url.rstrip("/") + "/queue", timeout=10) as response:
        queue = json.load(response)
    if queue.get("queue_running") or queue.get("queue_pending"):
        raise SystemExit("Refusing GPU benchmark: Comfy has active or pending work.")

    import torch
    from torch.nn.attention import SDPBackend, sdpa_kernel
    from torch.profiler import ProfilerActivity, profile

    if not torch.cuda.is_available():
        raise SystemExit("CUDA required")
    free, _ = torch.cuda.mem_get_info()
    if free < 2 * 1024**3:
        raise SystemExit("Refusing benchmark: less than 2 GiB free VRAM.")
    print(json.dumps({"torch": torch.__version__, "cuda": torch.version.cuda,
                      "gpu": torch.cuda.get_device_name(),
                      "capability": torch.cuda.get_device_capability(),
                      "shape": [1, 56, args.tokens, 128], "dtype": "bfloat16",
                      "note": "Synthetic dense attention; no H3 model or render loaded."}))
    torch.manual_seed(1234)
    # H3 dense DiT has 56 attention heads of dimension 128.
    q, k, v = [torch.randn(1, 56, args.tokens, 128, device="cuda", dtype=torch.bfloat16)
               for _ in range(3)]
    reference = None
    choices = [SDPBackend.FLASH_ATTENTION, SDPBackend.CUDNN_ATTENTION,
               SDPBackend.EFFICIENT_ATTENTION]
    with torch.inference_mode():
        for backend in choices:
            try:
                with sdpa_kernel(backend):
                    for _ in range(2):
                        result = torch.nn.functional.scaled_dot_product_attention(q, k, v)
                    torch.cuda.synchronize()
                    start = time.perf_counter()
                    for _ in range(args.repeats):
                        result = torch.nn.functional.scaled_dot_product_attention(q, k, v)
                    torch.cuda.synchronize()
                    elapsed = (time.perf_counter() - start) * 1000 / args.repeats
                    with profile(activities=[ProfilerActivity.CPU, ProfilerActivity.CUDA]) as prof:
                        result = torch.nn.functional.scaled_dot_product_attention(q, k, v)
                        torch.cuda.synchronize()
                if reference is None:
                    reference = result.clone()
                diff = (result.float() - reference.float())
                print(json.dumps({"backend": backend.name, "milliseconds": round(elapsed, 3),
                                  "max_abs_difference_from_first_supported": diff.abs().max().item(),
                                  "rms_difference": diff.square().mean().sqrt().item(),
                                  "operators": [e.key for e in prof.key_averages()
                                                if "attention" in e.key]}))
            except RuntimeError as error:
                print(json.dumps({"backend": backend.name, "unsupported": str(error)[:500]}))


if __name__ == "__main__":
    main()
