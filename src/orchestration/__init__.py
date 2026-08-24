"""
Orchestration layer: chains each demo's agents together with the
shared memory/database, and is what the API layer and CLIs call into.
Import directly from the submodules (demo1_orchestrator,
demo2_orchestrator, demo3_orchestrator) rather than from this package
root, since each pulls in that demo's own directory via sys.path.
"""
