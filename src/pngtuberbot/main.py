import argparse
import asyncio
import logging
from pathlib import Path

from .config import ConfigError, load_config
from .state import PNGTuberBotRuntime


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pngtuberbot")
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to config.yaml (default: ./config.yaml)",
    )
    args = parser.parse_args(argv)

    config_path = Path(args.config)

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


