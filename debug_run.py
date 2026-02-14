import sys
import traceback

try:
    import run
    print("Import successful")
    run.main()
    print("Main finished")
except Exception:
    traceback.print_exc()
