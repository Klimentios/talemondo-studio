import pytest

from app.sampling import sampling_available


def _info(sampler_spec, scheduler_spec):
    return {"KSamplerSelect": {"input": {"required": {"sampler_name": sampler_spec}}},
            "BasicScheduler": {"input": {"required": {"scheduler": scheduler_spec}}}}


@pytest.mark.parametrize("sampler_spec,scheduler_spec", [
    # Old /object_info format.
    ([["res_multistep", "er_sde"]], [["simple", "beta"], {}]),
    # Current ComfyUI format, as returned by the server image 20261006-1146.
    (["COMBO", {"multiselect": False, "options": ["res_multistep", "er_sde"]}],
     ["COMBO", {"multiselect": False, "options": ["simple", "beta"]}]),
])
def test_sampling_profile_seen_in_both_object_info_formats(sampler_spec, scheduler_spec):
    info = _info(sampler_spec, scheduler_spec)
    assert sampling_available(info, "standard")
    assert sampling_available(info, "motion_ab")


def test_sampling_profile_missing_option_is_unavailable():
    info = _info(["COMBO", {"options": ["res_multistep"]}], ["COMBO", {"options": ["simple"]}])
    assert sampling_available(info, "standard")
    assert not sampling_available(info, "motion_ab")
    assert not sampling_available({}, "standard")


@pytest.mark.parametrize("spec", [[["a.safetensors", "lora.safetensors"]],
                                  ["COMBO", {"options": ["a.safetensors", "lora.safetensors"]}]])
def test_model_file_checks_read_both_formats(spec):
    from app.ab_variant import ABVariant
    from app.director import missing_model_files
    info = {"LoraLoaderModelOnly": {"input": {"required": {"lora_name": spec}}}}
    assert missing_model_files("style3d", info) == ["3dsrx_minimax_h3_rank16-step00001250.safetensors"]
    spec_with = [list(spec[0]) + ["3dsrx_minimax_h3_rank16-step00001250.safetensors"]] if isinstance(spec[0], list) \
        else ["COMBO", {"options": spec[1]["options"] + ["3dsrx_minimax_h3_rank16-step00001250.safetensors"]}]
    info = {"LoraLoaderModelOnly": {"input": {"required": {"lora_name": spec_with}}}}
    assert missing_model_files("style3d", info) == []
    assert ABVariant("t", lora="style3d", lora_strength=1.0).missing_files(info) == []
