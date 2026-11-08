from __future__ import annotations

import math
import json
from collections.abc import Mapping
from typing import Any

import httpx


API_BASE = "https://console.vast.ai"
API_TIMEOUT = httpx.Timeout(20.0, connect=5.0)
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
OFFER_LIMIT = 100

# Vast documents gpu_ram and cpu_ram in decimal MB. H3's preflight requires
# 90,000 MiB, so the API search threshold is rounded up to decimal MB.
MIN_GPU_RAM_MB = math.ceil(90_000 * 1_048_576 / 1_000_000)
MIN_CPU_RAM_MB = 256_000
MIN_DISK_GB = 500
MIN_CUDA_VERSION = 12.8


class VastError(RuntimeError):
    code = "provider_error"


class VastAuthError(VastError):
    code = "authentication"


class VastRateLimitError(VastError):
    code = "rate_limited"


class VastUnavailableError(VastError):
    code = "unavailable"


class VastOfferUnavailable(VastError):
    code = "offer_unavailable"


class VastResponseError(VastError):
    code = "invalid_provider_response"


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _integer(value: Any) -> int | None:
    number = _number(value)
    return int(number) if number is not None and number.is_integer() else None


def is_h3_compatible_offer(row: Mapping[str, Any]) -> bool:
    """Fail closed when the marketplace omits any H3 compatibility field."""
    gpu_ram = _number(row.get("gpu_ram"))
    cpu_ram = _number(row.get("cpu_ram"))
    disk = _number(row.get("disk_space"))
    cuda = _number(row.get("cuda_max_good"))
    count = _integer(row.get("num_gpus"))
    fraction = _number(row.get("gpu_frac"))
    price = _number(row.get("dph_total"))
    verification = row.get("verification")
    verified = verification == "verified" if isinstance(verification, str) else row.get("verified") is True

    return bool(
        row.get("gpu_arch") == "nvidia"
        and count == 1
        and fraction is not None and fraction >= 1.0
        and gpu_ram is not None and gpu_ram >= MIN_GPU_RAM_MB
        and cpu_ram is not None and cpu_ram >= MIN_CPU_RAM_MB
        and disk is not None and disk >= MIN_DISK_GB
        and cuda is not None and cuda >= MIN_CUDA_VERSION
        and verified
        and row.get("rentable") is True
        and row.get("rented") is False
        and row.get("is_bid") is False
        and price is not None and price > 0
    )


