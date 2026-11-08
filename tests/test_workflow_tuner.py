import copy
import unittest

from scripts.tune_comfy_workflow import OFFICIAL_REF2VA_TEMPLATE, tune_workflow


def fixture() -> dict:
    def node(node_id, node_type, widgets=None, inputs=None, outputs=None):
        return {
            "id": node_id,
            "type": node_type,
            "widgets_values": widgets or [],
            "inputs": inputs or [],
            "outputs": outputs or [],
        }

    ref_inputs = [{"name": f"input-{index}", "link": None} for index in range(13)]
    return {
        "revision": 4,
        "nodes": [
            node(127, "UNETLoader", ["old-unet", "default"]),
            node(128, "CLIPLoader", ["old-clip", "minimax", "default"]),
            node(136, "MiniMaxH3ReferenceToVideo", inputs=ref_inputs),
            node(143, "PrimitiveInt", [20, "fixed"]),
            node(210, "PrimitiveBoolean", [False], [{"name": "value", "label": "old"}]),
            node(211, "PrimitiveBoolean", [False]),
            node(212, "ComfyMathExpression", ["old"], [{"name": "values.a"}, {"name": "values.b"}]),
            node(213, "ComfyMathExpression", ["old"], [{"name": "values.a"}, {"name": "values.b"}]),
            node(220, "StringReplace", ["", "@ref1", "<Picture 1>"]),
            node(221, "StringReplace"),
            node(222, "StringReplace"),
        ],
        "links": [
            [300, 201, 0, 220, 0, "STRING"],
            [301, 220, 0, 221, 0, "STRING"],
            [302, 221, 0, 222, 0, "STRING"],
            [303, 222, 0, 136, 9, "STRING"],
            [311, 211, 0, 212, 1, "BOOLEAN"],
            [313, 211, 0, 213, 1, "BOOLEAN"],
        ],
    }


class WorkflowTunerTests(unittest.TestCase):
    def test_replaces_brittle_string_chain_and_enables_native_bf16(self):
        tuned = tune_workflow(copy.deepcopy(fixture()))
        nodes = {node["id"]: node for node in tuned["nodes"]}

        self.assertEqual(nodes[127]["widgets_values"][0], "minimax_h3_ref2va_bf16.safetensors")
        self.assertEqual(nodes[128]["widgets_values"][0], "qwen3vl_32b_minimax_h3_bf16.safetensors")
        self.assertEqual(nodes[143]["widgets_values"][0], 25)
        self.assertEqual(nodes[212]["widgets_values"][0], "1344 + a*(-576)")
        self.assertEqual(nodes[213]["widgets_values"][0], "768 + a*(576)")
        self.assertEqual(nodes[220]["type"], "H3ReferencePrompt")
        self.assertEqual(nodes[220]["widgets_values"], [OFFICIAL_REF2VA_TEMPLATE])
        self.assertNotIn(211, nodes)
        self.assertNotIn(221, nodes)
        self.assertNotIn(222, nodes)
        self.assertIn([301, 220, 0, 136, 9, "STRING"], tuned["links"])
        self.assertEqual(nodes[136]["inputs"][9]["link"], 301)

    def test_can_keep_full_int8_as_fallback(self):
        tuned = tune_workflow(copy.deepcopy(fixture()), use_bf16=False)
        nodes = {node["id"]: node for node in tuned["nodes"]}
        self.assertEqual(nodes[127]["widgets_values"][0], "old-unet")
        self.assertEqual(nodes[128]["widgets_values"][0], "old-clip")


if __name__ == "__main__":
    unittest.main()
