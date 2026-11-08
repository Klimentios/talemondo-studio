import asyncio
import json
import unittest

import httpx

from local_studio.compute.vast import (
    VastAuthError,
    VastClient,
    VastOfferUnavailable,
    VastRateLimitError,
    VastResponseError,
    VastUnavailableError,
    is_h3_compatible_offer,
    normalize_offer,
)


def h3_offer(**changes):
    row = {
        "id": 81234,
        "gpu_name": "RTX PRO 6000 Blackwell",
        "gpu_arch": "nvidia",
        "num_gpus": 1,
        "gpu_frac": 1.0,
        "gpu_ram": 96000,
        "cpu_ram": 512000,
        "disk_space": 768,
        "cuda_max_good": 12.8,
        "verification": "verified",
        "rentable": True,
        "rented": False,
        "is_bid": False,
        "reliability": 0.9991,
        "dph_total": 1.71,
        "dph_base": 1.3,
        "storage_cost": 0.42,
        "inet_up_cost": 0.01,
        "inet_down_cost": 0.02,
        "geolocation": "CY",
    }
    return {**row, **changes}


class VastOfferNormalizationTests(unittest.TestCase):
    def test_accepts_complete_h3_compatible_offer_and_preserves_units(self):
        offer = normalize_offer(h3_offer())

        self.assertEqual(offer["id"], 81234)
        self.assertEqual(offer["gpu_vram_mb"], 96000)
        self.assertEqual(offer["cpu_ram_mb"], 512000)
        self.assertEqual(offer["disk_space_gb"], 768)
        self.assertEqual(offer["total_usd_per_hour"], 1.71)
        self.assertEqual(offer["storage_usd_per_gb_month"], 0.42)
        self.assertEqual(offer["upload_usd_per_gb"], 0.01)
        self.assertEqual(offer["location"], "CY")

    def test_rejects_each_incompatible_or_unknown_hardware_value(self):
        invalid = (
            {"gpu_arch": "amd"},
            {"num_gpus": 2},
            {"gpu_frac": 0.5},
            {"gpu_ram": 94371},  # 90,000 MiB rounds up to 94,372 MB in Vast's API units.
            {"cpu_ram": 255999},
            {"disk_space": 499},
            {"cuda_max_good": 12.7},
            {"verification": "unverified"},
            {"rentable": False},
            {"rented": True},
            {"is_bid": True},
            {"dph_total": 0},
            {"gpu_ram": None},
            {"cpu_ram": None},
            {"disk_space": None},
            {"cuda_max_good": None},
            {"verification": None},
            {"is_bid": None},
        )
        for changes in invalid:
            with self.subTest(changes=changes):
                self.assertFalse(is_h3_compatible_offer(h3_offer(**changes)))

    def test_uses_vast_documented_verified_field_when_present(self):
        self.assertTrue(is_h3_compatible_offer(h3_offer(verification=None, verified=True)))
        self.assertFalse(is_h3_compatible_offer(h3_offer(verification=None, verified=False)))
        self.assertFalse(is_h3_compatible_offer(h3_offer(verification="deverified", verified=True)))


