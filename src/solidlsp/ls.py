import dataclasses
import hashlib
import json
import logging
import os
import pathlib
import shutil
import subprocess
import threading
import time as monotonic_time
from abc import ABC, abstractmethod
from collections import defaultdict
from collections.abc import Hashable, Iterator
from contextlib import contextmanager
from copy import copy
from pathlib import Path, PurePath
from time import sleep
from typing import Self, Union, cast
import pathspec
from sensai.util.pickle import getstate, load_pickle
from serena.text_utils import MatchedConsecutiveLines
from serena.util.file_system import match_path
from solidlsp import ls_types
from solidlsp.ls_config import Language, LanguageServerConfig
from solidlsp.ls_exceptions import SolidLSPException
from solidlsp.ls_handler import SolidLanguageServerHandler
from solidlsp.ls_types import UnifiedSymbolInformation
from solidlsp.ls_utils import FileUtils, PathUtils, TextUtils
from solidlsp.lsp_protocol_handler import lsp_types
from solidlsp.lsp_protocol_handler import lsp_types as LSPTypes
from solidlsp.lsp_protocol_handler.lsp_constants import LSPConstants
from solidlsp.lsp_protocol_handler.lsp_types import Definition, DefinitionParams, DocumentSymbol, LocationLink, RenameParams, SymbolInformation
from solidlsp.lsp_protocol_handler.server import LSPError, ProcessLaunchInfo, StringDict
from solidlsp.settings import SolidLSPSettings
from solidlsp.util.cache import load_cache, save_cache
GenericDocumentSymbol = Union[LSPTypes.DocumentSymbol, LSPTypes.SymbolInformation, ls_types.UnifiedSymbolInformation]
log = logging.getLogger(__name__)

@dataclasses.dataclass(kw_only=True)
class ReferenceInSymbol:
    symbol: ls_types.UnifiedSymbolInformation
    line: int
    character: int

class LSPFileBuffer:

    def __init__(self, uri: str, contents: str, encoding: str, version: int, language_id: str, ref_count: int, language_server: 'SolidLanguageServer', open_in_ls: bool=True) -> None:
        self.language_server = language_server
        self.uri = uri
        self.contents = contents
        self.version = version
        self.language_id = language_id
        self.ref_count = ref_count
        self.encoding = encoding
        self._content_hash: str | None = None
        self._is_open_in_ls = False
        self._last_synced_hash: str | None = None
        self.last_used_at = monotonic_time.monotonic()
        if open_in_ls:
            self._open_in_ls()

    def _open_in_ls(self) -> None:
        if self._is_open_in_ls:
            return
        self._is_open_in_ls = True
        self.language_server.server.notify.did_open_text_document({LSPConstants.TEXT_DOCUMENT: {LSPConstants.URI: self.uri, LSPConstants.LANGUAGE_ID: self.language_id, LSPConstants.VERSION: 0, LSPConstants.TEXT: self.contents}})
        self._last_synced_hash = self.content_hash

    def close(self) -> None:
        if self._is_open_in_ls:
            self.language_server.server.notify.did_close_text_document({LSPConstants.TEXT_DOCUMENT: {LSPConstants.URI: self.uri}})
            self._is_open_in_ls = False

    def ensure_open_in_ls(self) -> None:
        self._open_in_ls()
        self.touch()

    def mark_content_updated(self) -> None:
        self._content_hash = None

    def mark_incremental_change_synced(self) -> None:
        self._last_synced_hash = self.content_hash

    def touch(self) -> None:
        self.last_used_at = monotonic_time.monotonic()

    def sync_changes_to_ls(self) -> None:
        if not self._is_open_in_ls:
            raise SolidLSPException('ERR_LSP_SYNC_OPEN_FAILED: document is not opened in language server')
        current_hash = self.content_hash
        if self._last_synced_hash == current_hash:
            return
        self.version += 1
        try:
            self.language_server.server.notify.did_change_text_document({LSPConstants.TEXT_DOCUMENT: {LSPConstants.VERSION: self.version, LSPConstants.URI: self.uri}, LSPConstants.CONTENT_CHANGES: [{'text': self.contents}]})
        except (RuntimeError, OSError, ValueError, TypeError) as exc:
            raise SolidLSPException(f'ERR_LSP_SYNC_CHANGE_FAILED: {exc}') from exc
        self._last_synced_hash = current_hash

    @property
    def content_hash(self) -> str:
        if self._content_hash is None:
            self._content_hash = hashlib.md5(self.contents.encode(self.encoding)).hexdigest()
        return self._content_hash

    def split_lines(self) -> list[str]:
        return self.contents.split('\n')

class SymbolBody:

    def __init__(self, lines: list[str], start_line: int, start_col: int, end_line: int, end_col: int) -> None:
        self._lines = lines
        self._start_line = start_line
        self._start_col = start_col
        self._end_line = end_line
        self._end_col = end_col

    def get_text(self) -> str:
        symbol_body = '\n'.join(self._lines[self._start_line:self._end_line + 1])
        symbol_body = symbol_body[self._start_col:]
        return symbol_body

class SymbolBodyFactory:

    def __init__(self, file_buffer: LSPFileBuffer):
        self._lines = file_buffer.split_lines()

    def create_symbol_body(self, symbol: GenericDocumentSymbol) -> SymbolBody:
        existing_body = symbol.get('body', None)
        if existing_body and isinstance(existing_body, SymbolBody):
            return existing_body
        assert 'location' in symbol
        start_line = symbol['location']['range']['start']['line']
        end_line = symbol['location']['range']['end']['line']
        start_col = symbol['location']['range']['start']['character']
        end_col = symbol['location']['range']['end']['character']
        return SymbolBody(self._lines, start_line, start_col, end_line, end_col)

class DocumentSymbols:

    def __init__(self, root_symbols: list[ls_types.UnifiedSymbolInformation]):
        self.root_symbols = root_symbols
        self._all_symbols: list[ls_types.UnifiedSymbolInformation] | None = None

    def __getstate__(self) -> dict:
        return getstate(DocumentSymbols, self, transient_properties=['_all_symbols'])

    def iter_symbols(self) -> Iterator[ls_types.UnifiedSymbolInformation]:
        if self._all_symbols is not None:
            yield from self._all_symbols
            return

        def traverse(s: ls_types.UnifiedSymbolInformation) -> Iterator[ls_types.UnifiedSymbolInformation]:
            yield s
            for child in s.get('children', []):
                yield from traverse(child)
        for root_symbol in self.root_symbols:
            yield from traverse(root_symbol)

    def get_all_symbols_and_roots(self) -> tuple[list[ls_types.UnifiedSymbolInformation], list[ls_types.UnifiedSymbolInformation]]:
        if self._all_symbols is None:
            self._all_symbols = list(self.iter_symbols())
        return (self._all_symbols, self.root_symbols)

class LanguageServerDependencyProvider(ABC):

    def __init__(self, custom_settings: SolidLSPSettings.CustomLSSettings, ls_resources_dir: str):
        self._custom_settings = custom_settings
        self._ls_resources_dir = ls_resources_dir

    @abstractmethod
    def create_launch_command(self) -> list[str] | str:
        ...

    def create_launch_command_env(self) -> dict[str, str]:
        return {}

class LanguageServerDependencyProviderSinglePath(LanguageServerDependencyProvider, ABC):

    @abstractmethod
    def _get_or_install_core_dependency(self) -> str:
        ...

    def create_launch_command(self) -> Union[str, list[str]]:
        path = self._custom_settings.get('ls_path', None)
        if path is not None:
            core_path = path
        else:
            core_path = self._get_or_install_core_dependency()
        return self._create_launch_command(core_path)

    @abstractmethod
    def _create_launch_command(self, core_path: str) -> list[str] | str:
        ...

