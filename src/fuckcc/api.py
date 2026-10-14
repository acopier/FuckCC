"""Client for Adobe's public software-delivery endpoints.

These are the same unauthenticated endpoints the Creative Cloud desktop app uses
to enumerate products and fetch installers. The headers below (app id, api key,
and a synthetic ``fg`` cookie) are what make the service answer; they are not a
secret and no Adobe account is required to *download* anything.
"""

import gzip
import http.client
import json
import os
import random
import socket
import ssl
import string
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from collections.abc import Callable
from typing import Any, Final

from . import __version__
from .ui import Progress, warn

type Headers = dict[str, str]

FFC_BASE: Final = "https://prod-rel-ffc-ccm.oobesaas.adobe.com/adobe-ffc-external/core"
APP_MANIFEST_URL: Final = "https://cdn-ffc.oobesaas.adobe.com/core/v3/applications"

# macOS platform identifiers understood by the product listing service.
MAC_PLATFORMS: Final = "osx10-64,osx10,macarm64,macuniversal"

ALL_LANGUAGES: Final[tuple[str, ...]] = (
    "en_US",
    "en_GB",
    "en_AE",
    "en_IL",
    "en_XM",
    "es_ES",
    "es_MX",
    "pt_BR",
    "fr_FR",
    "fr_CA",
    "fr_MA",
    "fr_XM",
    "it_IT",
    "de_DE",
    "nl_NL",
    "ru_RU",
    "uk_UA",
    "ar_AE",
    "he_IL",
    "zh_TW",
    "zh_CN",
    "ja_JP",
    "ko_KR",
    "pl_PL",
    "hu_HU",
    "cs_CZ",
    "tr_TR",
    "sv_SE",
    "nb_NO",
    "no_NO",
    "fi_FI",
    "da_DK",
    "id_ID",
    "vi_VN",
    "sl_SI",
)

_RETRYABLE: Final = (
    urllib.error.URLError,
    http.client.HTTPException,
    ConnectionError,
    socket.timeout,
    ssl.SSLError,
    EOFError,
    OSError,
)


def _rand_fg() -> str:
    token = "".join(random.choices(string.ascii_uppercase + string.digits, k=26))
    return f"fg={token}======"


class AdobeError(RuntimeError):
    """Raised for any unrecoverable Adobe API or download failure."""


