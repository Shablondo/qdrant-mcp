from __future__ import annotations

import argparse
import json

from rag_sync import get_source_sync_status, get_sync_status, list_sources, sync_sources


def main() -> None:
    parser = argparse.ArgumentParser(description="RAG source sync")
    subparsers = parser.add_subparsers(dest="command", required=True)

    once = subparsers.add_parser("once")
    once.add_argument("--sources-path")
    once.add_argument("--kind", action="append", dest="kinds")
    once.add_argument("--source-id", action="append", dest="source_ids")
    once.add_argument("--stale-after-minutes", type=int)
    once.add_argument("--force", action="store_true")

    list_cmd = subparsers.add_parser("list")
    list_cmd.add_argument("--sources-path")

    status = subparsers.add_parser("status")
    status.add_argument("--kind")
    status.add_argument("--source-id-prefix")

    source_status = subparsers.add_parser("source-status")
    source_status.add_argument("--sources-path")

    args = parser.parse_args()
    if args.command == "once":
        result = sync_sources(
            kinds=args.kinds,
            source_ids=args.source_ids,
            stale_after_minutes=args.stale_after_minutes,
            sources_path=args.sources_path,
            force=args.force,
        )
    elif args.command == "list":
        result = list_sources(args.sources_path)
    elif args.command == "status":
        result = get_sync_status(kind=args.kind, source_id_prefix=args.source_id_prefix)
    else:
        result = get_source_sync_status(sources_path=args.sources_path)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
