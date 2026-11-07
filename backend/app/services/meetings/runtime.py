"""Owner-only operator configuration for the normal API and worker, without editing or distributing .env files."""
import os,stat
from collections.abc import Mapping
from pathlib import Path
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field,SecretStr


class Runtime(BaseModel):
    model_config=ConfigDict(extra='forbid',frozen=True,hide_input_in_errors=True)
    database_url: SecretStr=Field(repr=False)
    zoom_directory: Path
    checkpoint_signing_key: SecretStr=Field(min_length=43,repr=False)
    chat_transport: Literal['api','codex','claude']


def require_private_file(path: Path) -> None:
    info=path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode&0o077:
        raise PermissionError('Meeting configuration and OAuth files must be regular owner-only files (chmod 600): '+str(path))


def read(path: Path) -> Runtime:
    require_private_file(path);configuration=Runtime.model_validate_json(path.read_text())
    info=configuration.zoom_directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode&0o077:
        raise PermissionError('The configured Zoom OAuth directory must be owner-only (chmod 700)')
    for name in ('zoom-tokens.json','zoom-public-client.json'):require_private_file(configuration.zoom_directory/name)
    return configuration


def api_environment(configuration: Runtime,base: Mapping[str,str]) -> dict[str,str]:
    inherited={key:value for key,value in base.items() if key not in {'MEETINGS_DATABASE_URL','MEETINGS_CHECKPOINT_SIGNING_KEY'}}
    return {**inherited,'MEETINGS_ENABLED':'true','ZOOM_CAPTURE_ENABLED':'true','ZOOM_OAUTH_DIRECTORY':str(configuration.zoom_directory),
        'RUN_WORKER_IN_API':'false','RUN_MEETING_WORKER_IN_API':'false','CHAT_TRANSPORT':configuration.chat_transport}


def worker_environment(configuration: Runtime,base: Mapping[str,str]) -> dict[str,str]:
    return {**api_environment(configuration,base),'MEETINGS_DATABASE_URL':configuration.database_url.get_secret_value(),
        'MEETINGS_CHECKPOINT_SIGNING_KEY':configuration.checkpoint_signing_key.get_secret_value()}
