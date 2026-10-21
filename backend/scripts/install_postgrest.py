"""Install the pinned official PostgREST test binary into the active project venv.

From backend/: ../.venv/bin/python -m scripts.install_postgrest
Versions and upstream SHA-256 digests live in pyproject.toml. No global installation.
"""
import hashlib
import logging
import platform
import sys
import tarfile
import tempfile
import time
import tomllib
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen
from pydantic import AliasPath, BaseModel, ConfigDict, Field

logger=logging.getLogger(__name__)


class Artifact(BaseModel):
    model_config=ConfigDict(extra='ignore')
    file: str = Field(pattern=r'^postgrest-v[0-9.]+-[a-z0-9-]+\.tar\.xz$')
    sha256: str = Field(pattern=r'^[a-f0-9]{64}$')


class ToolConfig(BaseModel):
    model_config=ConfigDict(extra='ignore')
    version: str = Field(pattern=r'^[0-9]+\.[0-9]+$')
    artifacts: dict[str,Artifact]


class Manifest(BaseModel):
    model_config=ConfigDict(extra='ignore')
    postgrest: ToolConfig = Field(validation_alias=AliasPath('tool','demobank','postgrest'))


def config() -> ToolConfig:
    with (Path(__file__).resolve().parents[1]/'pyproject.toml').open('rb') as source:
        return Manifest.model_validate(tomllib.load(source)).postgrest


def binary_path() -> Path:
    if sys.prefix==sys.base_prefix:raise RuntimeError('Use the project .venv Python to install or run native Data API tests')
    return Path(sys.prefix)/'tools'/'postgrest'/config().version/'postgrest'


def download(url: str, target: Path) -> None:
    for attempt in range(3):
        try:
            with urlopen(url,timeout=30) as response, target.open('wb') as output:
                while block:=response.read(65536):output.write(block)
            return
        except (URLError,TimeoutError) as error:
            if isinstance(error,HTTPError) and error.code not in {408,429,500,502,503,504}:raise
            if attempt==2:raise
            logger.warning('postgrest_download_retry',extra={'attempt':attempt+1,'error_type':type(error).__name__})
            time.sleep(0.5*2**attempt)


def install() -> Path:
    tool=config()
    target=binary_path()
    key=f'{platform.system()}-{platform.machine()}'
    artifact=tool.artifacts.get(key)
    if artifact is None:raise RuntimeError(f'Native PostgREST tests do not have a pinned binary for {key}; supported: {sorted(tool.artifacts)}')
    url=f'https://github.com/PostgREST/postgrest/releases/download/v{tool.version}/{artifact.file}'
    with tempfile.TemporaryDirectory(prefix='demobank-postgrest-') as folder:
        archive_path=Path(folder)/artifact.file
        download(url,archive_path)
        with archive_path.open('rb') as downloaded:
            actual=hashlib.file_digest(downloaded,'sha256').hexdigest()
        if actual!=artifact.sha256:raise ValueError(f'PostgREST archive checksum mismatch: expected {artifact.sha256}, received {actual}')
        with tarfile.open(archive_path,'r:xz') as archive:
            member=archive.getmember('postgrest')
            if not member.isfile():raise ValueError('PostgREST archive does not contain the expected regular executable')
            source=archive.extractfile(member)
            if source is None:raise ValueError('PostgREST executable could not be read from the verified archive')
            with source:
                executable=source.read()
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(executable)
        target.chmod(0o700)
    return target


if __name__=='__main__':
    logging.basicConfig(level=logging.INFO)
    print(install())
