"""CLI。サブコマンドは登録済みのデータ源 (invest/sources/*.py) から自動で組み立てる。

例:
    python -m invest boj series FM08 FXERD01 --start 202401
    python -m invest jq bars 7203 --start 2026-01-01
    python -m invest status
"""

import argparse
import json

from . import sources


def main() -> None:
    srcs = sources.all_sources()
    p = argparse.ArgumentParser(prog="invest")
    sub = p.add_subparsers(dest="source", required=True)
    for cli, src in srcs.items():
        sp = sub.add_parser(cli, help=src.help)
        cmds = sp.add_subparsers(dest="cmd", required=True)
        for c in src.commands():
            cp = cmds.add_parser(c.name, help=c.help)
            for flags, kwargs in c.args:
                cp.add_argument(*flags, **kwargs)
            cp.set_defaults(run=c.run)
    sub.add_parser("status", help="キャッシュの状態")

    a = p.parse_args()
    conn = sources.connect()
    if a.source == "status":
        r = {
            "sources": {cli: src.status(conn) for cli, src in srcs.items()},
            "coverage": [dict(x) for x in conn.execute(
                "SELECT source, series, period_start, period_end FROM coverage ORDER BY source, series")],
            "fetch_log": [dict(x) for x in conn.execute(
                "SELECT * FROM fetch_log ORDER BY source, resource")],
        }
    else:
        r = a.run(conn, a)
    print(json.dumps(r, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
