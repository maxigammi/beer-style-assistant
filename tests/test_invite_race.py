"""
Стресс-тест гонки: много ПРОЦЕССОВ (не потоков — иначе они не бьют в файл БД по-настоящему)
одновременно гасят один и тот же код.
Запуск: python tests/test_invite_race.py
"""

import multiprocessing
import sys
import tempfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from access import AccessStore  # noqa: E402

WORKERS = 20


def redeem_worker(args):
    db_path, code, user_id = args
    return AccessStore(Path(db_path)).redeem(code, user_id)


def run(users: list[int]) -> Counter:
    db_path = Path(tempfile.mkdtemp()) / "access.db"
    code = AccessStore(db_path).create_invite(created_by=1)
    ctx = multiprocessing.get_context("spawn")
    with ctx.Pool(WORKERS) as pool:
        results = Counter(pool.map(redeem_worker, [(str(db_path), code, u) for u in users], chunksize=1))
    members = AccessStore(db_path).users()
    assert len(members) == results["ok"], (len(members), results)
    return results


def test_single_use_code_has_exactly_one_winner():
    results = run(users=list(range(100, 100 + WORKERS)))
    assert results["ok"] == 1, results
    assert results["used_up"] == WORKERS - 1, results


def test_same_user_racing_with_himself_joins_once():
    results = run(users=[42] * WORKERS)
    assert results["ok"] == 1, results
    assert results["already"] == WORKERS - 1, results  # лишние клики не тратят инвайт


if __name__ == "__main__":
    test_single_use_code_has_exactly_one_winner()
    print("ok  один победитель из", WORKERS)
    test_same_user_racing_with_himself_joins_once()
    print("ok  один пользователь не дублируется")