class VastClientTests(unittest.TestCase):
    def run_async(self, coroutine):
        return asyncio.run(coroutine)

    def make_client(self, handler):
        transport = httpx.MockTransport(handler)
        http = httpx.AsyncClient(transport=transport)
        self.addCleanup(lambda: asyncio.run(http.aclose()))
        return VastClient("secret-test-token", http_client=http)

    def test_search_uses_official_constraints_and_local_h3_guard(self):
        requests = []

        def handler(request):
            requests.append(request)
            self.assertEqual(request.method, "POST")
            self.assertEqual(request.url.path, "/api/v0/bundles/")
            self.assertEqual(request.headers["Authorization"], "Bearer secret-test-token")
            body = json.loads(request.content)
            self.assertEqual(body["type"], "ondemand")
            self.assertEqual(body["verified"], {"eq": True})
            self.assertEqual(body["rentable"], {"eq": True})
            self.assertEqual(body["rented"], {"eq": False})
            self.assertEqual(body["gpu_arch"], {"eq": "nvidia"})
            self.assertEqual(body["num_gpus"], {"eq": 1})
            self.assertEqual(body["gpu_ram"], {"gte": 94372})
            self.assertEqual(body["cpu_ram"], {"gte": 256000})
            self.assertEqual(body["disk_space"], {"gte": 500})
            self.assertEqual(body["cuda_max_good"], {"gte": 12.8})
            self.assertEqual(body["gpu_frac"], {"gte": 1})
            self.assertEqual(body["order"], [["dph_total", "asc"]])
            return httpx.Response(200, json={"offers": [h3_offer(), h3_offer(id=2, gpu_ram=90000)]})

        client = self.make_client(handler)
        offers = self.run_async(client.search_h3_offers(max_hourly_rate=2.0))

        self.assertEqual([row["id"] for row in offers], [81234])
        self.assertEqual(len(requests), 1)

    def test_search_applies_max_hourly_rate_without_auto_selecting(self):
        def handler(request):
            body = json.loads(request.content)
            self.assertEqual(body["dph_total"], {"lte": 1.5})
            return httpx.Response(200, json={"offers": [h3_offer()]})

        offers = self.run_async(self.make_client(handler).search_h3_offers(max_hourly_rate=1.5))
        self.assertEqual(offers, [])

    def test_account_drops_extra_fields_and_validates_balance(self):
        def handler(request):
            self.assertEqual(request.method, "GET")
            self.assertEqual(request.url.path, "/api/v0/users/current/")
            return httpx.Response(200, json={"id": 13, "email": "user@example.test", "balance": 42.5,
                                             "ssh_key": "must not leak", "key": "secret"})

        result = self.run_async(self.make_client(handler).get_account())
        self.assertEqual(result, {"id": 13, "email": "user@example.test", "balance": 42.5})

    def test_instances_are_normalized_to_a_list(self):
        def handler(request):
            self.assertEqual(request.url.path, "/api/v1/instances/")
            self.assertEqual(request.url.params["limit"], "25")
            self.assertNotIn("jupyter_token", request.url.params["select_cols"])
            return httpx.Response(200, json={"instances": [{"id": 55, "actual_status": "running"}]})

        self.assertEqual(self.run_async(self.make_client(handler).get_instances()),
                         [{"id": 55, "actual_status": "running", "intended_status": None,
                           "gpu_name": None, "num_gpus": None, "gpu_ram_mb": None,
                           "cpu_ram_mb": None, "disk_space_gb": None,
                           "total_usd_per_hour": None, "ssh_host": None,
                           "ssh_port": None, "label": None}])

    def test_instance_list_follows_provider_cursor(self):
        calls = []
        def handler(request):
            calls.append(request.url.params.get("after_token"))
            if len(calls) == 1:
                return httpx.Response(200, json={"instances": [{"id": 1, "actual_status": "running"}],
                                                 "next_token": "cursor-2"})
            self.assertEqual(request.url.params["after_token"], "cursor-2")
            return httpx.Response(200, json={"instances": [{"id": 2, "actual_status": "stopped"}],
                                             "next_token": None})

        self.assertEqual([x["id"] for x in self.run_async(self.make_client(handler).get_instances())], [1, 2])
        self.assertEqual(calls, [None, "cursor-2"])

    def test_get_instance_normalizes_only_connection_and_compute_fields(self):
        def handler(request):
            self.assertEqual(request.method, "GET")
            self.assertEqual(request.url.path, "/api/v0/instances/55/")
            return httpx.Response(200, json={"instances": {"id": 55, "actual_status": "running",
                "ssh_host": "ssh2.vast.ai", "ssh_port": 18000, "jupyter_token": "must not leak"}})

        result = self.run_async(self.make_client(handler).get_instance(55))
        self.assertEqual(result["ssh_host"], "ssh2.vast.ai")
        self.assertEqual(result["ssh_port"], 18000)
        self.assertNotIn("jupyter_token", result)

    def test_create_uses_fixed_image_on_demand_and_requested_disk(self):
        def handler(request):
            self.assertEqual(request.method, "PUT")
            self.assertEqual(request.url.path, "/api/v0/asks/81234/")
            body = json.loads(request.content)
            self.assertEqual(body, {"image": "vastai/base-image:cuda-12.8.1-auto",
                                    "disk": 500, "runtype": "ssh", "target_state": "running",
                                    "cancel_unavail": True, "label": "H3 Studio"})
            return httpx.Response(200, json={"success": True, "new_contract": 9981})

        result = self.run_async(self.make_client(handler).create_instance(81234, 500))
        self.assertEqual(result, {"instance_id": 9981})

    def test_stop_and_destroy_use_documented_api_contracts(self):
        seen = []

        def handler(request):
            seen.append((request.method, request.url.path,
                         json.loads(request.content) if request.content else None))
            return httpx.Response(200, json={"success": True})

        client = self.make_client(handler)
        self.run_async(client.start_instance(9981))
        self.run_async(client.stop_instance(9981))
        self.run_async(client.destroy_instance(9981))
        self.assertEqual(seen, [
            ("PUT", "/api/v0/instances/9981/", {"state": "running"}),
            ("PUT", "/api/v0/instances/9981/", {"state": "stopped"}),
            ("DELETE", "/api/v0/instances/9981/", None),
        ])

    def test_provider_errors_are_typed_and_do_not_include_raw_body_or_key(self):
        cases = (
            (401, VastAuthError),
            (403, VastAuthError),
            (429, VastRateLimitError),
            (410, VastResponseError),
            (503, VastUnavailableError),
        )
        for status, error_type in cases:
            with self.subTest(status=status):
                def handler(_):
                    return httpx.Response(status, text="secret-test-token private host text")

                client = self.make_client(handler)
                with self.assertRaises(error_type) as caught:
                    self.run_async(client.get_instances())
                self.assertNotIn("secret-test-token", str(caught.exception))
                self.assertNotIn("private host text", str(caught.exception))

    def test_expired_offer_maps_to_offer_unavailable(self):
        def handler(_):
            return httpx.Response(410, text="offer expired")

        client = self.make_client(handler)
        with self.assertRaises(VastOfferUnavailable):
            self.run_async(client.create_instance(81234))

    def test_malformed_json_and_response_shape_raise_sanitized_error(self):
        for response in (httpx.Response(200, text="not-json"), httpx.Response(200, json={"offers": "no"})):
            with self.subTest(response=response):
                def handler(_):
                    return response

                with self.assertRaises(VastResponseError):
                    self.run_async(self.make_client(handler).search_h3_offers())

    def test_max_rate_must_be_positive_finite(self):
        for value in (0, -1, float("inf"), float("nan")):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    self.run_async(self.make_client(lambda _: httpx.Response(200, json={"offers": []}))
                                   .search_h3_offers(max_hourly_rate=value))


if __name__ == "__main__":
    unittest.main()
