import sys
import subprocess

REQUIRED_LIBRARIES = ["peewee", "tenacity", "filelock", "psutil"]

def check_dependencies():
    print("Verifying infrastructure dependencies...")
    missing = []
    for lib in REQUIRED_LIBRARIES:
        try:
            __import__(lib)
        except ImportError:
            missing.append(lib)
    
    if missing:
        print(f"Missing libraries: {', '.join(missing)}")
        try:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", "-r", "requirements.txt"],
                timeout=120.0,
            )
            print("Dependencies installed successfully.")
        except subprocess.TimeoutExpired:
            print("Error installing dependencies: pip install timed out")
            sys.exit(1)
        except subprocess.CalledProcessError as e:
            print(f"Error installing dependencies: pip exited with {e.returncode}")
            sys.exit(1)
        except OSError as e:
            print(f"Error installing dependencies: {e}")
            sys.exit(1)
    else:
        print("All dependencies are already satisfied.")

def setup_binary():
    # Logic to setup CLI command (sari)
    print("Configuring Sari CLI...")
    # ... existing binary setup logic ...

def main():
    check_dependencies()
    # ... rest of the installation logic ...
    print("Sari installation finished successfully.")

if __name__ == "__main__":
    main()
