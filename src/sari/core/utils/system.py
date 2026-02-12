import psutil
import os
import threading
from typing import TypeAlias

MetricMap: TypeAlias = dict[str, object]
ProcessInfo: TypeAlias = dict[str, object]

def get_system_metrics() -> MetricMap:
    """Returns a dictionary of current system resource usage."""
    try:
        cpu_percent = psutil.cpu_percent(interval=None)
        mem = psutil.virtual_memory()
        try:
            disk = psutil.disk_usage(os.getcwd())
            disk_percent = disk.percent
        except Exception:
            disk_percent = 0.0
        
        process = psutil.Process(os.getpid())
        with process.oneshot():
            proc_mem_mb = process.memory_info().rss / (1024**2)
            proc_cpu_percent = process.cpu_percent(interval=None)
            thread_count = process.num_threads()

        return {
            "cpu_percent": cpu_percent,
            "memory_percent": mem.percent,
            "memory_used_gb": round(mem.used / (1024**3), 2),
            "memory_total_gb": round(mem.total / (1024**3), 2),
            "disk_percent": disk_percent,
            "process_memory_mb": round(proc_mem_mb, 1),
            "process_cpu_percent": proc_cpu_percent,
            "process_thread_count": thread_count,
            "active_threads": threading.active_count()
        }
    except Exception:
        return {"error": "Failed to collect metrics"}

def list_sari_processes() -> list[ProcessInfo]:
    """Lists all running Sari-related processes."""
    procs: list[ProcessInfo] = []
    my_pid = os.getpid()
    for proc in psutil.process_iter(['pid', 'name', 'cmdline', 'create_time']):
        try:
            cmdline = proc.info.get('cmdline') or []
            cmd_str = " ".join(cmdline).lower()
            name = str(proc.info.get("name") or "")
            # Filter for Sari related processes
            if "sari" in cmd_str and ("python" in cmd_str or "sari" in name.lower()):
                memory_mb = 0.0
                try:
                    memory_mb = round(proc.memory_info().rss / (1024**2), 1)
                except Exception:
                    try:
                        info_mem = proc.info.get("memory_info")
                        memory_mb = round(float(getattr(info_mem, "rss", 0) or 0) / (1024**2), 1)
                    except Exception:
                        memory_mb = 0.0
                procs.append({
                    "pid": proc.info['pid'],
                    "name": name,
                    "cmd": " ".join(cmdline),
                    "created": proc.info['create_time'],
                    "memory_mb": memory_mb,
                    "is_self": proc.info['pid'] == my_pid
                })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return sorted(procs, key=lambda x: x['created'])

def kill_sari_process(pid: int) -> bool:
    """Terminates a specific process by PID."""
    try:
        if pid == os.getpid():
            return False # Don't suicide via this API
        proc = psutil.Process(pid)
        proc.terminate()
        wait = getattr(proc, "wait", None)
        if callable(wait):
            try:
                wait(timeout=0.8)
            except Exception:
                killer = getattr(proc, "kill", None)
                if callable(killer):
                    killer()
                try:
                    wait(timeout=0.8)
                except Exception:
                    return False
        is_running = getattr(proc, "is_running", None)
        if callable(is_running):
            try:
                return not bool(is_running())
            except Exception:
                return True
        return True
    except Exception:
        return False
