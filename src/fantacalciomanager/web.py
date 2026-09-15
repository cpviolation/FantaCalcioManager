"""HTTP download utilities for FantaCalcio Manager archives."""
import hashlib
from pathlib import Path

import requests

from fantacalciomanager.archive import InetConfig


_ARCHIVE_URL_TEMPLATE = "https://{server}/fcm_extra/ArchivioA{year}SerieA.zip"


def download_file(url: str, local_path: str | Path, show_progress: bool = False) -> Path:
    """Download *url* to *local_path*, returning the path on success."""
    local_path = Path(local_path)
    local_path.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, timeout=30) as resp:
        resp.raise_for_status()
        total = int(resp.headers.get("content-length", 0))
        downloaded = 0
        with open(local_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=65536):
                f.write(chunk)
                if show_progress and total:
                    downloaded += len(chunk)
                    pct = downloaded * 100 // total
                    print(f"\r  {pct:3d}%  {downloaded}/{total} bytes", end="", flush=True)
    if show_progress and total:
        print()
    return local_path


def _md5(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def download_season_archive(
    year: int,
    data_dir: str | Path = "data",
    *,
    server: str = "legafantacalciosanremo.it",
    force: bool = False,
    show_progress: bool = True,
) -> Path:
    """Download the season zip for *year* if not already present.

    Returns the local path to the downloaded (or existing) zip file.
    """
    data_dir = Path(data_dir)
    zip_name = f"ArchivioA{year}SerieA.zip"
    local_zip = data_dir / zip_name
    if local_zip.exists() and not force:
        return local_zip
    url = _ARCHIVE_URL_TEMPLATE.format(server=server, year=year)
    if show_progress:
        print(f"Downloading {url} ...")
    download_file(url, local_zip, show_progress=show_progress)
    return local_zip


def fetch_file_list(config: InetConfig) -> list[str]:
    """Fetch the list of available files from the update server."""
    url = f"https://{config.server}{config.folder}/{config.list_file}"
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    return [line.strip() for line in resp.text.splitlines() if line.strip()]


def download_update_file(
    config: InetConfig,
    filename: str,
    dest_dir: str | Path,
    *,
    verify_md5: bool = True,
    show_progress: bool = False,
) -> Path:
    """Download a single update file from the FCM update server.

    If *verify_md5* is True and the server provides an MD5 list, the checksum
    is verified after download (requires config.use_md5 to be True).
    """
    dest_dir = Path(dest_dir)
    url = f"https://{config.download_server}{config.download_folder}/{filename}"
    local_path = dest_dir / filename
    download_file(url, local_path, show_progress=show_progress)

    if verify_md5 and config.use_md5:
        md5_url = f"https://{config.server}{config.folder}/{config.list_file_md5}"
        resp = requests.get(md5_url, timeout=15)
        resp.raise_for_status()
        expected: str | None = None
        for line in resp.text.splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1] == filename:
                expected = parts[0]
                break
        if expected and _md5(local_path) != expected:
            local_path.unlink(missing_ok=True)
            raise ValueError(f"MD5 mismatch for {filename}: expected {expected}")

    return local_path
