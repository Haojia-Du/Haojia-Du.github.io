"""Run a user's existing PKU-Recorder with a lightweight status endpoint."""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path


def main() -> None:
    path = Path(sys.argv[1]).resolve()
    os.environ["PKU_QT_MODE"] = "1"
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("yanxue_recorder", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载 PKU-Recorder。")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    frozen = getattr(sys, "frozen", False)
    try:
        # External source must keep its own APP_DIR/settings instead of using our executable directory.
        sys.frozen = False
        spec.loader.exec_module(module)
    finally:
        sys.frozen = frozen
    if "/api/bridge/status" not in {rule.rule for rule in module.app.url_map.iter_rules()}:
        from flask import jsonify

        @module.app.route("/api/bridge/status")
        def bridge_status():
            jobs = module.job_snapshot()
            return jsonify(ok=True, logged_in=module.manager.is_logged_in(),
                           username=module.manager.username, semester=module.manager.semester,
                           settings=module.SETTINGS, jobs=jobs,
                           stats={"total": len(jobs), "done": sum(j.get("status") == "done" for j in jobs)})
    module.load_settings()
    module.load_jobs()
    module.app.run(host="127.0.0.1", port=5000, threaded=True)


if __name__ == "__main__":
    main()
