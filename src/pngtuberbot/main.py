import argparse
import asyncio
import logging
from pathlib import Path
import sys

from pngtuberbot.config import ConfigError, load_config
from pngtuberbot.state import PNGTuberBotRuntime


def _default_config_path() -> Path:
    # When bundled with PyInstaller, default to config.yaml next to the EXE.
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().with_name("config.yaml")
    return Path("config.yaml").resolve()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pngtuberbot")
    parser.add_argument(
        "--config",
        default=None,
        help="Path to config.yaml (default: next to the EXE / current folder)",
    )
    args = parser.parse_args(argv)

    config_path = Path(args.config).resolve() if args.config else _default_config_path()

    log = logging.getLogger("pngtuberbot")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log.info("Starting PNGTuberBot (config=%s)", config_path)

    try:
        cfg = load_config(config_path)
    except ConfigError as ce:
        logging.basicConfig(level=logging.INFO, format="%(message)s", force=True)
        for err in ce.errors:
            log.error("Config error: %s", err)
        return 2

    logging.basicConfig(
        level=getattr(logging, cfg.advanced.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )
    log.info("Config loaded OK. Launching runtime...")

    runtime = PNGTuberBotRuntime(cfg)
    try:
        asyncio.run(runtime.run())
    except KeyboardInterrupt:
        log.info("Shutting down...")
        return 0
    return 0


