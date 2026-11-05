"""Command line entry points for image scanning and automatic camera capture."""

import argparse
import json
import logging
import os
import sys
import traceback
from collections.abc import Sequence
from pathlib import Path

from . import __version__
from .camera import list_cameras
from .imaging import read_image
from .pdf import save_receipt_pdf
from .pipeline import Pipeline, scan_image
from .scanner import ReceiptScanner, capture_name
from .sinks import JsonlSink, Sink, StdoutSink, WebhookSink

logger = logging.getLogger(__name__)


def _output_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--extractor", choices=("auto", "einvoice", "ocr"), default="auto")
    parser.add_argument("--webhook", default=os.environ.get("EZRECEIPT_WEBHOOK_URL"))
    parser.add_argument("--webhook-token", default=os.environ.get("EZRECEIPT_WEBHOOK_TOKEN"))
    parser.add_argument("--jsonl")
    parser.add_argument("--save-pdf", metavar="DIR", help="另存裁切後、白色背景的收據 PDF")
    parser.add_argument("--stdout", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    _templates_dir_option(parser)
    templates = parser.add_mutually_exclusive_group()
    templates.add_argument("--template", metavar="ID")
    templates.add_argument("--no-templates", action="store_true")


def _templates_dir_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--templates-dir", metavar="DIR", default=os.environ.get("EZRECEIPT_TEMPLATES_DIR")
    )


def _pipeline_options(args: argparse.Namespace) -> dict:
    return {
        "mode": args.extractor,
        "templates_dir": args.templates_dir,
        "template": args.template,
        "use_templates": not args.no_templates,
    }


def _template_commands(commands: argparse._SubParsersAction) -> None:
    template = commands.add_parser("template", help="建立、測試與管理收據模板")
    actions = template.add_subparsers(dest="template_command", required=True)
    for action in ("list", "show", "test", "copy-builtin"):
        command = actions.add_parser(action)
        command.add_argument("-v", "--verbose", action="store_true")
        _templates_dir_option(command)
        if action != "list":
            command.add_argument("id")
        if action == "test":
            command.add_argument("image")
    validate = actions.add_parser("validate")
    validate.add_argument("-v", "--verbose", action="store_true")
    validate.add_argument("path")
    new = actions.add_parser("new")
    new.add_argument("-v", "--verbose", action="store_true")
    source = new.add_mutually_exclusive_group(required=True)
    source.add_argument("--image")
    source.add_argument("--camera", type=int)
    _templates_dir_option(new)
    new.add_argument("--id")
    new.add_argument("--name")


