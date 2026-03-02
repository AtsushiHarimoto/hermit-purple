"""
VibeDev CLI Shim
Backwards compatibility wrapper for workflows expecting `src.cli`.
Redirects to VibeDev 2.0 Interface.
"""
import sys
from .interface.cli import app

if __name__ == "__main__":
    if "health" in sys.argv:
        # Shim for 'health' command if arguments differ, 
        # but Typer should handle it if names match.
        pass
    
    app()