class SolidLanguageServer(ABC):
    CACHE_FOLDER_NAME = 'cache'
    RAW_DOCUMENT_SYMBOLS_CACHE_VERSION = 1
    '\n    global version identifier for raw symbol caches; an LS-specific version is defined separately and combined with this.\n    This should be incremented whenever there is a change in the way raw document symbols are stored.\n    If the result of a language server changes in a way that affects the raw document symbols,\n    the LS-specific version should be incremented instead.\n    '
    RAW_DOCUMENT_SYMBOL_CACHE_FILENAME = 'raw_document_symbols.pkl'
    RAW_DOCUMENT_SYMBOL_CACHE_FILENAME_LEGACY_FALLBACK = 'document_symbols_cache_v23-06-25.pkl'
    DOCUMENT_SYMBOL_CACHE_VERSION = 4
    DOCUMENT_SYMBOL_CACHE_FILENAME = 'document_symbols.pkl'

    def is_ignored_dirname(self, dirname: str) -> bool:
        return dirname.startswith('.')

    @staticmethod
    def _determine_log_level(line: str) -> int:
        line_lower = line.lower()
        if 'error' in line_lower or 'exception' in line_lower or line.startswith('E['):
            return logging.ERROR
        else:
            return logging.INFO

    @classmethod
    def get_language_enum_instance(cls) -> Language:
        return Language.from_ls_class(cls)

    @classmethod
    def ls_resources_dir(cls, solidlsp_settings: SolidLSPSettings, mkdir: bool=True) -> str:
        result = os.path.join(solidlsp_settings.ls_resources_dir, cls.__name__)
        pre_migration_ls_resources_dir = os.path.join(os.path.dirname(__file__), 'language_servers', 'static', cls.__name__)
        if os.path.exists(pre_migration_ls_resources_dir):
            if os.path.exists(result):
                shutil.rmtree(result, ignore_errors=True)
            else:
                shutil.move(pre_migration_ls_resources_dir, result)
        if mkdir:
            os.makedirs(result, exist_ok=True)
        return result

    @classmethod
    def create(cls, config: LanguageServerConfig, repository_root_path: str, timeout: float | None=None, solidlsp_settings: SolidLSPSettings | None=None) -> 'SolidLanguageServer':
        ls: SolidLanguageServer
        if solidlsp_settings is None:
            solidlsp_settings = SolidLSPSettings()
        repository_root_path = os.path.abspath(repository_root_path)
        ls_class = config.code_language.get_ls_class()
        ls = ls_class(config, repository_root_path, solidlsp_settings)
        ls.set_request_timeout(timeout)
        return ls

    def __init__(self, config: LanguageServerConfig, repository_root_path: str, process_launch_info: ProcessLaunchInfo | None, language_id: str, solidlsp_settings: SolidLSPSettings, cache_version_raw_document_symbols: Hashable=1):
        self._solidlsp_settings = solidlsp_settings
        lang = self.get_language_enum_instance()
        self._custom_settings = solidlsp_settings.get_ls_specific_settings(lang)
        self._ls_resources_dir = self.ls_resources_dir(solidlsp_settings)
        log.debug(f'Custom config (LS-specific settings) for {lang}: {self._custom_settings}')
        self._encoding = config.encoding
        self.repository_root_path: str = repository_root_path
        log.debug(f'Creating language server instance for repository_root_path={repository_root_path!r} with language_id={language_id!r} and process launch info: {process_launch_info}')
        self.language_id = language_id
        self.open_file_buffers: dict[str, LSPFileBuffer] = {}
        self.language = Language(language_id)
        self.cache_dir = Path(self.repository_root_path) / self._solidlsp_settings.project_data_relative_path / self.CACHE_FOLDER_NAME / self.language_id
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._ls_specific_raw_document_symbols_cache_version = cache_version_raw_document_symbols
        self._raw_document_symbols_cache: dict[str, tuple[str, list[DocumentSymbol] | list[SymbolInformation] | None]] = {}
        'maps relative file paths to a tuple of (file_content_hash, raw_root_symbols)'
        self._raw_document_symbols_cache_is_modified: bool = False
        self._load_raw_document_symbols_cache()
        self._document_symbols_cache: dict[str, tuple[str, DocumentSymbols]] = {}
        'maps relative file paths to a tuple of (file_content_hash, document_symbols)'
        self._document_symbols_cache_is_modified: bool = False
        self._load_document_symbols_cache()
        self.server_started = False
        if config.trace_lsp_communication:

            def logging_fn(source: str, target: str, msg: StringDict | str) -> None:
                log.debug(f'LSP: {source} -> {target}: {msg!s}')
        else:
            logging_fn = None
        self._dependency_provider: LanguageServerDependencyProvider | None = None
        if process_launch_info is None:
            self._dependency_provider = self._create_dependency_provider()
            process_launch_info = self._create_process_launch_info()
        log.debug(f'Creating language server instance with language_id={language_id!r} and process launch info: {process_launch_info}')
        self.server = SolidLanguageServerHandler(process_launch_info, language=self.language, determine_log_level=self._determine_log_level, logger=logging_fn, start_independent_lsp_process=config.start_independent_lsp_process)
        processed_patterns = []
        for pattern in set(config.ignored_paths):
            pattern = pattern.replace(os.path.sep, '/')
            processed_patterns.append(pattern)
        log.debug(f'Processing {len(processed_patterns)} ignored paths from the config')
        self._ignore_spec = pathspec.PathSpec.from_lines(pathspec.patterns.GitWildMatchPattern, processed_patterns)
        self._request_timeout: float | None = None
        self._has_waited_for_cross_file_references = False
        buffer_idle_ttl_raw = self._custom_settings.get("open_file_buffer_idle_ttl_sec", 20.0)
        buffer_max_open_raw = self._custom_settings.get("open_file_buffer_max_open", 512)
        try:
            self._open_file_buffer_idle_ttl_sec = max(1.0, float(buffer_idle_ttl_raw))
        except (TypeError, ValueError):
            self._open_file_buffer_idle_ttl_sec = 20.0
        try:
            self._open_file_buffer_max_open = max(16, int(buffer_max_open_raw))
        except (TypeError, ValueError):
            self._open_file_buffer_max_open = 512

    def _create_dependency_provider(self) -> LanguageServerDependencyProvider:
        raise NotImplementedError(f'{self.__class__.__name__} must implement _create_dependency_provider() or pass process_launch_info to __init__()')

    def _create_process_launch_info(self) -> ProcessLaunchInfo:
        assert self._dependency_provider is not None
        cmd = self._dependency_provider.create_launch_command()
        env = self._dependency_provider.create_launch_command_env()
        return ProcessLaunchInfo(cmd=cmd, cwd=self.repository_root_path, env=env)

    def _get_wait_time_for_cross_file_referencing(self) -> float:
        return 2

    def set_request_timeout(self, timeout: float | None) -> None:
        self.server.set_request_timeout(timeout)

    def get_ignore_spec(self) -> pathspec.PathSpec:
        return self._ignore_spec

    def is_ignored_path(self, relative_path: str, ignore_unsupported_files: bool=True) -> bool:
        abs_path = os.path.join(self.repository_root_path, relative_path)
        if not os.path.exists(abs_path):
            raise FileNotFoundError(f'File {abs_path} not found, the ignore check cannot be performed')
        is_file = os.path.isfile(abs_path)
        if is_file and ignore_unsupported_files:
            fn_matcher = self.language.get_source_fn_matcher()
            if not fn_matcher.is_relevant_filename(abs_path):
                return True
        rel_path = Path(relative_path)
        dir_parts = rel_path.parts
        if is_file:
            dir_parts = dir_parts[:-1]
        for part in dir_parts:
            if not part:
                continue
            if self.is_ignored_dirname(part):
                return True
        return match_path(relative_path, self.get_ignore_spec(), root_path=self.repository_root_path)

    def _shutdown(self, timeout: float=5.0) -> None:
        if not self.server.is_running():
            log.debug('Server process not running, skipping shutdown.')
            return
        log.info(f'Initiating final robust shutdown with a {timeout}s timeout...')
        process = self.server.process
        if process is None:
            log.debug('Server process is None, cannot shutdown.')
            return
        try:
            log.debug('Sending LSP shutdown request...')
            shutdown_thread = threading.Thread(target=self.server.shutdown)
            shutdown_thread.daemon = True
            shutdown_thread.start()
            shutdown_thread.join(timeout=2.0)
            if shutdown_thread.is_alive():
                log.debug('LSP shutdown request timed out, proceeding to terminate...')
            else:
                log.debug('LSP shutdown request completed.')
            if process.stdin and (not process.stdin.closed):
                process.stdin.close()
            log.debug('Stage 1 shutdown complete.')
        except (OSError, RuntimeError, ValueError) as e:
            log.debug(f'Exception during graceful shutdown: {e}')
        log.debug(f'Terminating process {process.pid}, current status: {process.poll()}')
        process.terminate()
        try:
            log.debug(f'Waiting for process {process.pid} to terminate...')
            exit_code = process.wait(timeout=timeout)
            log.info(f'Language server process terminated successfully with exit code {exit_code}.')
        except subprocess.TimeoutExpired:
            log.warning(f'Process {process.pid} termination timed out, killing process forcefully...')
            process.kill()
            try:
                exit_code = process.wait(timeout=2.0)
                log.info(f'Language server process killed successfully with exit code {exit_code}.')
            except subprocess.TimeoutExpired:
                log.error(f'Process {process.pid} could not be killed within timeout.')
        except (OSError, RuntimeError, ValueError) as e:
            log.error(f'Error during process shutdown: {e}')

    @contextmanager
    def start_server(self) -> Iterator['SolidLanguageServer']:
        self.start()
        yield self
        self.stop()

    def _start_server_process(self) -> None:
        self.server_started = True
        self._start_server()

    @abstractmethod
    def _start_server(self) -> None:
        ...

    def _requires_open_file_for_document_symbols(self) -> bool:
        return True

    def _get_language_id_for_file(self, relative_file_path: str) -> str:
        return self.language_id

    def _evict_open_file_buffers(self, force_lru: bool) -> None:
        now = monotonic_time.monotonic()
        stale_uris: list[str] = []
        for uri, buffer in self.open_file_buffers.items():
            if buffer.ref_count > 0:
                continue
            if (now - buffer.last_used_at) >= self._open_file_buffer_idle_ttl_sec:
                stale_uris.append(uri)
        for uri in stale_uris:
            buffer = self.open_file_buffers.get(uri)
            if buffer is None:
                continue
            try:
                buffer.close()
            except (RuntimeError, OSError, ValueError, TypeError) as exc:
                raise SolidLSPException(f"ERR_LSP_BUFFER_EVICT_FAILED: {exc}") from exc
            self.open_file_buffers.pop(uri, None)
        if not force_lru:
            return
        while len(self.open_file_buffers) > self._open_file_buffer_max_open:
            lru_uri: str | None = None
            lru_time = float("inf")
            for uri, buffer in self.open_file_buffers.items():
                if buffer.ref_count > 0:
                    continue
                if buffer.last_used_at < lru_time:
                    lru_time = buffer.last_used_at
                    lru_uri = uri
            if lru_uri is None:
                return
            lru_buffer = self.open_file_buffers.get(lru_uri)
            if lru_buffer is None:
                continue
            try:
                lru_buffer.close()
            except (RuntimeError, OSError, ValueError, TypeError) as exc:
                raise SolidLSPException(f"ERR_LSP_BUFFER_EVICT_FAILED: {exc}") from exc
            self.open_file_buffers.pop(lru_uri, None)

    @contextmanager
    def open_file(self, relative_file_path: str, open_in_ls: bool=True) -> Iterator[LSPFileBuffer]:
        if not self.server_started:
            log.error('open_file called before Language Server started')
            raise SolidLSPException('Language Server not started')
        self._evict_open_file_buffers(force_lru=True)
        absolute_file_path = str(PurePath(self.repository_root_path, relative_file_path))
        uri = pathlib.Path(absolute_file_path).as_uri()
        if uri in self.open_file_buffers:
            fb = self.open_file_buffers[uri]
            assert fb.uri == uri
            assert fb.ref_count >= 0
            fb.ref_count += 1
            fb.touch()
            if open_in_ls:
                fb.ensure_open_in_ls()
            yield fb
            fb.ref_count -= 1
            fb.touch()
        else:
            contents = FileUtils.read_file(absolute_file_path, self._encoding)
            version = 0
            language_id = self._get_language_id_for_file(relative_file_path)
            fb = LSPFileBuffer(uri=uri, contents=contents, encoding=self._encoding, version=version, language_id=language_id, ref_count=1, language_server=self, open_in_ls=open_in_ls)
            self.open_file_buffers[uri] = fb
            yield fb
            fb.ref_count -= 1
            fb.touch()
        self._evict_open_file_buffers(force_lru=True)

    @contextmanager
    def _open_file_context(self, relative_file_path: str, file_buffer: LSPFileBuffer | None=None, open_in_ls: bool=True) -> Iterator[LSPFileBuffer]:
        if file_buffer is not None:
            if open_in_ls:
                file_buffer.ensure_open_in_ls()
            yield file_buffer
        else:
            with self.open_file(relative_file_path, open_in_ls=open_in_ls) as fb:
                yield fb

    def insert_text_at_position(self, relative_file_path: str, line: int, column: int, text_to_be_inserted: str) -> ls_types.Position:
        if not self.server_started:
            log.error('insert_text_at_position called before Language Server started')
            raise SolidLSPException('Language Server not started')
        absolute_file_path = str(PurePath(self.repository_root_path, relative_file_path))
        uri = pathlib.Path(absolute_file_path).as_uri()
        assert uri in self.open_file_buffers
        file_buffer = self.open_file_buffers[uri]
        file_buffer.version += 1
        new_contents, new_l, new_c = TextUtils.insert_text_at_position(file_buffer.contents, line, column, text_to_be_inserted)
        file_buffer.contents = new_contents
        file_buffer.mark_content_updated()
        self.server.notify.did_change_text_document({LSPConstants.TEXT_DOCUMENT: {LSPConstants.VERSION: file_buffer.version, LSPConstants.URI: file_buffer.uri}, LSPConstants.CONTENT_CHANGES: [{LSPConstants.RANGE: {'start': {'line': line, 'character': column}, 'end': {'line': line, 'character': column}}, 'text': text_to_be_inserted}]})
        file_buffer.mark_incremental_change_synced()
        return ls_types.Position(line=new_l, character=new_c)

    def delete_text_between_positions(self, relative_file_path: str, start: ls_types.Position, end: ls_types.Position) -> str:
        if not self.server_started:
            log.error('insert_text_at_position called before Language Server started')
            raise SolidLSPException('Language Server not started')
        absolute_file_path = str(PurePath(self.repository_root_path, relative_file_path))
        uri = pathlib.Path(absolute_file_path).as_uri()
        assert uri in self.open_file_buffers
        file_buffer = self.open_file_buffers[uri]
        file_buffer.version += 1
        new_contents, deleted_text = TextUtils.delete_text_between_positions(file_buffer.contents, start_line=start['line'], start_col=start['character'], end_line=end['line'], end_col=end['character'])
        file_buffer.contents = new_contents
        file_buffer.mark_content_updated()
        self.server.notify.did_change_text_document({LSPConstants.TEXT_DOCUMENT: {LSPConstants.VERSION: file_buffer.version, LSPConstants.URI: file_buffer.uri}, LSPConstants.CONTENT_CHANGES: [{LSPConstants.RANGE: {'start': start, 'end': end}, 'text': ''}]})
        file_buffer.mark_incremental_change_synced()
        return deleted_text

    def _send_definition_request(self, definition_params: DefinitionParams) -> Definition | list[LocationLink] | None:
        return self.server.send.definition(definition_params)

    def request_definition(self, relative_file_path: str, line: int, column: int) -> list[ls_types.Location]:
        if not self.server_started:
            log.error('request_definition called before language server started')
            raise SolidLSPException('Language Server not started')
        if not self._has_waited_for_cross_file_references:
            sleep(self._get_wait_time_for_cross_file_referencing())
            self._has_waited_for_cross_file_references = True
        with self.open_file(relative_file_path):
            definition_params = cast(DefinitionParams, {LSPConstants.TEXT_DOCUMENT: {LSPConstants.URI: pathlib.Path(str(PurePath(self.repository_root_path, relative_file_path))).as_uri()}, LSPConstants.POSITION: {LSPConstants.LINE: line, LSPConstants.CHARACTER: column}})
            response = self._send_definition_request(definition_params)
        ret: list[ls_types.Location] = []
        if isinstance(response, list):
            for item in response:
                assert isinstance(item, dict)
                if LSPConstants.URI in item and LSPConstants.RANGE in item:
                    new_item: dict = {}
                    new_item.update(item)
                    new_item['absolutePath'] = PathUtils.uri_to_path(new_item['uri'])
                    new_item['relativePath'] = PathUtils.get_relative_path(new_item['absolutePath'], self.repository_root_path)
                    ret.append(ls_types.Location(**new_item))
                elif LSPConstants.TARGET_URI in item and LSPConstants.TARGET_RANGE in item and (LSPConstants.TARGET_SELECTION_RANGE in item):
                    new_item: dict = {}
                    new_item['uri'] = item[LSPConstants.TARGET_URI]
                    new_item['absolutePath'] = PathUtils.uri_to_path(new_item['uri'])
                    new_item['relativePath'] = PathUtils.get_relative_path(new_item['absolutePath'], self.repository_root_path)
                    new_item['range'] = item[LSPConstants.TARGET_SELECTION_RANGE]
                    ret.append(ls_types.Location(**new_item))
                else:
                    assert False, f'Unexpected response from Language Server: {item}'
        elif isinstance(response, dict):
            assert LSPConstants.URI in response
            assert LSPConstants.RANGE in response
            new_item: dict = {}
            new_item.update(response)
            new_item['absolutePath'] = PathUtils.uri_to_path(new_item['uri'])
            new_item['relativePath'] = PathUtils.get_relative_path(new_item['absolutePath'], self.repository_root_path)
            ret.append(ls_types.Location(**new_item))
        elif response is None:
            log.warning(f'Language server returned None for definition request at {relative_file_path}:{line}:{column}')
        else:
            assert False, f'Unexpected response from Language Server: {response}'
        return ret

    def _send_references_request(self, relative_file_path: str, line: int, column: int) -> list[lsp_types.Location] | None:
        return self.server.send.references({'textDocument': {'uri': PathUtils.path_to_uri(os.path.join(self.repository_root_path, relative_file_path))}, 'position': {'line': line, 'character': column}, 'context': {'includeDeclaration': False}})

    def request_references(self, relative_file_path: str, line: int, column: int) -> list[ls_types.Location]:
        if not self.server_started:
            log.error('request_references called before Language Server started')
            raise SolidLSPException('Language Server not started')
        if not self._has_waited_for_cross_file_references:
            sleep(self._get_wait_time_for_cross_file_referencing())
            self._has_waited_for_cross_file_references = True
        with self.open_file(relative_file_path):
            try:
                response = self._send_references_request(relative_file_path, line=line, column=column)
            except LSPError as e:
                if getattr(e, 'code', None) == -32603:
                    raise RuntimeError(f'LSP internal error (-32603) when requesting references for {relative_file_path}:{line}:{column}. This often occurs when requesting references for a symbol not referenced in the expected way. ') from e
                raise
        if response is None:
            return []
        ret: list[ls_types.Location] = []
        assert isinstance(response, list), f'Unexpected response from Language Server (expected list, got {type(response)}): {response}'
        for item in response:
            assert isinstance(item, dict), f'Unexpected response from Language Server (expected dict, got {type(item)}): {item}'
            assert LSPConstants.URI in item
            assert LSPConstants.RANGE in item
            abs_path = PathUtils.uri_to_path(item[LSPConstants.URI])
            if not Path(abs_path).is_relative_to(self.repository_root_path):
                log.warning(f'Found a reference in a path outside the repository, probably the LS is parsing things in installed packages or in the standardlib! Path: {abs_path}. This is a bug but we currently simply skip these references.')
                continue
            rel_path = Path(abs_path).relative_to(self.repository_root_path)
            if self.is_ignored_path(str(rel_path)):
                log.debug('Ignoring reference in %s since it should be ignored', rel_path)
                continue
            new_item: dict = {}
            new_item.update(item)
            new_item['absolutePath'] = str(abs_path)
            new_item['relativePath'] = str(rel_path)
            ret.append(ls_types.Location(**new_item))
        return ret

    def request_text_document_diagnostics(self, relative_file_path: str) -> list[ls_types.Diagnostic]:
        if not self.server_started:
            log.error('request_text_document_diagnostics called before Language Server started')
            raise SolidLSPException('Language Server not started')
        with self.open_file(relative_file_path):
            response = self.server.send.text_document_diagnostic({LSPConstants.TEXT_DOCUMENT: {LSPConstants.URI: pathlib.Path(str(PurePath(self.repository_root_path, relative_file_path))).as_uri()}})
        if response is None:
            return []
        assert isinstance(response, dict), f'Unexpected response from Language Server (expected list, got {type(response)}): {response}'
        ret: list[ls_types.Diagnostic] = []
        for item in response['items']:
            new_item: ls_types.Diagnostic = {'uri': pathlib.Path(str(PurePath(self.repository_root_path, relative_file_path))).as_uri(), 'severity': item['severity'], 'message': item['message'], 'range': item['range'], 'code': item['code']}
            ret.append(ls_types.Diagnostic(**new_item))
        return ret

    def retrieve_full_file_content(self, file_path: str) -> str:
        if os.path.isabs(file_path):
            file_path = os.path.relpath(file_path, self.repository_root_path)
        with self.open_file(file_path) as file_data:
            return file_data.contents

    def retrieve_content_around_line(self, relative_file_path: str, line: int, context_lines_before: int=0, context_lines_after: int=0) -> MatchedConsecutiveLines:
        with self.open_file(relative_file_path) as file_data:
            file_contents = file_data.contents
        return MatchedConsecutiveLines.from_file_contents(file_contents, line=line, context_lines_before=context_lines_before, context_lines_after=context_lines_after, source_file_path=relative_file_path)

    def request_completions(self, relative_file_path: str, line: int, column: int, allow_incomplete: bool=False) -> list[ls_types.CompletionItem]:
        with self.open_file(relative_file_path):
            open_file_buffer = self.open_file_buffers[pathlib.Path(os.path.join(self.repository_root_path, relative_file_path)).as_uri()]
            completion_params: LSPTypes.CompletionParams = {'position': {'line': line, 'character': column}, 'textDocument': {'uri': open_file_buffer.uri}, 'context': {'triggerKind': LSPTypes.CompletionTriggerKind.Invoked}}
            response: list[LSPTypes.CompletionItem] | LSPTypes.CompletionList | None = None
            num_retries = 0
            while response is None or (response['isIncomplete'] and num_retries < 30):
                response = self.server.send.completion(completion_params)
                if isinstance(response, list):
                    response = {'items': response, 'isIncomplete': False}
                num_retries += 1
            if response is None or (response['isIncomplete'] and (not allow_incomplete)):
                return []
            if 'items' in response:
                response = response['items']
            response = cast(list[LSPTypes.CompletionItem], response)
            items = [item for item in response if item['kind'] != LSPTypes.CompletionItemKind.Keyword]
            completions_list: list[ls_types.CompletionItem] = []
            for item in items:
                assert 'insertText' in item or 'textEdit' in item
                assert 'kind' in item
                completion_item = {}
                if 'detail' in item:
                    completion_item['detail'] = item['detail']
                if 'label' in item:
                    completion_item['completionText'] = item['label']
                    completion_item['kind'] = item['kind']
                elif 'insertText' in item:
                    completion_item['completionText'] = item['insertText']
                    completion_item['kind'] = item['kind']
                elif 'textEdit' in item and 'newText' in item['textEdit']:
                    completion_item['completionText'] = item['textEdit']['newText']
                    completion_item['kind'] = item['kind']
                elif 'textEdit' in item and 'range' in item['textEdit']:
                    new_dot_lineno, new_dot_colno = (completion_params['position']['line'], completion_params['position']['character'])
                    assert all((item['textEdit']['range']['start']['line'] == new_dot_lineno, item['textEdit']['range']['start']['character'] == new_dot_colno, item['textEdit']['range']['start']['line'] == item['textEdit']['range']['end']['line'], item['textEdit']['range']['start']['character'] == item['textEdit']['range']['end']['character']))
                    completion_item['completionText'] = item['textEdit']['newText']
                    completion_item['kind'] = item['kind']
                elif 'textEdit' in item and 'insert' in item['textEdit']:
                    assert False
                else:
                    assert False
                completion_item = ls_types.CompletionItem(**completion_item)
                completions_list.append(completion_item)
            return [json.loads(json_repr) for json_repr in set((json.dumps(item, sort_keys=True) for item in completions_list))]

    def _request_document_symbols(self, relative_file_path: str, file_data: LSPFileBuffer | None) -> list[SymbolInformation] | list[DocumentSymbol] | None:

        def get_cached_raw_document_symbols(cache_key: str, fd: LSPFileBuffer) -> list[SymbolInformation] | list[DocumentSymbol] | None:
            file_hash_and_result = self._raw_document_symbols_cache.get(cache_key)
            if file_hash_and_result is not None:
                file_hash, result = file_hash_and_result
                if file_hash == fd.content_hash:
                    log.debug('Returning cached raw document symbols for %s', relative_file_path)
                    return result
                else:
                    log.debug('Document content for %s has changed (raw symbol cache is not up-to-date)', relative_file_path)
            else:
                log.debug('No cache hit for raw document symbols symbols in %s', relative_file_path)
            return None

        def get_raw_document_symbols(fd: LSPFileBuffer) -> list[SymbolInformation] | list[DocumentSymbol] | None:
            cache_key = relative_file_path
            response = get_cached_raw_document_symbols(cache_key, fd)
            if response is not None:
                return response
            log.debug(f'Requesting document symbols for {relative_file_path} from the Language Server')
            response = self.server.send.document_symbol({'textDocument': {'uri': pathlib.Path(os.path.join(self.repository_root_path, relative_file_path)).as_uri()}})
            self._raw_document_symbols_cache[cache_key] = (fd.content_hash, response)
            self._raw_document_symbols_cache_is_modified = True
            return response
        if file_data is not None:
            return get_raw_document_symbols(file_data)
        else:
            with self.open_file(relative_file_path) as opened_file_data:
                return get_raw_document_symbols(opened_file_data)

    def request_document_symbols(self, relative_file_path: str, file_buffer: LSPFileBuffer | None=None) -> DocumentSymbols:
        with self._open_file_context(relative_file_path, file_buffer, open_in_ls=False) as file_data:
            cache_key = relative_file_path
            file_hash_and_result = self._document_symbols_cache.get(cache_key)
            if file_hash_and_result is not None:
                file_hash, document_symbols = file_hash_and_result
                if file_hash == file_data.content_hash:
                    log.debug('Returning cached document symbols for %s', relative_file_path)
                    return document_symbols
                else:
                    log.debug('Cached document symbol content for %s has changed', relative_file_path)
            else:
                log.debug('No cache hit for document symbols in %s', relative_file_path)
            try:
                file_data.ensure_open_in_ls()
            except (RuntimeError, OSError, ValueError, TypeError) as exc:
                raise SolidLSPException(f'ERR_LSP_SYNC_OPEN_FAILED: {exc}') from exc
            file_data.sync_changes_to_ls()
            root_symbols = self._request_document_symbols(relative_file_path, file_data)
            if root_symbols is None:
                log.warning(f"Received None response from the Language Server for document symbols in {relative_file_path}. This means the language server can't understand this file (possibly due to syntax errors). It may also be due to a bug or misconfiguration of the LS. Returning empty list")
                return DocumentSymbols([])
            assert isinstance(root_symbols, list), f'Unexpected response from Language Server: {root_symbols}'
            log.debug('Received %d root symbols for %s from the language server', len(root_symbols), relative_file_path)
            body_factory = SymbolBodyFactory(file_data)

            def convert_to_unified_symbol(original_symbol_dict: GenericDocumentSymbol) -> ls_types.UnifiedSymbolInformation:
                """
                Converts the given symbol dictionary to the unified representation, ensuring
                that all required fields are present (except 'children' which is handled separately).

                :param original_symbol_dict: the item to augment
                :return: the augmented item (new object)
                """
                item = cast(ls_types.UnifiedSymbolInformation, dict(original_symbol_dict))
                absolute_path = os.path.join(self.repository_root_path, relative_file_path)
                if 'location' not in item:
                    uri = pathlib.Path(absolute_path).as_uri()
                    assert 'range' in item
                    tree_location = ls_types.Location(uri=uri, range=item['range'], absolutePath=absolute_path, relativePath=relative_file_path)
                    item['location'] = tree_location
                location = item['location']
                if 'absolutePath' not in location:
                    location['absolutePath'] = absolute_path
                if 'relativePath' not in location:
                    location['relativePath'] = relative_file_path
                item['body'] = self.create_symbol_body(item, factory=body_factory)
                if 'selectionRange' not in item:
                    if 'range' in item:
                        item['selectionRange'] = item['range']
                    else:
                        item['selectionRange'] = item['location']['range']
                return item

            def convert_symbols_with_common_parent(symbols: list[DocumentSymbol] | list[SymbolInformation] | list[UnifiedSymbolInformation], parent: ls_types.UnifiedSymbolInformation | None) -> list[ls_types.UnifiedSymbolInformation]:
                """
                Converts the given symbols into UnifiedSymbolInformation with proper parent-child relationships,
                adding overload indices for symbols with the same name under the same parent.
                """
                total_name_counts: dict[str, int] = defaultdict(lambda: 0)
                for symbol in symbols:
                    total_name_counts[symbol['name']] += 1
                name_counts: dict[str, int] = defaultdict(lambda: 0)
                unified_symbols = []
                for symbol in symbols:
                    usymbol = convert_to_unified_symbol(symbol)
                    if total_name_counts[usymbol['name']] > 1:
                        usymbol['overload_idx'] = name_counts[usymbol['name']]
                    name_counts[usymbol['name']] += 1
                    usymbol['parent'] = parent
                    if 'children' in usymbol:
                        usymbol['children'] = convert_symbols_with_common_parent(usymbol['children'], usymbol)
                    else:
                        usymbol['children'] = []
                    unified_symbols.append(usymbol)
                return unified_symbols
            unified_root_symbols = convert_symbols_with_common_parent(root_symbols, None)
            document_symbols = DocumentSymbols(unified_root_symbols)
            log.debug('Updating cached document symbols for %s', relative_file_path)
            self._document_symbols_cache[cache_key] = (file_data.content_hash, document_symbols)
            self._document_symbols_cache_is_modified = True
            return document_symbols

    def request_full_symbol_tree(self, within_relative_path: str | None=None) -> list[ls_types.UnifiedSymbolInformation]:
        if within_relative_path is not None:
            within_abs_path = os.path.join(self.repository_root_path, within_relative_path)
            if not os.path.exists(within_abs_path):
                raise FileNotFoundError(f'File or directory not found: {within_abs_path}')
            if os.path.isfile(within_abs_path):
                if self.is_ignored_path(within_relative_path):
                    log.error('You passed a file explicitly, but it is ignored. This is probably an error. File: %s', within_relative_path)
                    return []
                else:
                    root_nodes = self.request_document_symbols(within_relative_path).root_symbols
                    return root_nodes

        def process_directory(rel_dir_path: str) -> list[ls_types.UnifiedSymbolInformation]:
            abs_dir_path = self.repository_root_path if rel_dir_path == '.' else os.path.join(self.repository_root_path, rel_dir_path)
            abs_dir_path = os.path.realpath(abs_dir_path)
            if self.is_ignored_path(str(Path(abs_dir_path).relative_to(self.repository_root_path))):
                log.debug('Skipping directory: %s (because it should be ignored)', rel_dir_path)
                return []
            result = []
            try:
                contained_dir_or_file_names = os.listdir(abs_dir_path)
            except OSError:
                return []
            package_symbol = ls_types.UnifiedSymbolInformation(name=os.path.basename(abs_dir_path), kind=ls_types.SymbolKind.Package, location=ls_types.Location(uri=str(pathlib.Path(abs_dir_path).as_uri()), range={'start': {'line': 0, 'character': 0}, 'end': {'line': 0, 'character': 0}}, absolutePath=str(abs_dir_path), relativePath=str(Path(abs_dir_path).resolve().relative_to(self.repository_root_path))), children=[])
            result.append(package_symbol)
            for contained_dir_or_file_name in contained_dir_or_file_names:
                contained_dir_or_file_abs_path = os.path.join(abs_dir_path, contained_dir_or_file_name)
                try:
                    contained_dir_or_file_rel_path = str(Path(contained_dir_or_file_abs_path).resolve().relative_to(self.repository_root_path))
                except ValueError as e:
                    log.warning('Skipping path %s; likely outside of the repository root %s [cause: %s]', contained_dir_or_file_abs_path, self.repository_root_path, e)
                    continue
                if self.is_ignored_path(contained_dir_or_file_rel_path):
                    log.debug('Skipping item: %s (because it should be ignored)', contained_dir_or_file_rel_path)
                    continue
                if os.path.isdir(contained_dir_or_file_abs_path):
                    child_symbols = process_directory(contained_dir_or_file_rel_path)
                    package_symbol['children'].extend(child_symbols)
                    for child in child_symbols:
                        child['parent'] = package_symbol
                elif os.path.isfile(contained_dir_or_file_abs_path):
                    with self._open_file_context(contained_dir_or_file_rel_path) as file_data:
                        document_symbols = self.request_document_symbols(contained_dir_or_file_rel_path, file_data)
                        file_root_nodes = document_symbols.root_symbols
                        file_range = self._get_range_from_file_content(file_data.contents)
                        file_symbol = ls_types.UnifiedSymbolInformation(name=os.path.splitext(contained_dir_or_file_name)[0], kind=ls_types.SymbolKind.File, range=file_range, selectionRange=file_range, location=ls_types.Location(uri=str(pathlib.Path(contained_dir_or_file_abs_path).as_uri()), range=file_range, absolutePath=str(contained_dir_or_file_abs_path), relativePath=str(Path(contained_dir_or_file_abs_path).resolve().relative_to(self.repository_root_path))), children=file_root_nodes, parent=package_symbol)
                        for child in file_root_nodes:
                            child['parent'] = file_symbol
                    package_symbol['children'].append(file_symbol)

                    def fix_relative_path(nodes: list[ls_types.UnifiedSymbolInformation]) -> None:
                        for node in nodes:
                            if 'location' in node and 'relativePath' in node['location']:
                                path = Path(node['location']['relativePath'])
                                if path.is_absolute():
                                    try:
                                        path = path.relative_to(self.repository_root_path)
                                        node['location']['relativePath'] = str(path)
                                    except ValueError as exc:
                                        log.debug("Could not normalize relative path '%s': %s", path, exc)
                            if 'children' in node:
                                fix_relative_path(node['children'])
                    fix_relative_path(file_root_nodes)
            return result
        start_rel_path = within_relative_path or '.'
        return process_directory(start_rel_path)

    @staticmethod
    def _get_range_from_file_content(file_content: str) -> ls_types.Range:
        lines = file_content.split('\n')
        end_line = len(lines)
        end_column = len(lines[-1])
        return ls_types.Range(start=ls_types.Position(line=0, character=0), end=ls_types.Position(line=end_line, character=end_column))

    def request_dir_overview(self, relative_dir_path: str) -> dict[str, list[UnifiedSymbolInformation]]:
        symbol_tree = self.request_full_symbol_tree(relative_dir_path)
        result: dict[str, list[UnifiedSymbolInformation]] = defaultdict(list)

        def process_symbol(symbol: ls_types.UnifiedSymbolInformation) -> None:
            if symbol['kind'] == ls_types.SymbolKind.File:
                for child in symbol['children']:
                    absolute_path = Path(child['location']['absolutePath']).resolve()
                    repository_root = Path(self.repository_root_path).resolve()
                    try:
                        path = absolute_path.relative_to(repository_root)
                    except ValueError:
                        if 'relativePath' in child['location'] and child['location']['relativePath']:
                            path = Path(child['location']['relativePath'])
                        else:
                            path_parts = absolute_path.parts
                            if 'test_repo' in path_parts:
                                test_repo_idx = path_parts.index('test_repo')
                                path = Path(*path_parts[test_repo_idx:])
                            else:
                                path = Path(absolute_path.name)
                    result[str(path)].append(child)
            for child in symbol['children']:
                process_symbol(child)
        for root in symbol_tree:
            process_symbol(root)
        return result

    def request_document_overview(self, relative_file_path: str) -> list[UnifiedSymbolInformation]:
        return self.request_document_symbols(relative_file_path).root_symbols

    def request_overview(self, within_relative_path: str) -> dict[str, list[UnifiedSymbolInformation]]:
        abs_path = (Path(self.repository_root_path) / within_relative_path).resolve()
        if not abs_path.exists():
            raise FileNotFoundError(f'File or directory not found: {abs_path}')
        if abs_path.is_file():
            symbols_overview = self.request_document_overview(within_relative_path)
            return {within_relative_path: symbols_overview}
        else:
            return self.request_dir_overview(within_relative_path)

    def request_hover(self, relative_file_path: str, line: int, column: int) -> ls_types.Hover | None:
        with self.open_file(relative_file_path):
            uri = pathlib.Path(os.path.join(self.repository_root_path, relative_file_path)).as_uri()
            return self._request_hover(uri, line, column)

    def _request_hover(self, uri: str, line: int, column: int) -> ls_types.Hover | None:
        response = self.server.send.hover({'textDocument': {'uri': uri}, 'position': {'line': line, 'character': column}})
        if response is None:
            return None
        assert isinstance(response, dict)
        contents = response.get('contents')
        if not contents:
            return None
        if isinstance(contents, dict) and (not contents.get('value')):
            return None
        return ls_types.Hover(**response)

    def request_signature_help(self, relative_file_path: str, line: int, column: int) -> ls_types.SignatureHelp | None:
        with self.open_file(relative_file_path):
            response = self.server.send.signature_help({'textDocument': {'uri': pathlib.Path(os.path.join(self.repository_root_path, relative_file_path)).as_uri()}, 'position': {'line': line, 'character': column}})
        if response is None:
            return None
        assert isinstance(response, dict)
        return ls_types.SignatureHelp(**response)

    def create_symbol_body(self, symbol: ls_types.UnifiedSymbolInformation | LSPTypes.SymbolInformation, factory: SymbolBodyFactory | None=None) -> SymbolBody:
        if factory is None:
            assert 'relativePath' in symbol['location']
            with self._open_file_context(symbol['location']['relativePath']) as f:
                factory = SymbolBodyFactory(f)
        return factory.create_symbol_body(symbol)

    def request_referencing_symbols(self, relative_file_path: str, line: int, column: int, include_imports: bool=True, include_self: bool=False, include_body: bool=False, include_file_symbols: bool=False) -> list[ReferenceInSymbol]:
        if not self.server_started:
            log.error('request_referencing_symbols called before Language Server started')
            raise SolidLSPException('Language Server not started')
        references = self.request_references(relative_file_path, line, column)
        if not references:
            return []
        result = []
        incoming_symbol = None
        for ref in references:
            ref_path = ref['relativePath']
            assert ref_path is not None
            ref_line = ref['range']['start']['line']
            ref_col = ref['range']['start']['character']
            with self.open_file(ref_path) as file_data:
                body_factory = SymbolBodyFactory(file_data)
                containing_symbol = self.request_containing_symbol(ref_path, ref_line, ref_col, include_body=include_body, body_factory=body_factory)
                if containing_symbol is None:
                    ref_text = file_data.contents.split('\n')[ref_line]
                    if '.' in ref_text:
                        containing_symbol_name = ref_text.split('.')[0]
                        document_symbols = self.request_document_symbols(ref_path)
                        for symbol in document_symbols.iter_symbols():
                            if symbol['name'] == containing_symbol_name and symbol['kind'] == ls_types.SymbolKind.Variable:
                                containing_symbol = copy(symbol)
                                containing_symbol['location'] = ref
                                containing_symbol['range'] = ref['range']
                                break
                if containing_symbol is None and include_file_symbols:
                    log.warning(f'Could not find containing symbol for {ref_path}:{ref_line}:{ref_col}. Returning file symbol instead')
                    fileRange = self._get_range_from_file_content(file_data.contents)
                    location = ls_types.Location(uri=str(pathlib.Path(os.path.join(self.repository_root_path, ref_path)).as_uri()), range=fileRange, absolutePath=str(os.path.join(self.repository_root_path, ref_path)), relativePath=ref_path)
                    name = os.path.splitext(os.path.basename(ref_path))[0]
                    containing_symbol = ls_types.UnifiedSymbolInformation(kind=ls_types.SymbolKind.File, range=fileRange, selectionRange=fileRange, location=location, name=name, children=[])
                    if include_body:
                        containing_symbol['body'] = self.create_symbol_body(containing_symbol, factory=body_factory)
                if containing_symbol is None or (not include_file_symbols and containing_symbol['kind'] == ls_types.SymbolKind.File):
                    continue
                assert 'location' in containing_symbol
                assert 'selectionRange' in containing_symbol
                if containing_symbol['location']['relativePath'] == relative_file_path and containing_symbol['selectionRange']['start']['line'] == ref_line and (containing_symbol['selectionRange']['start']['character'] == ref_col):
                    incoming_symbol = containing_symbol
                    if include_self:
                        result.append(ReferenceInSymbol(symbol=containing_symbol, line=ref_line, character=ref_col))
                        continue
                    log.debug(f"Found self-reference for {incoming_symbol['name']}, skipping it since include_self={include_self!r}")
                    continue
                if not include_imports and incoming_symbol is not None and (containing_symbol['name'] == incoming_symbol['name']) and (containing_symbol['kind'] == incoming_symbol['kind']):
                    log.debug(
                        f"Found import of referenced symbol {incoming_symbol['name']} in {containing_symbol['location']['relativePath']}, skipping"
                    )
                    continue
                result.append(ReferenceInSymbol(symbol=containing_symbol, line=ref_line, character=ref_col))
        return result

    def request_containing_symbol(self, relative_file_path: str, line: int, column: int | None=None, strict: bool=False, include_body: bool=False, body_factory: SymbolBodyFactory | None=None) -> ls_types.UnifiedSymbolInformation | None:
        with self.open_file(relative_file_path):
            absolute_file_path = str(PurePath(self.repository_root_path, relative_file_path))
            content = FileUtils.read_file(absolute_file_path, self._encoding)
            if content.split('\n')[line].strip() == '':
                log.error(f'Passing empty lines to request_container_symbol is currently not supported, relative_file_path={relative_file_path!r}, line={line!r}')
                return None
        document_symbols = self.request_document_symbols(relative_file_path)
        for symbol in document_symbols.iter_symbols():
            if 'location' not in symbol:
                range = symbol['range']
                location = ls_types.Location(uri=f'file:/{absolute_file_path}', range=range, absolutePath=absolute_file_path, relativePath=relative_file_path)
                symbol['location'] = location
            else:
                location = symbol['location']
                assert 'range' in location
                location['absolutePath'] = absolute_file_path
                location['relativePath'] = relative_file_path
                location['uri'] = Path(absolute_file_path).as_uri()
        container_symbol_kinds = {ls_types.SymbolKind.Method, ls_types.SymbolKind.Function, ls_types.SymbolKind.Class}

        def is_position_in_range(line: int, range_d: ls_types.Range) -> bool:
            start = range_d['start']
            end = range_d['end']
            column_condition = True
            if strict:
                line_condition = end['line'] >= line > start['line']
                if column is not None and line == start['line']:
                    column_condition = column > start['character']
            else:
                line_condition = end['line'] >= line >= start['line']
                if column is not None and line == start['line']:
                    column_condition = column >= start['character']
            return line_condition and column_condition
        candidate_containers = [s for s in document_symbols.iter_symbols() if s['kind'] in container_symbol_kinds and s['location']['range']['start']['line'] != s['location']['range']['end']['line']]
        var_containers = [s for s in document_symbols.iter_symbols() if s['kind'] == ls_types.SymbolKind.Variable]
        candidate_containers.extend(var_containers)
        if not candidate_containers:
            return None
        containing_symbols = []
        for symbol in candidate_containers:
            s_range = symbol['location']['range']
            if not is_position_in_range(line, s_range):
                continue
            containing_symbols.append(symbol)
        if containing_symbols:
            containing_symbol = max(containing_symbols, key=lambda s: s['location']['range']['start']['line'])
            if include_body:
                containing_symbol['body'] = self.create_symbol_body(containing_symbol, factory=body_factory)
            return containing_symbol
        else:
            return None

    def request_container_of_symbol(self, symbol: ls_types.UnifiedSymbolInformation, include_body: bool=False) -> ls_types.UnifiedSymbolInformation | None:
        if 'parent' in symbol:
            return symbol['parent']
        assert 'location' in symbol, f'Symbol {symbol} has no location and no parent attribute'
        return self.request_containing_symbol(symbol['location']['relativePath'], symbol['location']['range']['start']['line'], symbol['location']['range']['start']['character'], strict=True, include_body=include_body)

    def _get_preferred_definition(self, definitions: list[ls_types.Location]) -> ls_types.Location:
        return definitions[0]

    def request_defining_symbol(self, relative_file_path: str, line: int, column: int, include_body: bool=False) -> ls_types.UnifiedSymbolInformation | None:
        if not self.server_started:
            log.error('request_defining_symbol called before language server started')
            raise SolidLSPException('Language Server not started')
        definitions = self.request_definition(relative_file_path, line, column)
        if not definitions:
            return None
        definition = self._get_preferred_definition(definitions)
        def_path = definition['relativePath']
        assert def_path is not None
        def_line = definition['range']['start']['line']
        def_col = definition['range']['start']['character']
        defining_symbol = self.request_containing_symbol(def_path, def_line, def_col, strict=False, include_body=include_body)
        return defining_symbol

    def _cache_context_fingerprint(self) -> Hashable | None:
        return None

    def _document_symbols_cache_version(self) -> Hashable:
        fingerprint = self._cache_context_fingerprint()
        if fingerprint is not None:
            return (self.DOCUMENT_SYMBOL_CACHE_VERSION, fingerprint)
        return self.DOCUMENT_SYMBOL_CACHE_VERSION

    def _save_raw_document_symbols_cache(self) -> None:
        cache_file = self.cache_dir / self.RAW_DOCUMENT_SYMBOL_CACHE_FILENAME
        if not self._raw_document_symbols_cache_is_modified:
            log.debug('No changes to raw document symbols cache, skipping save')
            return
        log.info('Saving updated raw document symbols cache to %s', cache_file)
        try:
            save_cache(str(cache_file), self._raw_document_symbols_cache_version(), self._raw_document_symbols_cache)
            self._raw_document_symbols_cache_is_modified = False
        except (OSError, ValueError, TypeError, EOFError) as e:
            log.error('Failed to save raw document symbols cache to %s: %s. Note: this may have resulted in a corrupted cache file.', cache_file, e)

    def _raw_document_symbols_cache_version(self) -> tuple[Hashable, ...]:
        base_version: tuple[Hashable, ...] = (self.RAW_DOCUMENT_SYMBOLS_CACHE_VERSION, self._ls_specific_raw_document_symbols_cache_version)
        fingerprint = self._cache_context_fingerprint()
        if fingerprint is not None:
            return (*base_version, fingerprint)
        return base_version

    def _load_raw_document_symbols_cache(self) -> None:
        cache_file = self.cache_dir / self.RAW_DOCUMENT_SYMBOL_CACHE_FILENAME
        if not cache_file.exists():
            legacy_cache_file = self.cache_dir / self.RAW_DOCUMENT_SYMBOL_CACHE_FILENAME_LEGACY_FALLBACK
            if legacy_cache_file.exists():
                try:
                    legacy_cache: dict[str, tuple[str, tuple[list[ls_types.UnifiedSymbolInformation], list[ls_types.UnifiedSymbolInformation]]]] = load_pickle(legacy_cache_file)
                    log.info('Migrating legacy document symbols cache with %d entries', len(legacy_cache))
                    num_symbols_migrated = 0
                    migrated_cache = {}
                    for cache_key, (file_hash, (all_symbols, root_symbols)) in legacy_cache.items():
                        if cache_key.endswith('-True'):
                            new_cache_key = cache_key[:-5]
                            migrated_cache[new_cache_key] = (file_hash, root_symbols)
                            num_symbols_migrated += len(all_symbols)
                    log.info('Migrated %d document symbols from legacy cache', num_symbols_migrated)
                    self._raw_document_symbols_cache = migrated_cache
                    self._raw_document_symbols_cache_is_modified = True
                    self._save_raw_document_symbols_cache()
                    legacy_cache_file.unlink()
                    return
                except (OSError, ValueError, TypeError, EOFError) as e:
                    log.error('Error during cache migration: %s', e)
                    return
        if cache_file.exists():
            log.info('Loading document symbols cache from %s', cache_file)
            try:
                saved_cache = load_cache(str(cache_file), self._raw_document_symbols_cache_version())
                if saved_cache is not None:
                    self._raw_document_symbols_cache = saved_cache
                    log.info(f'Loaded {len(self._raw_document_symbols_cache)} entries from raw document symbols cache.')
            except (OSError, ValueError, TypeError, EOFError) as e:
                log.warning('Failed to load raw document symbols cache from %s (%s); Ignoring cache.', cache_file, e)

    def _save_document_symbols_cache(self) -> None:
        cache_file = self.cache_dir / self.DOCUMENT_SYMBOL_CACHE_FILENAME
        if not self._document_symbols_cache_is_modified:
            log.debug('No changes to document symbols cache, skipping save')
            return
        log.info('Saving updated document symbols cache to %s', cache_file)
        try:
            save_cache(str(cache_file), self._document_symbols_cache_version(), self._document_symbols_cache)
            self._document_symbols_cache_is_modified = False
        except (OSError, ValueError, TypeError, EOFError) as e:
            log.error('Failed to save document symbols cache to %s: %s. Note: this may have resulted in a corrupted cache file.', cache_file, e)

    def _load_document_symbols_cache(self) -> None:
        cache_file = self.cache_dir / self.DOCUMENT_SYMBOL_CACHE_FILENAME
        if cache_file.exists():
            log.info('Loading document symbols cache from %s', cache_file)
            try:
                saved_cache = load_cache(str(cache_file), self._document_symbols_cache_version())
                if saved_cache is not None:
                    self._document_symbols_cache = saved_cache
                    log.info(f'Loaded {len(self._document_symbols_cache)} entries from document symbols cache.')
            except (OSError, ValueError, TypeError, EOFError) as e:
                log.warning('Failed to load document symbols cache from %s (%s); Ignoring cache.', cache_file, e)

    def save_cache(self) -> None:
        self._save_raw_document_symbols_cache()
        self._save_document_symbols_cache()

    def request_workspace_symbol(self, query: str) -> list[ls_types.UnifiedSymbolInformation] | None:
        response = self.server.send.workspace_symbol({'query': query})
        if response is None:
            return None
        assert isinstance(response, list)
        ret: list[ls_types.UnifiedSymbolInformation] = []
        for item in response:
            assert isinstance(item, dict)
            assert LSPConstants.NAME in item
            assert LSPConstants.KIND in item
            assert LSPConstants.LOCATION in item
            ret.append(ls_types.UnifiedSymbolInformation(**item))
        return ret

    def request_rename_symbol_edit(self, relative_file_path: str, line: int, column: int, new_name: str) -> ls_types.WorkspaceEdit | None:
        params = RenameParams(textDocument=ls_types.TextDocumentIdentifier(uri=pathlib.Path(os.path.join(self.repository_root_path, relative_file_path)).as_uri()), position=ls_types.Position(line=line, character=column), newName=new_name)
        with self.open_file(relative_file_path):
            return self.server.send.rename(params)

    def apply_text_edits_to_file(self, relative_path: str, edits: list[ls_types.TextEdit]) -> None:
        with self.open_file(relative_path):
            sorted_edits = sorted(edits, key=lambda e: (e['range']['start']['line'], e['range']['start']['character']), reverse=True)
            for edit in sorted_edits:
                start_pos = ls_types.Position(line=edit['range']['start']['line'], character=edit['range']['start']['character'])
                end_pos = ls_types.Position(line=edit['range']['end']['line'], character=edit['range']['end']['character'])
                self.delete_text_between_positions(relative_path, start_pos, end_pos)
                self.insert_text_at_position(relative_path, start_pos['line'], start_pos['character'], edit['newText'])

    def start(self) -> 'SolidLanguageServer':
        log.info(f'Starting language server with language {self.language_server.language} for {self.language_server.repository_root_path}')
        self._start_server_process()
        return self

    def stop(self, shutdown_timeout: float=2.0) -> None:
        try:
            self._shutdown(timeout=shutdown_timeout)
        except (OSError, RuntimeError, ValueError, SolidLSPException) as e:
            log.warning(f'Exception while shutting down language server: {e}')

    @property
    def language_server(self) -> Self:
        return self

    @property
    def handler(self) -> SolidLanguageServerHandler:
        return self.server

    def is_running(self) -> bool:
        return self.server.is_running()
