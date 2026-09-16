"""Run an explicit simulation or physical acquisition, without the web server."""
import argparse
import json
from pathlib import Path
from scanner_core.pipeline import controller
from scanner_core.runtime_settings import update_settings

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("simulation", "physical"), required=True)
    parser.add_argument("--settings", type=Path)
    parser.add_argument("--captures", type=int)
    parser.add_argument("--calibration", type=Path)
    args = parser.parse_args()
    data = json.loads(args.settings.read_text(encoding="utf-8")) if args.settings else {}
    data.update(mode=args.mode, simulation_mode=args.mode == "simulation")
    if args.captures is not None:
        data["scan_steps"] = args.captures
    if args.calibration:
        data["calibration_path"] = str(args.calibration.resolve())
    _, errors = update_settings(data)
    if errors:
        parser.error("; ".join(errors))
    try:
        session_id = controller.start()
        print(f"Session: {session_id}", flush=True)
        result = controller.wait()
    except KeyboardInterrupt:
        controller.stop()
        result = controller.wait()
    except (ValueError, OSError, RuntimeError) as error:
        parser.exit(1, f"{error}\n")
    print(json.dumps(result, indent=2))
    return 0 if result["phase"] == "completed" else 1

if __name__ == "__main__":
    raise SystemExit(main())
