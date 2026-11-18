"""Command line: the same import/validation code paths the API and worker use."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import config, db, jobs, sources


def _p(x):
    print(json.dumps(x, ensure_ascii=False, indent=2, default=str))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="studio", description="Creative Studio CLI")
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("import", help="импорт файлов")
    a.add_argument("files", nargs="+")
    a.add_argument("--meta", help="JSON с полями источника (title, author, purpose, usage_status, provenance, url, collections…)")
    a.add_argument("--attach", help="продолжить существующую карточку (source_id) этим файлом")
    a.add_argument("--test", action="store_true", help="пометить как тестовый материал")

    a = sub.add_parser("import-url", help="импорт ссылок (по одной в аргументе или файл со списком через @file)")
    a.add_argument("urls", nargs="+")
    a.add_argument("--meta")

    a = sub.add_parser("note", help="заметка")
    a.add_argument("title")
    a.add_argument("text")

    a = sub.add_parser("import-analysis", help="импорт структурированного анализа источника (JSON)")
    a.add_argument("source_id")
    a.add_argument("file")
    a.add_argument("--dry-run", action="store_true")

    a = sub.add_parser("import-project", help="импорт документов проекта (JSON)")
    a.add_argument("project_id")
    a.add_argument("file")

    a = sub.add_parser("import-card", help="принять отредактированную карточку знания (JSON-экспорт) с проверкой версии")
    a.add_argument("file")

    sub.add_parser("worker", help="запустить worker (бесконечный цикл)")
    a = sub.add_parser("work-once", help="выполнить все готовые задачи и выйти")
    sub.add_parser("status", help="сводка")
    a = sub.add_parser("source", help="карточка источника")
    a.add_argument("source_id")
    a = sub.add_parser("search")
    a.add_argument("query")
    a = sub.add_parser("pack", help="пакет project-generation")
    a.add_argument("project_id")
    a = sub.add_parser("export", help="экспорт проекта в ZIP")
    a.add_argument("project_id")
    a.add_argument("--mode", choices=["links", "portable"], default="portable")
    a = sub.add_parser("backup")
    a.add_argument("--dest", help="внешняя папка для копии")
    a = sub.add_parser("restore-check", help="восстановить копию в отдельную папку и проверить")
    a.add_argument("backup_dir")
    a.add_argument("target_dir")
    sub.add_parser("reexport", help="пересобрать Markdown/JSON-экспорты знаний, INDEX и playbooks")
    sub.add_parser("seed", help="начальные бренды и настройки владельца (идемпотентно)")
    a = sub.add_parser("retry")
    a.add_argument("job_id")
    a = sub.add_parser("rerun", help="принудительно повторить этап источника (например после исправления обработчика)")
    a.add_argument("source_id")
    a.add_argument("stage")
    a = sub.add_parser("replan", help="добавить новые этапы обработки (после обновления студии) к существующим источникам")
    a.add_argument("source_ids", nargs="*", help="пусто = все источники с файлом")
    a = sub.add_parser("dense", help="плотный проход кадров+OCR по интервалу (короткие вставки)")
    a.add_argument("source_id")
    a.add_argument("t0", type=float)
    a.add_argument("t1", type=float)
    a.add_argument("--step", type=float, default=0.1)
    a = sub.add_parser("coverage-report", help="таблицы фактического покрытия (stdout и docs/COVERAGE.md)")
    a.add_argument("source_id", nargs="?")
    a = sub.add_parser("export-skill", help="экспорт переносимого творческого навыка (папка + ZIP) с проверкой")
    a.add_argument("--name", default="dolishx-creative-method")
    a = sub.add_parser("check-skill", help="проверить самостоятельность папки навыка (любая копия)")
    a.add_argument("package_dir")
    a = sub.add_parser("check-skill-output", help="механическая проверка работы, созданной по навыку")
    a.add_argument("package_dir")
    a.add_argument("output_dir")
    a = sub.add_parser("site-connect", help="подключить сайт-кабинет: адрес + ключ студии (ключ — в Keychain)")
    a.add_argument("url")
    a = sub.add_parser("site-snapshot", help="собрать снимок dolishx-site-data/2 в файл (без отправки)")
    a.add_argument("--out", default=None)
    sub.add_parser("sync", help="синхронизация с сайтом: снимок, heartbeat, задачи «Изучить» (бесконечный цикл)")
    sub.add_parser("sync-once", help="один проход синхронизации (дожидается текущей задачи)")
    a = sub.add_parser("cancel")
    a.add_argument("job_id")
    sub.add_parser("review-scan", help="«Ревью»: (пере)сканировать сгенерированные ассеты (config/review_sets.json)")
    a = sub.add_parser("review-feedback", help="«Ревью»: решения и запросы владельца для Claude (JSON)")
    a.add_argument("--since", default=None, help="ISO-время: только решения после него")
    a.add_argument("--all", action="store_true", help="включая уже обработанные")
    a = sub.add_parser("review-handled", help="«Ревью»: отметить запрос выполненным")
    a.add_argument("item_id")
    a.add_argument("note")

    args = ap.parse_args(argv)
    con = db.open_db()

    if args.cmd == "import":
        meta = json.loads(args.meta) if args.meta else {}
        if args.test:
            meta["is_test"] = True
        for f in args.files:
            r = sources.import_file(con, Path(f), meta, attach_to=args.attach)
            if "source" in r:
                s = r["source"]
                print(f"{'ДУБЛЬ ' if r['duplicate'] else ''}{s['id']}  {s['kind']}  {s['original_name']}  новых задач: {len(r['created_jobs'])}")
            else:
                _p(r)
    elif args.cmd == "import-url":
        meta = json.loads(args.meta) if args.meta else {}
        urls = []
        for u in args.urls:
            urls += [l.strip() for l in Path(u[1:]).read_text().splitlines() if l.strip()] if u.startswith("@") else [u]
        for u in urls:
            r = sources.import_url(con, u, meta)
            if "account" in r:
                print(f"{'ДУБЛЬ ' if r['duplicate'] else ''}АККАУНТ {r['account']['id']} @{r['account']['handle']}")
            else:
                print(f"{'ДУБЛЬ ' if r['duplicate'] else ''}{r['source']['id']}  {r['source']['url']}")
    elif args.cmd == "note":
        _p(sources.add_note(con, args.title, args.text)["source"]["id"])
    elif args.cmd == "import-analysis":
        from . import results
        r = results.import_analysis(con, args.source_id, json.loads(Path(args.file).read_text()), dry_run=args.dry_run)
        _p(r)
        sys.exit(0 if r["ok"] else 2)
    elif args.cmd == "import-project":
        from . import projects
        r = projects.import_docs(con, args.project_id, json.loads(Path(args.file).read_text()))
        _p(r)
        sys.exit(0 if r["ok"] else 2)
    elif args.cmd == "import-card":
        from . import knowledge
        r = knowledge.import_card_file(con, Path(args.file))
        _p(r)
        sys.exit(0 if r["ok"] else 2)
    elif args.cmd == "worker":
        jobs.worker_loop()
    elif args.cmd == "work-once":
        jobs.worker_loop(once=True)
    elif args.cmd == "status":
        _p({"data_dir": str(config.DATA_DIR),
            "sources": db.rows(con.execute("SELECT kind, count(*) n FROM sources GROUP BY kind")),
            "jobs": db.rows(con.execute("SELECT state, count(*) n FROM jobs GROUP BY state")),
            "knowledge": con.execute("SELECT count(*) FROM knowledge WHERE status='active'").fetchone()[0],
            "projects": con.execute("SELECT count(*) FROM projects").fetchone()[0]})
    elif args.cmd == "source":
        _p({"source": sources.get(con, args.source_id),
            "jobs": db.rows(con.execute("SELECT stage,state,note,error FROM jobs WHERE source_id=?", (args.source_id,)))})
    elif args.cmd == "search":
        from . import knowledge
        _p(knowledge.search(con, args.query))
    elif args.cmd == "pack":
        from . import projects
        _p(projects.generation_pack(con, args.project_id))
    elif args.cmd == "export":
        from . import projects
        _p(projects.export_zip(con, args.project_id, args.mode))
    elif args.cmd == "backup":
        from . import backup
        _p(backup.create(con, Path(args.dest) if args.dest else None))
    elif args.cmd == "restore-check":
        from . import backup
        _p(backup.restore_and_verify(Path(args.backup_dir), Path(args.target_dir)))
    elif args.cmd == "reexport":
        from . import knowledge
        _p(knowledge.export_all(con))
    elif args.cmd == "seed":
        from . import brands
        _p(brands.seed(con))
    elif args.cmd == "retry":
        _p(jobs.retry(con, args.job_id))
    elif args.cmd == "rerun":
        ids = [r["id"] for r in con.execute("SELECT id FROM jobs WHERE source_id=? AND stage=?", (args.source_id, args.stage))]
        _p({i: jobs.retry(con, i, force=True) for i in ids})
    elif args.cmd == "replan":
        ids = args.source_ids or [r["id"] for r in con.execute("SELECT id FROM sources WHERE rel_path IS NOT NULL")]
        out = {}
        for sid in ids:
            with db.tx(con):
                out[sid] = len(jobs.enqueue_plan(con, sources.get(con, sid)))
        _p({"новых задач": out})
    elif args.cmd == "dense":
        from . import processors, coverage
        r = processors.dense_pass(con, args.source_id, args.t0, args.t1, args.step)
        coverage.write(con, args.source_id)
        _p(r)
    elif args.cmd == "coverage-report":
        from . import report
        if args.source_id:
            print(report.source_report(con, args.source_id))
        else:
            txt = report.all_reports(con)
            (config.PROJECT_ROOT / "docs" / "COVERAGE.md").write_text(txt, encoding="utf-8")
            print(txt)
    elif args.cmd == "export-skill":
        from . import skill_export
        _p(skill_export.export(con, args.name))
    elif args.cmd == "check-skill":
        from . import skill_export
        r = skill_export.check(Path(args.package_dir))
        _p(r)
        sys.exit(0 if r["ok"] else 2)
    elif args.cmd == "check-skill-output":
        from . import skill_export
        _p(skill_export.check_output(Path(args.package_dir), Path(args.output_dir)))
    elif args.cmd == "site-connect":
        import getpass
        from . import site_sync
        tok = os.environ.get("STUDIO_SITE_TOKEN") or getpass.getpass("Ключ студии с сайта (ввод скрыт): ").strip()
        _p(site_sync.connect(args.url, tok))
    elif args.cmd == "site-snapshot":
        from . import site_sync
        out = Path(args.out) if args.out else config.DATA_DIR / "site" / "dolishx-site-data.json"
        _p(site_sync.write_local_snapshot(out))
    elif args.cmd in ("sync", "sync-once"):
        from . import site_sync
        site_sync.loop(once=args.cmd == "sync-once")
    elif args.cmd == "cancel":
        _p(jobs.cancel(con, args.job_id))
    elif args.cmd == "review-scan":
        from . import review
        _p(review.scan(con))
    elif args.cmd == "review-feedback":
        from . import review
        _p(review.feedback(con, args.since, args.all))
    elif args.cmd == "review-handled":
        from . import review
        _p(review.mark_handled(con, args.item_id, args.note))


if __name__ == "__main__":
    main()
