#!/usr/bin/env python3
"""
Deckard CLI - Command-line interface for daemon management.

Usage:
    deckard daemon start [-d]   Start daemon (foreground or daemonized)
    deckard daemon stop         Stop running daemon
    deckard daemon status       Check daemon status
    deckard proxy               Run in proxy mode (stdio ↔ daemon)
"""
import argparse
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 47779
PID_FILE = Path.home() / ".local" / "share" / "deckard" / "daemon.pid"


def get_daemon_address():
    """Get daemon host and port from environment or defaults."""
    host = os.environ.get("DECKARD_DAEMON_HOST", DEFAULT_HOST)
    port = int(os.environ.get("DECKARD_DAEMON_PORT", DEFAULT_PORT))
    return host, port


def is_daemon_running(host: str, port: int) -> bool:
    """Check if daemon is running by attempting connection."""
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except (ConnectionRefusedError, OSError):
        return False


def read_pid() -> Optional[int]:
    """Read PID from pidfile."""
    if PID_FILE.exists():
        try:
            return int(PID_FILE.read_text().strip())
        except (ValueError, OSError):
            pass
    return None


def remove_pid() -> None:
    """Remove pidfile manually (if daemon crashed)."""
    if PID_FILE.exists():
        PID_FILE.unlink()


def cmd_daemon_start(args):
    """Start the daemon."""
    host, port = get_daemon_address()
    
    if is_daemon_running(host, port):
        print(f"✅ Daemon already running on {host}:{port}")
        return 0
    
    repo_root = Path(__file__).parent.parent.resolve()
    
    if args.daemonize:
        # Start in background
        print(f"Starting daemon on {host}:{port} (background)...")
        
        proc = subprocess.Popen(
            [sys.executable, "-m", "mcp.daemon"],
            cwd=repo_root,
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        
        # PID file will be written by the daemon process itself
        
        # Wait for startup
        for _ in range(30):
            if is_daemon_running(host, port):
                print(f"✅ Daemon started (PID: {proc.pid})")
                return 0
            time.sleep(0.1)
        
        print("❌ Daemon failed to start", file=sys.stderr)
        return 1
    else:
        # Start in foreground
        print(f"Starting daemon on {host}:{port} (foreground, Ctrl+C to stop)...")
        
        try:
            # Import and run directly
            from .daemon import main as daemon_main
            import asyncio
            asyncio.run(daemon_main())
        except KeyboardInterrupt:
            print("\nDaemon stopped.")
        
        return 0


def cmd_daemon_stop(args):
    """Stop the daemon."""
    host, port = get_daemon_address()
    
    if not is_daemon_running(host, port):
        print("Daemon is not running")
        remove_pid()
        return 0
    
    pid = read_pid()
    
    if pid:
        try:
            os.kill(pid, signal.SIGTERM)
            print(f"Sent SIGTERM to PID {pid}")
            
            # Wait for shutdown
            for _ in range(30):
                if not is_daemon_running(host, port):
                    print("✅ Daemon stopped")
                    remove_pid()
                    return 0
                time.sleep(0.1)
            
            # Force kill
            print("Daemon not responding, sending SIGKILL...")
            os.kill(pid, signal.SIGKILL)
            remove_pid()
            return 0
            
        except ProcessLookupError:
            print("PID not found, daemon may have crashed")
            remove_pid()
            return 0
    else:
        print("No PID file found. Try stopping manually.")
        return 1


def cmd_daemon_status(args):
    """Check daemon status."""
    host, port = get_daemon_address()
    
    running = is_daemon_running(host, port)
    pid = read_pid()
    
    print(f"Host: {host}")
    print(f"Port: {port}")
    print(f"Status: {'🟢 Running' if running else '⚫ Stopped'}")
    
    if pid:
        print(f"PID: {pid}")
    
    if running:
        # Try to get workspace info
        try:
            with socket.create_connection((host, port), timeout=1.0) as sock:
                # Send a status request
                import json
                request = json.dumps({
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "status", "arguments": {"details": True}}
                }) + "\n"
                sock.sendall(request.encode())
                
                # We'd need an initialize first, so just skip detailed status for now
        except Exception:
            pass
    
    return 0 if running else 1


def cmd_proxy(args):
    """Run in proxy mode (for MCP stdio)."""
    from .proxy import main as proxy_main
    proxy_main()


def main():
    parser = argparse.ArgumentParser(
        prog="deckard",
        description="Deckard - Local Search MCP Server"
    )
    
    subparsers = parser.add_subparsers(dest="command", help="Commands")
    
    # daemon subcommand
    daemon_parser = subparsers.add_parser("daemon", help="Daemon management")
    daemon_sub = daemon_parser.add_subparsers(dest="daemon_command")
    
    # daemon start
    start_parser = daemon_sub.add_parser("start", help="Start daemon")
    start_parser.add_argument("-d", "--daemonize", action="store_true",
                              help="Run in background")
    start_parser.set_defaults(func=cmd_daemon_start)
    
    # daemon stop
    stop_parser = daemon_sub.add_parser("stop", help="Stop daemon")
    stop_parser.set_defaults(func=cmd_daemon_stop)
    
    # daemon status
    status_parser = daemon_sub.add_parser("status", help="Check status")
    status_parser.set_defaults(func=cmd_daemon_status)
    
    # proxy subcommand
    proxy_parser = subparsers.add_parser("proxy", help="Run in proxy mode")
    proxy_parser.set_defaults(func=cmd_proxy)
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return 1
    
    if args.command == "daemon" and not args.daemon_command:
        daemon_parser.print_help()
        return 1
    
    if hasattr(args, "func"):
        return args.func(args)
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
