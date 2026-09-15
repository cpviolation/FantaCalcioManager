import configparser
import zipfile
from dataclasses import dataclass
from pathlib import Path


@dataclass
class InetConfig:
    server: str
    folder: str
    request_file: str
    list_file: str
    list_file_md5: str
    dispo_file_template: str
    download_server: str
    download_folder: str
    use_md5: bool


def extract_archive(zip_path: str | Path, extract_to: str | Path | None = None) -> Path:
    """Extract a FantaCalcio zip archive and return the extraction directory."""
    zip_path = Path(zip_path)
    if extract_to is None:
        extract_to = zip_path.parent / zip_path.stem
    extract_to = Path(extract_to)
    extract_to.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(extract_to)
    return extract_to


def find_fca_file(directory: str | Path) -> Path:
    """Return the first .fca file found in *directory* (non-recursive)."""
    directory = Path(directory)
    matches = list(directory.glob("*.fca"))
    if not matches:
        raise FileNotFoundError(f"No .fca file found in {directory}")
    return matches[0]


def parse_inet_ini(ini_path: str | Path) -> InetConfig:
    """Parse an inet.ini file and return an InetConfig."""
    ini_path = Path(ini_path)
    cfg = configparser.ConfigParser()
    # inet.ini uses Italian key names; read as-is (case-sensitive keys)
    cfg.optionxform = str  # type: ignore[assignment]
    cfg.read(ini_path, encoding="utf-8")

    dl = cfg["Download"]
    return InetConfig(
        server=dl.get("Server", ""),
        folder=dl.get("Folder", ""),
        request_file=dl.get("Requestfile", ""),
        list_file=dl.get("Listfile", ""),
        list_file_md5=dl.get("ListfileMD5", ""),
        dispo_file_template=dl.get("Dispofile", ""),
        download_server=dl.get("ServerDownload", ""),
        download_folder=dl.get("FolderDownload", ""),
        use_md5=dl.get("MD5", "False").strip().lower() in ("true", "vero", "1"),
    )