class AdobeClient:
    def __init__(
        self, timeout: int = 60, retries: int = 6, verbose: bool = False
    ) -> None:
        self.timeout = timeout
        self.retries = retries
        self.verbose = verbose
        self._cookie = _rand_fg()

    # -- headers -------------------------------------------------------------
    def _headers(self, extra: Headers | None = None) -> Headers:
        headers: Headers = {
            "X-Adobe-App-Id": "accc-apps-panel-desktop",
            "User-Agent": "Adobe Application Manager 2.0",
            "X-Api-Key": "CC_HD_ESD_1_0",
            "Accept": "*/*",
            "Accept-Encoding": "gzip",
            "Cookie": self._cookie,
            "X-fuckcc-Version": __version__,
        }
        if extra:
            headers.update(extra)
        return headers

    def _open(
        self,
        url: str,
        extra_headers: Headers | None = None,
        method: str = "GET",
        timeout: float | None = None,
    ) -> http.client.HTTPResponse:
        req = urllib.request.Request(
            url, headers=self._headers(extra_headers), method=method
        )
        return urllib.request.urlopen(req, timeout=timeout or self.timeout)

    @staticmethod
    def _maybe_decompress(resp: http.client.HTTPResponse) -> bytes:
        data = resp.read()
        enc = (resp.headers.get("Content-Encoding") or "").lower()
        if "gzip" in enc:
            return gzip.decompress(data)
        if "deflate" in enc:
            try:
                return zlib.decompress(data)
            except zlib.error:
                return zlib.decompress(data, -zlib.MAX_WBITS)
        return data

    def get_bytes(
        self,
        url: str,
        extra_headers: Headers | None = None,
        method: str = "GET",
        retries: int | None = None,
        timeout: int | None = None,
    ) -> bytes:
        """GET a URL, retrying transient failures. Returns the response body."""
        attempts = self.retries if retries is None else retries
        last_exc: BaseException | None = None
        for i in range(attempts):
            try:
                with self._open(
                    url, extra_headers, method=method, timeout=timeout
                ) as resp:
                    return self._maybe_decompress(resp)
            except urllib.error.HTTPError as exc:
                # 4xx other than 429 are not worth retrying.
                if exc.code < 500 and exc.code != 429:
                    body = exc.read()[:200]
                    raise AdobeError(
                        f"HTTP {exc.code} for {url}: {body.decode('utf-8', 'replace')}"
                    ) from exc
                last_exc = exc
            except _RETRYABLE as exc:
                last_exc = exc
            if i < attempts - 1:
                delay = min(2**i, 15) + random.random()
                if self.verbose:
                    warn(f"retrying {url} in {delay:.1f}s ({last_exc})")
                time.sleep(delay)
        raise AdobeError(f"failed to fetch {url} after {attempts} attempts: {last_exc}")

    # -- high level endpoints ------------------------------------------------
    def products_xml(self, url_version: int = 6) -> bytes:
        params = urllib.parse.urlencode(
            [
                ("_type", "xml"),
                ("channel", "ccm"),
                ("channel", "sti"),
                ("platform", MAC_PLATFORMS),
                ("productType", "Desktop"),
            ]
        )
        url = f"{FFC_BASE}/v{url_version}/products/all?{params}"
        return self.get_bytes(url, retries=self.retries)

    def application_manifest(self, build_guid: str) -> dict[str, Any]:
        data = self.get_bytes(
            APP_MANIFEST_URL, extra_headers={"x-adobe-build-guid": build_guid}
        )
        try:
            return json.loads(data)
        except ValueError as exc:
            raise AdobeError(
                f"invalid application manifest for {build_guid}: {exc}"
            ) from exc

    def head_size(self, url: str) -> int | None:
        try:
            with self._open(url, method="HEAD") as resp:
                cl = resp.headers.get("Content-Length")
                return int(cl) if cl else None
        except Exception:
            return None

    def wam_installer(
        self, sap_code: str, product_name: str, os_name: str = "mac"
    ) -> str:
        """Ask Adobe's WAM service for a product's *official* installer download.

        For ``KCCC`` this returns the Creative Cloud installer; for other SAP
        codes it returns ``<SAP>_Installer.dmg``. Useful for seeding the Setup
        engine without hand-holding.
        """
        q = urllib.parse.urlencode(
            {"sapCode": sap_code, "productName": product_name, "os": os_name}
        )
        return f"{FFC_BASE}/v1/wam/download?{q}"

    # -- file download -------------------------------------------------------
    def download(
        self,
        url: str,
        dest: str,
        expected_size: int | None = None,
        progress_label: str | None = None,
        checksum: Callable[[str], bool] | None = None,
    ) -> str:
        """Download ``url`` to ``dest`` with resume support.

        Writes to ``dest + '.part'`` and atomically renames on success. If a
        ``.part`` file exists it is resumed with an HTTP Range request.
        """
        os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
        part = f"{dest}.part"

        if (
            os.path.exists(dest)
            and expected_size
            and os.path.getsize(dest) == expected_size
        ) and (checksum is None or checksum(dest)):
            return dest

        for attempt in range(self.retries):
            offset = os.path.getsize(part) if os.path.exists(part) else 0
            if expected_size and offset > expected_size:
                os.remove(part)
                offset = 0

            headers: Headers = {}
            if offset:
                headers["Range"] = f"bytes={offset}-"
            # Downloads come from the CDN and only need the generic agent.
            headers["User-Agent"] = "Creative Cloud"

            bar = Progress(
                progress_label or os.path.basename(dest), total=expected_size or 0
            )
            bar.done = offset
            try:
                with self._open(url, headers) as resp:
                    if offset and resp.status == 200:
                        # Server ignored Range; start over.
                        offset = 0
                        bar.done = 0
                    total = expected_size
                    if not total:
                        cl = resp.headers.get("Content-Length")
                        if cl:
                            total = int(cl) + offset
                            bar.total = total
                    mode = "ab" if offset else "wb"
                    with open(part, mode) as fh:
                        while True:
                            chunk = resp.read(1 << 20)
                            if not chunk:
                                break
                            fh.write(chunk)
                            bar.update(len(chunk))
                bar.close()
            except _RETRYABLE as exc:
                bar.close()
                if attempt < self.retries - 1:
                    if self.verbose:
                        warn(
                            f"download interrupted ({exc}); resuming {os.path.basename(dest)}"
                        )
                    time.sleep(min(2**attempt, 15))
                    continue
                raise AdobeError(f"download failed for {url}: {exc}") from exc

            size = os.path.getsize(part)
            if expected_size and size != expected_size:
                if attempt < self.retries - 1:
                    continue
                raise AdobeError(
                    f"size mismatch for {url}: got {size}, expected {expected_size}"
                )
            os.replace(part, dest)
            if checksum is not None and not checksum(dest):
                os.remove(dest)
                raise AdobeError(f"checksum failed for {url}")
            return dest

        raise AdobeError(f"download failed for {url}")
