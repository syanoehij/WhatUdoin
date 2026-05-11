"""WhatUdoin 운영자 진단 도구.

WhatUdoin.exe / python main.py에 `--doctor` sub-command로 노출된다. 회사 운영자가
별도 Python 설치 없이 (a) 사전 점검 (b) 사후 진단 (c) 안전 그룹 자동 정리 (d) unsafe
충돌의 권장 SQL 템플릿을 받을 수 있다.

명령어:
  WhatUdoin.exe --doctor check
      등록된 모든 _PREFLIGHT_CHECKS 순회 + projects 충돌 그룹 진단(자동 가능 vs 운영자
      결정 필요 분류). 운영 DB는 read-only로 열어 검사. 출력은 한국어 ASCII 표.

  WhatUdoin.exe --doctor fix-projects
      안전 정리 dry-run. 어떤 row가 지워질지만 출력, 변경 X.

  WhatUdoin.exe --doctor fix-projects --apply
      자체 백업(run_migration_backup) 호출 후 안전 그룹만 자동 정리.

  WhatUdoin.exe --doctor --help
      사용법.

옵션:
  --db-path PATH   기본은 운영 DB. 합성 DB 검사 시 사용.

unsafe 충돌(users/teams.name_norm)은 자동 정리 안 함. 권장 SQL 템플릿만 출력.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path


# ────────────────────────────────────────────────────────────────
# DB 경로 해석 — main.py와 동일한 _RUN_DIR 규칙.
# database 모듈을 import하면 init_db() 등 부수 효과는 없다(모듈 import만).

def _resolve_default_db_path() -> str:
    run_dir = os.environ.get("WHATUDOIN_RUN_DIR")
    if not run_dir:
        if getattr(sys, "frozen", False):
            run_dir = os.path.dirname(sys.executable)
        else:
            run_dir = os.path.dirname(os.path.abspath(__file__))
            # tools/ 안에서 실행 시 한 단계 위로
            parent = os.path.dirname(run_dir)
            if os.path.isfile(os.path.join(parent, "main.py")):
                run_dir = parent
    return os.path.join(run_dir, "whatudoin.db")


# ────────────────────────────────────────────────────────────────
# 출력 헬퍼 — ASCII 표.

def _fmt_table(headers: list[str], rows: list[list[str]]) -> str:
    if not rows:
        widths = [len(h) for h in headers]
    else:
        widths = [
            max(len(str(headers[i])), *(len(str(r[i])) for r in rows))
            for i in range(len(headers))
        ]
    sep = "+" + "+".join("-" * (w + 2) for w in widths) + "+"

    def _line(cells):
        return "| " + " | ".join(
            str(cells[i]).ljust(widths[i]) for i in range(len(cells))
        ) + " |"

    out = [sep, _line(headers), sep]
    for r in rows:
        out.append(_line(r))
    out.append(sep)
    return "\n".join(out)


# ────────────────────────────────────────────────────────────────
# 진단 — projects (team_id, name_norm) 충돌 그룹.

def _diagnose_projects(conn) -> tuple[list[dict], int, int]:
    """returns (plans, safe_count, unsafe_count).

    각 plan: _classify_projects_dedup_group의 출력.
    """
    from database import (
        _projects_duplicate_groups,
        _classify_projects_dedup_group,
    )
    groups = _projects_duplicate_groups(conn)
    plans = [_classify_projects_dedup_group(conn, g) for g in groups]
    safe = sum(1 for p in plans if p["safe"])
    unsafe = sum(1 for p in plans if not p["safe"])
    return plans, safe, unsafe


def _diagnose_users_teams(conn) -> dict:
    """returns {users: list[dict], teams: list[dict]}.

    각 dict: {name_norm, ids: list[int], names: list[str]}.
    빈 테이블/컬럼 미존재면 [].
    """
    from database import _table_exists, _column_set

    out: dict[str, list[dict]] = {"users": [], "teams": []}
    for tbl in ("users", "teams"):
        if not _table_exists(conn, tbl):
            continue
        if "name_norm" not in _column_set(conn, tbl):
            continue
        # 1) 충돌 name_norm 그룹 식별 (id 목록만).
        rows = conn.execute(
            f"SELECT name_norm, GROUP_CONCAT(id) AS ids "
            f"  FROM {tbl} "
            f" WHERE name_norm IS NOT NULL "
            f" GROUP BY name_norm "
            f"HAVING COUNT(*) > 1 "
            f"ORDER BY name_norm"
        ).fetchall()
        for row in rows:
            name_norm = row["name_norm"] if isinstance(row, sqlite3.Row) else row[0]
            ids_str = row["ids"] if isinstance(row, sqlite3.Row) else row[1]
            ids = sorted(int(x) for x in (ids_str or "").split(",") if x.strip())
            # 2) 각 id의 name을 별도 SELECT로 정확히 가져온다 (이름에 콤마 들어가도 안전).
            names: list[str] = []
            if ids:
                placeholders = ",".join("?" * len(ids))
                name_rows = conn.execute(
                    f"SELECT id, name FROM {tbl} WHERE id IN ({placeholders})",
                    tuple(ids),
                ).fetchall()
                name_by_id = {
                    (r["id"] if isinstance(r, sqlite3.Row) else r[0]):
                        (r["name"] if isinstance(r, sqlite3.Row) else r[1])
                    for r in name_rows
                }
                names = [str(name_by_id.get(pid, "")) for pid in ids]
            out[tbl].append({
                "name_norm": name_norm,
                "ids": ids,
                "names": names,
            })
    return out


def _diagnose_user_ips_whitelist(conn) -> list[dict]:
    """user_ips 의 type='whitelist' ip_address 충돌 진단 (#9).

    같은 IP가 2명 이상에게 whitelist면 부분 UNIQUE 인덱스 생성이 실패한다.
    자동 정리는 하지 않는다 — 안전한 선택 기준이 없다(운영자가 어느 사용자 IP인지 결정).

    각 dict: {ip_address, user_ids: list[int], names: list[str]}. 테이블/컬럼 없으면 [].
    """
    from database import _table_exists, _column_set

    if not _table_exists(conn, "user_ips"):
        return []
    cols = _column_set(conn, "user_ips")
    if "ip_address" not in cols or "type" not in cols:
        return []

    rows = conn.execute(
        "SELECT ip_address, GROUP_CONCAT(user_id) AS uids "
        "  FROM user_ips "
        " WHERE type = 'whitelist' "
        " GROUP BY ip_address "
        "HAVING COUNT(*) > 1 "
        "ORDER BY ip_address"
    ).fetchall()
    out: list[dict] = []
    for row in rows:
        ip = row["ip_address"] if isinstance(row, sqlite3.Row) else row[0]
        uids_str = row["uids"] if isinstance(row, sqlite3.Row) else row[1]
        uids = sorted(int(x) for x in (uids_str or "").split(",") if x.strip())
        names: list[str] = []
        if uids:
            placeholders = ",".join("?" * len(uids))
            name_rows = conn.execute(
                f"SELECT id, name FROM users WHERE id IN ({placeholders})",
                tuple(uids),
            ).fetchall()
            name_by_id = {
                (r["id"] if isinstance(r, sqlite3.Row) else r[0]):
                    (r["name"] if isinstance(r, sqlite3.Row) else r[1])
                for r in name_rows
            }
            names = [str(name_by_id.get(uid, "")) for uid in uids]
        out.append({"ip_address": ip, "user_ids": uids, "names": names})
    return out


# ────────────────────────────────────────────────────────────────
# 명령어: check (read-only).

def cmd_check(db_path: str) -> int:
    print("=" * 60)
    print("  WhatUdoin migration_doctor — check")
    print(f"  DB: {db_path}")
    print("=" * 60)

    if not os.path.exists(db_path):
        print(f"\n[오류] DB 파일을 찾을 수 없습니다: {db_path}")
        return 2

    # read-only mode (uri=True + mode=ro). 락 영향 없음.
    uri = f"file:{Path(db_path).as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        # 1) projects 충돌
        plans, safe_count, unsafe_count = _diagnose_projects(conn)
        print()
        print(f"[1] projects (team_id, name_norm) 충돌: {len(plans)}건")
        if plans:
            rows = []
            for p in plans:
                ref_summary = ",".join(
                    f"{pid}:{p['ref_counts'][pid]}" for pid in p["ids"]
                )
                rows.append([
                    str(p["team_id"]),
                    repr(p["name_norm"]),
                    str(p["ids"]),
                    ref_summary,
                    "safe" if p["safe"] else "unsafe",
                    p["unsafe_reason"] or "",
                ])
            print(_fmt_table(
                ["team_id", "name_norm", "ids", "id:refs", "분류", "사유"],
                rows,
            ))
            print(f"  → 자동 정리 가능: {safe_count}건  운영자 결정 필요: {unsafe_count}건")
            if safe_count:
                print("  → 자동 정리: WhatUdoin.exe --doctor fix-projects --apply")
            if unsafe_count:
                print("  → unsafe 그룹은 운영자가 직접 처리 후 재시작.")
        else:
            print("  → 이상 없음.")

        # 2) users.name_norm / teams.name_norm 충돌 (자동 정리 X)
        ut = _diagnose_users_teams(conn)
        print()
        print(f"[2] users.name_norm 충돌: {len(ut['users'])}건")
        if ut["users"]:
            rows = []
            for c in ut["users"]:
                rows.append([
                    repr(c["name_norm"]),
                    str(c["ids"]),
                    ",".join(c["names"]),
                ])
            print(_fmt_table(["name_norm", "ids", "names"], rows))
            print("  권장 SQL 템플릿(운영자가 의도 결정 후 실행):")
            for c in ut["users"]:
                for pid in c["ids"]:
                    print(f"    UPDATE users SET name = ?, name_norm = ? WHERE id = {pid};")
            print("  또는 합병 대상 row 삭제:")
            for c in ut["users"]:
                if len(c["ids"]) > 1:
                    print(f"    DELETE FROM users WHERE id = {c['ids'][-1]};  -- 합병 의사결정 후")

        print()
        print(f"[3] teams.name_norm 충돌: {len(ut['teams'])}건")
        if ut["teams"]:
            rows = []
            for c in ut["teams"]:
                rows.append([
                    repr(c["name_norm"]),
                    str(c["ids"]),
                    ",".join(c["names"]),
                ])
            print(_fmt_table(["name_norm", "ids", "names"], rows))
            print("  권장 SQL 템플릿:")
            for c in ut["teams"]:
                for pid in c["ids"]:
                    print(f"    UPDATE teams SET name = ?, name_norm = ? WHERE id = {pid};")

        # 4) user_ips whitelist 충돌 (자동 정리 X — #9)
        ip_conflicts = _diagnose_user_ips_whitelist(conn)
        print()
        print(f"[4] user_ips whitelist(ip_address) 충돌: {len(ip_conflicts)}건")
        if ip_conflicts:
            rows = []
            for c in ip_conflicts:
                rows.append([
                    c["ip_address"],
                    str(c["user_ids"]),
                    ",".join(c["names"]),
                ])
            print(_fmt_table(["ip_address", "user_ids", "names"], rows))
            print("  권장 SQL 템플릿(운영자가 어느 사용자 IP인지 결정 후 실행):")
            for c in ip_conflicts:
                # 한 명만 whitelist로 남기고 나머지는 history로 강등(이력 보존). row 삭제도 가능.
                if len(c["user_ids"]) > 1:
                    keep = c["user_ids"][0]
                    print(
                        f"    UPDATE user_ips SET type='history' "
                        f"WHERE ip_address = {c['ip_address']!r} AND type='whitelist' AND user_id != {keep};"
                        f"  -- user_id={keep} 만 자동 로그인 유지"
                    )

        # 종합 결과
        print()
        has_any = bool(plans or ut["users"] or ut["teams"] or ip_conflicts)
        if not has_any:
            print("종합: 이상 없음. 마이그레이션 진입 가능.")
            return 0
        if unsafe_count or ut["users"] or ut["teams"] or ip_conflicts:
            print("종합: 운영자 결정이 필요한 충돌이 있습니다.")
            return 1
        print("종합: 자동 정리 가능한 충돌만 남아 있습니다. fix-projects --apply 권장.")
        return 0
    finally:
        conn.close()


# ────────────────────────────────────────────────────────────────
# 명령어: fix-projects (dry-run / --apply).

def cmd_fix_projects(db_path: str, apply: bool) -> int:
    mode = "APPLY" if apply else "DRY-RUN"
    print("=" * 60)
    print(f"  WhatUdoin migration_doctor — fix-projects ({mode})")
    print(f"  DB: {db_path}")
    print("=" * 60)

    if not os.path.exists(db_path):
        print(f"\n[오류] DB 파일을 찾을 수 없습니다: {db_path}")
        return 2

    if apply:
        # 자체 백업 (run_migration_backup). 백업 실패 시 정리 거부.
        try:
            from backup import run_migration_backup
            backup_dir = Path(db_path).parent
            backup_path = run_migration_backup(db_path, backup_dir)
            print(f"  사전 백업: {backup_path}")
        except Exception as exc:
            print(f"\n[오류] 백업 실패 — 정리 중단: {exc!r}")
            return 2

    # dry-run은 read-only, apply는 read-write + 매뉴얼 트랜잭션.
    if apply:
        conn = sqlite3.connect(db_path, timeout=5)
        # default isolation_level=""은 DML 직전 자동 BEGIN을 발행해
        # 직접 BEGIN IMMEDIATE 호출이 "cannot start a transaction within a transaction"
        # 에러를 낸다. 매뉴얼 트랜잭션을 위해 None으로 설정.
        conn.isolation_level = None
    else:
        uri = f"file:{Path(db_path).as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=5)
    conn.row_factory = sqlite3.Row

    try:
        plans, safe_count, unsafe_count = _diagnose_projects(conn)
        if not plans:
            print("\n충돌 그룹 없음. 정리할 항목이 없습니다.")
            return 0

        print(f"\n충돌 그룹: {len(plans)}건  (자동: {safe_count}, 운영자 결정: {unsafe_count})\n")

        rows = []
        delete_total = 0
        for p in plans:
            if not p["safe"]:
                rows.append([
                    str(p["team_id"]), repr(p["name_norm"]),
                    str(p["ids"]), "[]", str(p["ids"]), p["unsafe_reason"] or "",
                ])
                continue
            rows.append([
                str(p["team_id"]), repr(p["name_norm"]),
                str(p["ids"]), str(p["delete"]), str(p["keep"]), "",
            ])
            delete_total += len(p["delete"])

        print(_fmt_table(
            ["team_id", "name_norm", "before", "DELETE", "KEEP", "사유"],
            rows,
        ))

        if delete_total == 0:
            print("\n자동 정리할 row가 없습니다. unsafe 그룹은 운영자 직접 처리.")
            return 0

        if not apply:
            print(f"\n[DRY-RUN] {delete_total}개 row가 정리 대상입니다.")
            print("실제 실행: --apply 추가")
            return 0

        # APPLY — 단일 트랜잭션으로 실행.
        deleted = 0
        try:
            conn.execute("BEGIN IMMEDIATE")
            for p in plans:
                if not p["safe"] or not p["delete"]:
                    continue
                placeholders = ",".join("?" * len(p["delete"]))
                conn.execute(
                    f"DELETE FROM projects WHERE id IN ({placeholders})",
                    tuple(p["delete"]),
                )
                deleted += len(p["delete"])
            conn.execute("COMMIT")
        except Exception as exc:
            conn.execute("ROLLBACK")
            print(f"\n[오류] 정리 실패, ROLLBACK: {exc!r}")
            return 2

        print(f"\n[APPLY] {deleted}개 row 정리 완료. 백업으로 복구 가능.")
        return 0
    finally:
        conn.close()


# ────────────────────────────────────────────────────────────────
# 진입점.

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="WhatUdoin --doctor",
        description="WhatUdoin 마이그레이션 진단·정리 도구",
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help="DB 파일 경로 (기본: 운영 DB whatudoin.db)",
    )
    sub = parser.add_subparsers(dest="cmd", required=False)

    sub.add_parser("check", help="진단(read-only)")

    fix = sub.add_parser("fix-projects", help="projects 안전 그룹 자동 정리")
    fix.add_argument(
        "--apply",
        action="store_true",
        help="실제 정리 수행 (없으면 dry-run)",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    db_path = args.db_path or _resolve_default_db_path()

    cmd = args.cmd or "check"
    if cmd == "check":
        return cmd_check(db_path)
    if cmd == "fix-projects":
        return cmd_fix_projects(db_path, apply=args.apply)
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
