"""Validate execution-bound measurements; missing telemetry stays unknown, never zero."""
import math


def validated_metrics(data, prompt_id):
    if not isinstance(data, dict) or data.get('version') != 1 or data.get('prompt_id') != prompt_id:
        return None
    passes = data.get('passes')
    if not isinstance(passes, list) or not 1 <= len(passes) <= 128:
        return None
    for item in passes:
        if not isinstance(item, dict): return None
        full, reused = item.get('full_forward_calls'), item.get('reused_forward_calls')
        if any(type(n) is not int or not 0 <= n <= 100000 for n in (full, reused)): return None
        elapsed = item.get('sample_wrapper_wall_seconds')
        if type(elapsed) not in (int, float) or not math.isfinite(elapsed) or elapsed < 0: return None
        if type(item.get('completed')) is not bool: return None
        numbers = item.get('reused_call_numbers')
        if not isinstance(numbers, list) or len(numbers) != reused or any(type(n) is not int or not 1 <= n <= full + reused for n in numbers): return None
    return data