def _template_command(args: argparse.Namespace) -> int:
    from .templates.annotate import new_template
    from .templates.model import load_template
    from .templates.registry import TemplateRegistry, copy_builtin

    action = args.template_command
    if action == "validate":
        template = load_template(args.path)
        print(f"OK: {template.id}")
        return 0
    if action in {"new", "copy-builtin"} and not args.templates_dir:
        raise ValueError("請指定 --templates-dir 或 EZRECEIPT_TEMPLATES_DIR")
    if action == "copy-builtin":
        print(copy_builtin(args.id, args.templates_dir))
        return 0
    if action == "new":
        path = new_template(
            image=args.image,
            camera=args.camera,
            templates_dir=args.templates_dir,
            template_id=args.id,
            name=args.name,
        )
        if path:
            print(path)
        return 0 if path else 1
    registry = TemplateRegistry(args.templates_dir)
    if action == "list":
        for template in registry.templates:
            print(f"{template.id}\t{template.name}\t{'user' if template.user else 'builtin'}")
        return 0
    template = registry.get(args.id)
    if action == "show":
        print(json.dumps(template.data, ensure_ascii=False, indent=2))
        return 0
    from .templates.annotate import read_image

    pipeline = Pipeline("ocr", templates=registry, template=args.id)
    receipt = pipeline.process(read_image(args.image))
    print(
        json.dumps(
            {
                "receipt": receipt.to_payload() if receipt else None,
                "strategies": pipeline.last_strategy_report,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if receipt else 1


def _sinks(args: argparse.Namespace, always_stdout: bool = False) -> list[Sink]:
    sinks: list[Sink] = []
    if args.webhook:
        sinks.append(WebhookSink(args.webhook, args.webhook_token))
    if args.jsonl:
        sinks.append(JsonlSink(args.jsonl))
    if args.stdout or always_stdout or not sinks:
        sinks.append(StdoutSink())
    return sinks


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ezreceipt", description="離線收據辨識與自動擷取")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    watch = commands.add_parser("watch", help="自動擷取攝影機或影片中的收據")
    source = watch.add_mutually_exclusive_group()
    source.add_argument("--camera", type=int, default=0)
    source.add_argument("--source", help="影片檔路徑")
    watch.add_argument("--width", type=int, default=1920)
    watch.add_argument("--height", type=int, default=1080)
    watch.add_argument("--backend", choices=("auto", "msmf", "dshow"), default="auto")
    watch.add_argument("--save-images", metavar="DIR")
    watch.add_argument("--no-preview", action="store_true")
    watch.add_argument("--dedupe-window", type=float, default=600)
    _output_options(watch)
    scan = commands.add_parser("scan", help="辨識圖片並輸出 JSON")
    scan.add_argument("images", nargs="+")
    _output_options(scan)
    commands.add_parser("cameras", help="列出可開啟的攝影機索引")
    _template_commands(commands)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8")
    args = _parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if getattr(args, "verbose", False) else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    try:
        if args.command == "cameras":
            for index in list_cameras():
                print(index)
            return 0
        if args.command == "template":
            return _template_command(args)
        sinks = _sinks(args, always_stdout=args.command == "scan")
        if args.command == "watch":
            if args.width <= 0 or args.height <= 0:
                raise ValueError("width and height must be positive")
            source = args.source if args.source is not None else args.camera
            scanner = ReceiptScanner(
                source=source,
                pipeline=Pipeline(**_pipeline_options(args)),
                sinks=sinks,
                dedupe_window_s=args.dedupe_window,
                save_images_dir=args.save_images,
                save_pdf_dir=args.save_pdf,
                width=args.width,
                height=args.height,
                backend=args.backend,
            )
            scanner.run(preview=not args.no_preview)
            return 0
        read_any, failed = False, False
        for path in args.images:
            try:
                image = read_image(path)
                receipt = scan_image(image, **_pipeline_options(args))
                if receipt is None:
                    logger.warning("無法辨識：%s", path)
                    continue
                read_any = True
                if args.save_pdf:
                    try:
                        pdf_path = Path(args.save_pdf) / capture_name(receipt, ".pdf")
                        receipt.pdf_file = str(save_receipt_pdf(image, pdf_path))
                    except Exception:
                        failed = True
                        receipt.warnings.append("pdf_save_failed")
                        logger.exception("無法儲存 PDF：%s", path)
                for sink in sinks:
                    try:
                        sink.send(receipt)
                    except Exception:
                        failed = True
                        logger.exception("傳送失敗：%s", type(sink).__name__)
            except (OSError, ValueError):
                failed = True
                logger.exception("無法讀取圖片：%s", path)
        return 0 if read_any and not failed else 1
    except (OSError, ValueError, RuntimeError) as exc:
        if args.command == "template":
            reason = " ".join(str(exc).splitlines())
            if args.template_command == "validate":
                path_prefix = f"{args.path}: "
                message = f"INVALID: {path_prefix}{reason.removeprefix(path_prefix)}"
            else:
                message = f"ERROR: {reason}"
            print(message, file=sys.stderr)
            if args.verbose:
                traceback.print_exc()
        else:
            logger.exception("無法啟動 EzReceipt")
        return 1
    except KeyboardInterrupt:
        return 130
