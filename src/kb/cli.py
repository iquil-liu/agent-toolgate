"""Knowledge index CLI: rebuild, stats and search."""

import argparse
import json

from .index import KnowledgeIndex


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("rebuild")
    commands.add_parser("stats")
    search = commands.add_parser("search")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=3)
    search.add_argument("--mode", choices=("hybrid", "baseline"), default="hybrid")
    args = parser.parse_args()
    with KnowledgeIndex() as index:
        if args.command == "rebuild":
            result = index.rebuild()
        elif args.command == "stats":
            result = index.stats()
        else:
            result = index.search(args.query, args.limit, args.mode)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
