"""
Hermit Purple CLI Shim
Backwards compatibility wrapper for workflows expecting `src.cli`.
Redirects to hermit-purple Interface.
"""
import sys
from .interface.cli import app

if __name__ == "__main__":
    if "health" in sys.argv:
        # Shim for 'health' command if arguments differ, 
        # but Typer should handle it if names match.
        pass
    
    app()
