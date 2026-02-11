import os
import time
import json
import logging
import threading
import multiprocessing
import tempfile
import concurrent.futures
from collections import OrderedDict
from typing import Optional, Callable
from pathlib import Path
from sari.core.config.main import Config
from sari.core.db.main import LocalSearchDB
from .worker import IndexWorker
from sari.core.workspace import WorkspaceManager

from sari.core.models import IndexingResult


def _is_pid_alive(pid: int) -> bool:
    """Return True when the target PID is alive."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        return False


def _scan_to_db(config: Config, db: LocalSearchDB,
                logger: logging.Logger,
                parent_pid: Optional[int] = None,
                parent_alive_check: Optional[Callable[[int], bool]] = None) -> dict[str, object]:
    """
    파일 시스템을 스캔하여 변경된 파일을 감지하고 데이터베이스에 인덱싱합니다.
    이 함수는 별도의 프로세스(Worker) 내에서 실행될 수 있으며, 결과를 Status 딕셔너리로 반환합니다.
    """
    status = {
        "scan_started_ts": int(time.time()),
        "scan_finished_ts": 0,
        "scanned_files": 0,
        "indexed_files": 0,
        "symbols_extracted": 0,
        "errors": 0,
        "index_version": "",
    }
    worker = IndexWorker(config, db, logger, None)
    max_workers = os.cpu_count() or 4
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)
    check_parent_alive = parent_alive_check or _is_pid_alive

    def _ensure_parent_alive() -> None:
        if parent_pid is None:
            return
        if not check_parent_alive(int(parent_pid)):
            raise RuntimeError(
                f"orphaned worker detected: parent pid {parent_pid} is not alive")

    try:
        def _get_files_generator():
            """
            모든 파일을 메모리에 로드하지 않고 하나씩 처리하기 위한 제너레이터입니다.
            각 루트 디렉토리를 순회하며 인덱싱 대상 파일을 선별합니다.
            """
            for root in config.workspace_roots:
                rid = WorkspaceManager.root_id(root)
                db.ensure_root(rid, str(root))
                for path in Path(root).rglob("*"):
                    if path.is_file() and config.should_index(str(path)):
                        yield root, path, rid

        futures = []
        now = int(time.time())
        for root, path, rid in _get_files_generator():
            _ensure_parent_alive()
            status["scanned_files"] += 1
            try:
                st = path.stat()
                # 파일 처리 작업을 쓰레드 풀에 제출
                futures.append(executor.submit(
                    worker.process_file_task,
                    root, path, st, now, st.st_mtime, True, root_id=rid
                ))
            except Exception:
                status["errors"] += 1

        file_rows = []
        all_symbols = []
        all_relations = []

        # 완료된 작업 결과 수집
        for future in concurrent.futures.as_completed(futures):
            _ensure_parent_alive()
            try:
                res: Optional[IndexingResult] = future.result()
                if not res:
                    continue

                if res.type in ("changed", "new"):
                    status["indexed_files"] += 1
                    file_rows.append(res.to_file_row())

                    root_id = res.root_id
                    if res.symbols:
                        for s in res.symbols:
                            all_symbols.append(
                                (s.sid,
                                 s.path,
                                 root_id,
                                 s.name,
                                 s.kind,
                                 s.line,
                                 s.end_line,
                                 s.content,
                                 s.parent,
                                 json.dumps(
                                     s.meta),
                                    s.doc,
                                    s.qualname))
                        status["symbols_extracted"] += len(res.symbols)

                    if res.relations:
                        for r in res.relations:
                            all_relations.append(
                                (res.path,
                                 root_id,
                                 r.from_name,
                                 r.from_sid,
                                 r.to_path or res.path,
                                 root_id,
                                 r.to_name,
                                 r.to_sid,
                                 r.rel_type,
                                 r.line,
                                 json.dumps(
                                     r.meta)))

            except Exception as e:
                status["errors"] += 1
                if logger:
                    logger.error(f"Async result processing failed: {e}")

        # 데이터베이스 일괄 업데이트 (Batch Update)
        if file_rows:
            db.upsert_files_turbo(file_rows)
        db.finalize_turbo_batch()

        if all_symbols:
            # 중복 심볼 제거 및 트랜잭션 처리
            unique_symbols = list(OrderedDict.fromkeys(all_symbols))
            try:
                db.upsert_symbols_tx(None, unique_symbols)
            except Exception as e:
                if logger:
                    logger.error(f"Failed to store extracted symbols: {e}")

        if all_relations:
            try:
                db.upsert_relations_tx(None, all_relations)
            except Exception as e:
                if logger:
                    logger.error(f"Failed to store extracted relations: {e}")

        status["scan_finished_ts"] = int(time.time())
        status["index_version"] = str(status["scan_finished_ts"])
        return status
    finally:
        executor.shutdown(wait=True, cancel_futures=True)


def _worker_build_snapshot(
        config_dict: dict[str, object],
        snapshot_path: str,
        status_path: str,
        log_path: str,
        parent_pid: Optional[int] = None) -> None:
    """별도 프로세스에서 인덱싱을 수행하고 결과를 스냅샷 DB 및 상태 파일에 기록합니다."""
    logger = logging.getLogger("sari.indexer.worker")
    if log_path:
        try:
            fh = logging.FileHandler(log_path, encoding="utf-8")
            fh.setFormatter(logging.Formatter(
                "%(asctime)s %(levelname)s %(message)s"))
            logger.addHandler(fh)
            logger.setLevel(logging.INFO)
        except Exception:
            pass
    try:
        cfg = Config(**config_dict)
        db = LocalSearchDB(snapshot_path, logger=logger, bind_proxy=False, journal_mode="delete")
        status = _scan_to_db(cfg, db, logger, parent_pid=parent_pid)
        db.close_all()
        # 성공 상태 기록
        with open(status_path, "w", encoding="utf-8") as f:
            json.dump({"ok": True, "status": status,
                      "snapshot_path": snapshot_path}, f)
    except Exception as e:
        # 실패 상태 기록
        try:
            with open(status_path, "w", encoding="utf-8") as f:
                json.dump({"ok": False, "error": str(e),
                          "snapshot_path": snapshot_path}, f)
        except Exception:
            pass


class Indexer:
    """
    전체 인덱싱 작업을 관리하는 클래스입니다.
    주기적인 스캔, 온디맨드 리스캔, 워커 프로세스 관리 등을 담당합니다.
    """

    def __init__(
            self,
            config: Config,
            db: LocalSearchDB,
            logger=None,
            **kwargs):
        self.config = config
        self.db = db
        self.logger = logger or logging.getLogger("sari.indexer")
        self.status = IndexStatus()
        self.worker = IndexWorker(config, db, self.logger, None)
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        self.indexing_enabled = True
        self.indexer_mode = "worker"
        self._rescan_event = threading.Event()
        self._stop_event = threading.Event()
        self._scan_lock = threading.Lock()
        self._worker_proc: Optional[multiprocessing.Process] = None
        self._worker_snapshot_path: Optional[str] = None
        self._worker_status_path: Optional[str] = None
        self._worker_log_path: Optional[str] = None
        self._pending_rescan = False

    def _remove_file_if_exists(self, path: Optional[str]) -> None:
        if not path:
            return
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception as e:
            if self.logger:
                self.logger.debug("Failed to remove file %s: %s", path, e)

    def _cleanup_snapshot_artifacts(
            self, snapshot_path: Optional[str]) -> None:
        if not snapshot_path:
            return
        self._remove_file_if_exists(snapshot_path)
        self._remove_file_if_exists(f"{snapshot_path}-wal")
        self._remove_file_if_exists(f"{snapshot_path}-shm")
        self._remove_file_if_exists(f"{snapshot_path}-journal")

    def _cleanup_stale_snapshot_artifacts(
            self,
            now_ts: Optional[int] = None,
            max_age_seconds: int = 3600) -> None:
        base = self._safe_db_path()
        if not base:
            return
        parent = os.path.dirname(base) or "."
        prefix = f"{os.path.basename(base)}.snapshot."
        now = int(now_ts if now_ts is not None else time.time())
        try:
            for name in os.listdir(parent):
                if not name.startswith(prefix):
                    continue
                path = os.path.join(parent, name)
                try:
                    st = os.stat(path)
                except Exception:
                    continue
                if (now - int(st.st_mtime)) >= int(max_age_seconds):
                    self._remove_file_if_exists(path)
        except Exception as e:
            if self.logger:
                self.logger.debug(
                    "Failed to cleanup stale snapshots under %s: %s", parent, e)

    def scan_once(self):
        """동기적으로 1회 스캔을 수행합니다. (블로킹)"""
        with self._scan_lock:
            self.status.index_ready = False
            snapshot_path = self._snapshot_path()
            snapshot_db = LocalSearchDB(
                snapshot_path,
                logger=self.logger,
                bind_proxy=False,
                journal_mode="delete")
            status = _scan_to_db(self.config, snapshot_db, self.logger)
            try:
                snapshot_db.close_all()
            except Exception:
                try:
                    snapshot_db.close()
                except Exception:
                    pass
            try:
                # 스냅샷 DB를 메인 DB로 교체 (Swap)
                self.db.swap_db_file(snapshot_path)
                self.status.scan_started_ts = status.get("scan_started_ts", 0)
                self.status.scan_finished_ts = status.get(
                    "scan_finished_ts", 0)
                self.status.scanned_files = status.get("scanned_files", 0)
                self.status.indexed_files = status.get("indexed_files", 0)
                self.status.symbols_extracted = status.get(
                    "symbols_extracted", 0)
                self.status.errors = status.get("errors", 0)
                self.status.index_version = status.get("index_version", "")
                self.status.index_ready = True
                self._cleanup_snapshot_artifacts(snapshot_path)
            except Exception as e:
                self.status.errors += 1
                self.status.last_error = str(e)
                if self.logger:
                    self.logger.error(f"Snapshot swap failed: {e}")

    def stop(self):
        """인덱서 실행을 중단하고 리소스를 정리합니다."""
        self._stop_event.set()
        if self._worker_proc and self._worker_proc.is_alive():
            try:
                self._worker_proc.terminate()
                self._worker_proc.join(timeout=3.0)
                if self._worker_proc.is_alive():
                    self._worker_proc.kill()
                    self._worker_proc.join(timeout=1.0)
            except Exception:
                pass
        self._cleanup_snapshot_artifacts(self._worker_snapshot_path)
        self._remove_file_if_exists(self._worker_status_path)
        self._remove_file_if_exists(self._worker_log_path)
        self._worker_proc = None
        self._worker_snapshot_path = None
        self._worker_status_path = None
        self._worker_log_path = None
        self._cleanup_stale_snapshot_artifacts()
        if self._executor:
            self._executor.shutdown(wait=True, cancel_futures=True)
            self._executor = None

    def run_forever(self):
        """백그라운드에서 주기적으로 인덱싱 작업을 수행하는 무한 루프입니다."""
        next_due = time.time()
        while not self._stop_event.is_set():
            self._finalize_worker_if_done()
            now = time.time()
            if self._rescan_event.is_set() or now >= next_due:
                self._rescan_event.clear()
                self._start_worker_scan()
                next_due = now + self.config.scan_interval_seconds
            time.sleep(0.2)

    def request_rescan(self):
        """즉시 리스캔을 요청합니다."""
        self._rescan_event.set()

    def index_file(self, _path: str):
        """특정 파일 변경 시 인덱싱을 요청합니다."""
        self.request_rescan()

    def _enqueue_fsevent(self, _evt: object) -> None:
        """파일 시스템 이벤트를 처리합니다."""
        self.request_rescan()

    def _snapshot_path(self) -> str:
        """임시 스냅샷 DB 파일 경로를 생성합니다."""
        import random
        base = self._safe_db_path()
        if base in ("", ":memory:"):
            tmp_dir = os.path.join(tempfile.gettempdir(), "sari_snapshots")
            os.makedirs(tmp_dir, exist_ok=True)
            base = os.path.join(tmp_dir, "index.db")
        # Add PID and a random suffix to prevent collisions within the same millisecond
        pid = os.getpid()
        rand = random.randint(1000, 9999)
        return f"{base}.snapshot.{int(time.time() * 1000)}.{pid}.{rand}"

    def _safe_db_path(self) -> str:
        """db_path를 안전한 문자열 경로로 정규화합니다."""
        raw = getattr(self.db, "db_path", "")
        if isinstance(raw, str):
            return raw
        if isinstance(raw, Path):
            return str(raw)
        return ""

    def _serialize_config(self) -> dict[str, object]:
        """설정 객체를 직렬화하여 워커 프로세스에 전달합니다. 직렬화 불가능한 필드는 제외합니다."""
        data = {}
        for k, v in self.config.__dict__.items():
            # Skip non-serializable or internal objects
            if k.startswith("_") or hasattr(v, "__call__") or "lock" in k.lower() or "logger" in k.lower():
                continue
            # Ensure path objects are strings
            if hasattr(v, "__fspath__"):
                data[k] = str(v)
            else:
                data[k] = v
        return data

    def _start_worker_scan(self) -> None:
        """별도 프로세스에서 인덱싱 작업을 시작합니다."""
        if self._worker_proc and self._worker_proc.is_alive():
            self._pending_rescan = True
            return
        self._cleanup_stale_snapshot_artifacts()
        self.status.index_ready = False
        self.status.last_error = ""
        self._worker_snapshot_path = self._snapshot_path()
        self._worker_status_path = f"{self.db.db_path}.snapshot.status.json"
        self._worker_log_path = f"{self.db.db_path}.snapshot.log"
        cfg = self._serialize_config()
        ctx = multiprocessing.get_context("spawn")
        self._worker_proc = ctx.Process(
            target=_worker_build_snapshot,
            args=(
                cfg,
                self._worker_snapshot_path,
                self._worker_status_path,
                self._worker_log_path,
                os.getpid()),
            daemon=True)
        self._worker_proc.start()

    def _finalize_worker_if_done(self) -> None:
        """워커 프로세스가 완료되었는지 확인하고 결과를 반영합니다."""
        if not self._worker_proc:
            return
        if self._worker_proc.is_alive():
            return
        exitcode = self._worker_proc.exitcode
        self._worker_proc = None
        status_path = self._worker_status_path
        log_path = self._worker_log_path
        snapshot_path = self._worker_snapshot_path
        self._worker_status_path = None
        self._worker_log_path = None
        self._worker_snapshot_path = None
        if not status_path or not snapshot_path or not os.path.exists(
                status_path):
            self.status.errors += 1
            self.status.last_error = "worker status missing"
            self._cleanup_snapshot_artifacts(snapshot_path)
            self._remove_file_if_exists(log_path)
            return
        try:
            with open(status_path, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except Exception as e:
            self.status.errors += 1
            self.status.last_error = f"worker status read failed: {e}"
            self._cleanup_snapshot_artifacts(snapshot_path)
            self._remove_file_if_exists(status_path)
            self._remove_file_if_exists(log_path)
            return
        if not payload.get("ok"):
            self.status.errors += 1
            self.status.last_error = payload.get("error", "worker failed")
            self._cleanup_snapshot_artifacts(snapshot_path)
            self._remove_file_if_exists(status_path)
            self._remove_file_if_exists(log_path)
            return
        if exitcode not in (0, None):
            self.status.errors += 1
            self.status.last_error = f"worker exit {exitcode}"
            self._cleanup_snapshot_artifacts(snapshot_path)
            self._remove_file_if_exists(status_path)
            self._remove_file_if_exists(log_path)
            return
        status = payload.get("status", {})
        try:
            self.db.swap_db_file(snapshot_path)
            self.status.scan_started_ts = status.get("scan_started_ts", 0)
            self.status.scan_finished_ts = status.get("scan_finished_ts", 0)
            self.status.scanned_files = status.get("scanned_files", 0)
            self.status.indexed_files = status.get("indexed_files", 0)
            self.status.symbols_extracted = status.get("symbols_extracted", 0)
            self.status.errors = status.get("errors", 0)
            self.status.index_version = status.get("index_version", "")
            self.status.index_ready = True
            self._cleanup_snapshot_artifacts(snapshot_path)
        except Exception as e:
            self.status.errors += 1
            self.status.last_error = str(e)
            if self.logger:
                self.logger.error(f"Snapshot swap failed: {e}")
        finally:
            self._remove_file_if_exists(status_path)
            self._remove_file_if_exists(log_path)
        if self._pending_rescan:
            self._pending_rescan = False
            self._start_worker_scan()


class IndexStatus:
    """인덱싱 상태 정보를 저장하는 데이터 클래스입니다."""

    def __init__(self):
        self.index_ready = False
        self.indexed_files = 0
        self.symbols_extracted = 0
        self.scan_started_ts = 0
        self.scan_finished_ts = 0
        self.scanned_files = 0
        self.errors = 0
        self.index_version = ""
        self.last_error = ""

    def to_meta(self) -> dict:
        """상태 정보를 딕셔너리로 변환하여 반환합니다."""
        return {
            "index_ready": bool(self.index_ready),
            "indexed_files": int(self.indexed_files or 0),
            "scanned_files": int(self.scanned_files or 0),
            "index_errors": int(self.errors or 0),
            "symbols_extracted": int(self.symbols_extracted or 0),
            "index_version": self.index_version or "",
            "last_error": self.last_error or "",
            "scan_started_ts": int(self.scan_started_ts or 0),
            "scan_finished_ts": int(self.scan_finished_ts or 0),
        }
