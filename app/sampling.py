"""Studio sampling presets mapped to actual ComfyUI node inputs."""

from .object_info import input_options

SAMPLING_PROFILES = {
    "standard": ("res_multistep", "simple"),
    "motion_ab": ("er_sde", "beta"),
    "workflow_euler": ("euler", "simple"),
    # Comfy-Org R2V template note (workflow_templates 3c1df78): "beta or normal scheduler tends to outperform
    # simple for reference-heavy prompts". A/B only until a render beats the standard on the same seed.
    "ref_beta": ("res_multistep", "beta"),
}


def sampling_inputs(profile: str) -> tuple[str, str]:
    try:
        return SAMPLING_PROFILES[profile]
    except (KeyError, TypeError) as exc:
        raise ValueError("Сэмплинг: выбери стандартный или экспериментальный режим движения") from exc


def sampling_available(node_info: dict, profile: str) -> bool:
    sampler, scheduler = sampling_inputs(profile)
    return (sampler in input_options(node_info, "KSamplerSelect", "sampler_name")
            and scheduler in input_options(node_info, "BasicScheduler", "scheduler"))