def normalize_offer(row: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(row, Mapping):
        raise VastResponseError("Vast.ai returned an invalid offer")
    offer_id = _integer(row.get("id"))
    if offer_id is None or offer_id <= 0:
        raise VastResponseError("Vast.ai returned an offer without a valid ID")

    return {
        "id": offer_id,
        "gpu_name": row.get("gpu_name") if isinstance(row.get("gpu_name"), str) else None,
        "gpu_arch": row.get("gpu_arch") if isinstance(row.get("gpu_arch"), str) else None,
        "num_gpus": _integer(row.get("num_gpus")),
        "gpu_fraction": _number(row.get("gpu_frac")),
        "gpu_vram_mb": _number(row.get("gpu_ram")),
        "cpu_ram_mb": _number(row.get("cpu_ram")),
        "disk_space_gb": _number(row.get("disk_space")),
        "cuda_max_good": _number(row.get("cuda_max_good")),
        "verification": row.get("verification") if isinstance(row.get("verification"), str) else None,
        "rentable": row.get("rentable") is True,
        "rented": row.get("rented") is True,
        "is_bid": row.get("is_bid") is True,
        "reliability": _number(row.get("reliability")),
        "total_usd_per_hour": _number(row.get("dph_total")),
        "base_usd_per_hour": _number(row.get("dph_base")),
        "storage_usd_per_gb_month": _number(row.get("storage_cost")),
        "upload_usd_per_gb": _number(row.get("inet_up_cost")),
        "download_usd_per_gb": _number(row.get("inet_down_cost")),
        "location": row.get("geolocation") if isinstance(row.get("geolocation"), str) else None,
    }


class VastClient:
    """Small, fixed-origin client for the Vast.ai REST endpoints used by Studio."""

    def __init__(self, api_key: str, http_client: httpx.AsyncClient | None = None) -> None:
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("Vast.ai API key is empty")
        self._api_key = api_key.strip()
        self._http = http_client

    async def _request(self, method: str, path: str, *, json_body: dict | None = None,
                       params: dict[str, str] | None = None) -> dict[str, Any]:
        api_path = path if path.startswith('/api/') else f'/api/v0{path}'
        # Vast canonicalizes REST endpoints with a trailing slash. Redirects are
        # intentionally disabled below so credentials are never forwarded across
        # hosts; use the canonical URL directly instead of turning a 301 into a
        # misleading provider error.
        if not api_path.endswith('/'):
            api_path += '/'
        url = f"{API_BASE}{api_path}"
        headers = {"Authorization": f"Bearer {self._api_key}", "Accept": "application/json"}
        try:
            if self._http is not None:
                async with self._http.stream(method, url, headers=headers, json=json_body,
                                             params=params, timeout=API_TIMEOUT) as response:
                    content = await self._read_limited(response)
            else:
                async with httpx.AsyncClient(timeout=API_TIMEOUT, follow_redirects=False) as client:
                    async with client.stream(method, url, headers=headers, json=json_body,
                                             params=params) as response:
                        content = await self._read_limited(response)
        except httpx.HTTPError as exc:
            # Do not chain transport exceptions: their repr may include request headers.
            raise VastUnavailableError("Vast.ai could not be reached") from None

        status = response.status_code
        if status in {401, 403}:
            raise VastAuthError("Vast.ai rejected the API key or its permissions")
        if status == 429:
            raise VastRateLimitError("Vast.ai rate limit reached; retry later")
        if status in {404, 410} and "/asks/" in path:
            raise VastOfferUnavailable("This GPU offer is no longer available")
        if status >= 500:
            raise VastUnavailableError("Vast.ai is temporarily unavailable")
        if status >= 400:
            raise VastResponseError(f"Vast.ai rejected the request (HTTP {status})")
        try:
            data = json.loads(content)
        except (ValueError, json.JSONDecodeError):
            raise VastResponseError("Vast.ai returned invalid JSON") from None
        if not isinstance(data, dict):
            raise VastResponseError("Vast.ai returned an invalid response")
        if data.get("success") is False:
            if "/asks/" in path:
                raise VastOfferUnavailable("Vast.ai did not create the selected offer")
            raise VastResponseError("Vast.ai could not complete the request")
        return data

    @staticmethod
    async def _read_limited(response: httpx.Response) -> bytes:
        chunks: list[bytes] = []
        total = 0
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > MAX_RESPONSE_BYTES:
                raise VastResponseError("Vast.ai returned an oversized response")
            chunks.append(chunk)
        return b"".join(chunks)

    async def get_account(self) -> dict[str, Any]:
        data = await self._request("GET", "/users/current")
        account_id = _integer(data.get("id"))
        balance = _number(data.get("balance"))
        email = data.get("email")
        if account_id is None or balance is None or not isinstance(email, str):
            raise VastResponseError("Vast.ai returned incomplete account information")
        return {"id": account_id, "email": email, "balance": balance}

    async def search_h3_offers(self, *, max_hourly_rate: float | None = None) -> list[dict[str, Any]]:
        filters: dict[str, Any] = {
            "limit": OFFER_LIMIT,
            "type": "ondemand",
            "verified": {"eq": True},
            "rentable": {"eq": True},
            "rented": {"eq": False},
            "gpu_arch": {"eq": "nvidia"},
            "num_gpus": {"eq": 1},
            "gpu_frac": {"gte": 1},
            "gpu_ram": {"gte": MIN_GPU_RAM_MB},
            "cpu_ram": {"gte": MIN_CPU_RAM_MB},
            "disk_space": {"gte": MIN_DISK_GB},
            "cuda_max_good": {"gte": MIN_CUDA_VERSION},
            "order": [["dph_total", "asc"]],
        }
        if max_hourly_rate is not None:
            rate = _number(max_hourly_rate)
            if rate is None or rate <= 0:
                raise ValueError("Maximum hourly rate must be a positive finite number")
            filters["dph_total"] = {"lte": rate}

        data = await self._request("POST", "/bundles", json_body=filters)
        offers = data.get("offers")
        if not isinstance(offers, list):
            raise VastResponseError("Vast.ai returned an invalid offer list")
        compatible: list[dict[str, Any]] = []
        for row in offers:
            if not isinstance(row, Mapping) or not is_h3_compatible_offer(row):
                continue
            offer = normalize_offer(row)
            if max_hourly_rate is None or offer["total_usd_per_hour"] <= max_hourly_rate:
                compatible.append(offer)
        return compatible

    async def get_instances(self) -> list[dict[str, Any]]:
        instances: list[Any] = []
        cursor: str | None = None
        selected = json.dumps(["id", "actual_status", "intended_status", "label", "gpu_name",
                               "num_gpus", "gpu_ram", "cpu_ram", "disk_space", "dph_total",
                               "ssh_host", "ssh_port"], separators=(",", ":"))
        # Vast's v1 instance list is paginated (25 rows maximum). Bound pagination
        # to avoid an accidental endless loop if a provider cursor malfunctions.
        for _ in range(20):
            params = {"limit": "25", "select_cols": selected}
            if cursor:
                params["after_token"] = cursor
            data = await self._request("GET", "/api/v1/instances", params=params)
            page = data.get("instances")
            if not isinstance(page, list):
                raise VastResponseError("Vast.ai returned an invalid instance list")
            instances.extend(page)
            cursor = data.get("next_token") if isinstance(data.get("next_token"), str) else None
            if not cursor:
                break
        else:
            raise VastResponseError("Vast.ai instance list is too large to load safely")

        normalized = [self._normalize_instance(row) for row in instances if isinstance(row, Mapping)]
        return [row for row in normalized if row is not None]

    async def get_instance(self, instance_id: int) -> dict[str, Any]:
        if isinstance(instance_id, bool) or not isinstance(instance_id, int) or instance_id <= 0:
            raise ValueError("Instance ID must be a positive integer")
        data = await self._request("GET", f"/instances/{instance_id}")
        instance = data.get("instances")
        if not isinstance(instance, Mapping):
            raise VastResponseError("Vast.ai returned an invalid instance")
        normalized = self._normalize_instance(instance)
        if normalized is None:
            raise VastResponseError("Vast.ai returned an invalid instance ID")
        return normalized

    @staticmethod
    def _normalize_instance(row: Mapping[str, Any]) -> dict[str, Any] | None:
        instance_id = _integer(row.get("id"))
        if instance_id is None or instance_id <= 0:
            return None
        return {
            "id": instance_id,
            "actual_status": row.get("actual_status") if isinstance(row.get("actual_status"), str) else None,
            "intended_status": row.get("intended_status") if isinstance(row.get("intended_status"), str) else None,
            "gpu_name": row.get("gpu_name") if isinstance(row.get("gpu_name"), str) else None,
            "num_gpus": _integer(row.get("num_gpus")),
            "gpu_ram_mb": _number(row.get("gpu_ram")),
            "cpu_ram_mb": _number(row.get("cpu_ram")),
            "disk_space_gb": _number(row.get("disk_space")),
            "total_usd_per_hour": _number(row.get("dph_total")),
            "ssh_host": row.get("ssh_host") if isinstance(row.get("ssh_host"), str) else None,
            "ssh_port": _integer(row.get("ssh_port")),
            "label": row.get("label") if isinstance(row.get("label"), str) else None,
        }

    async def create_instance(self, offer_id: int, disk_gb: int = MIN_DISK_GB,
                              label: str = "H3 Studio") -> dict[str, int]:
        if isinstance(offer_id, bool) or not isinstance(offer_id, int) or offer_id <= 0:
            raise ValueError("Offer ID must be a positive integer")
        if isinstance(disk_gb, bool) or not isinstance(disk_gb, int) or disk_gb < MIN_DISK_GB:
            raise ValueError(f"Disk size must be at least {MIN_DISK_GB} GB")
        if (not isinstance(label, str) or not (label == "H3 Studio" or
                label.startswith("H3 Studio ")) or len(label) > 64):
            raise ValueError("Instance label is invalid")
        data = await self._request("PUT", f"/asks/{offer_id}", json_body={
            "image": "vastai/base-image:cuda-12.8.1-auto",
            "disk": disk_gb,
            "runtype": "ssh",
            "target_state": "running",
            "cancel_unavail": True,
            "label": label,
        })
        instance_id = _integer(data.get("new_contract"))
        if instance_id is None or instance_id <= 0:
            raise VastResponseError("Vast.ai accepted the request without returning an instance ID")
        return {"instance_id": instance_id}

    async def stop_instance(self, instance_id: int) -> None:
        if isinstance(instance_id, bool) or not isinstance(instance_id, int) or instance_id <= 0:
            raise ValueError("Instance ID must be a positive integer")
        await self._request("PUT", f"/instances/{instance_id}", json_body={"state": "stopped"})

    async def start_instance(self, instance_id: int) -> None:
        if isinstance(instance_id, bool) or not isinstance(instance_id, int) or instance_id <= 0:
            raise ValueError("Instance ID must be a positive integer")
        await self._request("PUT", f"/instances/{instance_id}", json_body={"state": "running"})

    async def destroy_instance(self, instance_id: int) -> None:
        if isinstance(instance_id, bool) or not isinstance(instance_id, int) or instance_id <= 0:
            raise ValueError("Instance ID must be a positive integer")
        await self._request("DELETE", f"/instances/{instance_id}")
