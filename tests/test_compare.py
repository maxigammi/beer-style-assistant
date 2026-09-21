"""Тесты сравнения групп и проверки чисел: на реальной базе BJCP, без API. Запуск: python tests/test_compare.py"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rag.compare import (Group, assign_groups, matches, midpoint_mean, parse_stats,  # noqa: E402
                         render_facts, title_of)
from rag.loader import load_styles  # noqa: E402
from rag.verify import unsupported_numbers  # noqa: E402

CHUNKS = load_styles(Path(__file__).resolve().parent.parent / "data" / "styles")
BY_CODE = {c.code: c for c in CHUNKS}


def codes(chunks):
    return sorted(c.code for c in chunks)


def test_parse_stats_from_real_style():
    stats = parse_stats(BY_CODE["20A"].text)  # American Porter
    assert stats == {"IBU": (25, 50), "SRM": (22, 40), "ABV": (4.8, 6.5)}
    assert parse_stats("без статистики") == {}


def test_styles_without_stats_are_skipped_not_crashing():
    no_stats = [c for c in CHUNKS if not parse_stats(c.text)]
    assert no_stats  # такие стили есть (специальные категории)
    assert midpoint_mean(no_stats, "IBU") is None


def test_groups_by_title_are_complete():
    porters, stouts = assign_groups(["porter", "stout"], CHUNKS)
    assert codes(porters) == ["13C", "20A", "9C"]
    assert codes(stouts) == ["15B", "15C", "16A", "16B", "16C", "16D", "20B", "20C"]


def test_specific_styles_and_specificity():
    american, hazy = assign_groups(["American IPA", "Hazy IPA"], CHUNKS)
    assert codes(american) == ["21A"] and codes(hazy) == ["21C"]
    ipa, double = assign_groups(["IPA", "Double IPA"], CHUNKS)
    assert codes(double) == ["22A"] and "22A" not in codes(ipa)  # конкретному стилю — своя группа
    assert {"21A", "21C"} <= set(codes(ipa))


def test_plural_and_case_and_unknown():
    assert codes(assign_groups(["Stouts"], CHUNKS)[0]) == codes(assign_groups(["stout"], CHUNKS)[0])
    assert assign_groups(["xyzzy"], CHUNKS) == [[]]
    assert not matches("port", "American Porter")  # слово целиком, а не подстрока


def test_aggregates_match_hand_calculation():
    """Числа сверены вручную по data/styles: 9C 20–40, 13C 18–35, 20A 25–50 (IBU)."""
    porters = assign_groups(["porter"], CHUNKS)[0]
    ibu = midpoint_mean(porters, "IBU")
    assert (ibu["min"], ibu["max"], ibu["n"]) == (18, 50, 3)
    assert abs(ibu["mean"] - (30 + 26.5 + 37.5) / 3) < 1e-9
    abv = midpoint_mean(porters, "ABV")  # 6.5–9.5, 4–5.4, 4.8–6.5
    assert (abv["min"], abv["max"]) == (4, 9.5)


def test_render_facts_is_compact_and_has_no_conclusions():
    porters, stouts = assign_groups(["porter", "stout"], CHUNKS)
    text = render_facts([Group("портеры", "porter", porters), Group("стауты", "stout", stouts)])
    assert "Портеры — 3 стиля" in text and "Стауты — 8 стилей" in text
    assert "• Портеры: 18–50, в среднем 31.3" in text
    assert "• Стауты: 20–90" in text
    for word in ("лучше", "хуже", "рекомендую", "итог", "вывод"):
        assert word not in text.lower()
    assert len(text) < 1200  # лаконично: без описаний стилей
    assert all(title_of(c) in text for c in porters + stouts)


def test_single_style_group_is_named_by_style():
    (american,) = assign_groups(["American IPA"], CHUNKS)
    assert Group("американские ипа", "American IPA", american).display == "American IPA"
    assert Group("портеры", "porter", assign_groups(["porter"], CHUNKS)[0]).display == "Портеры"  # регистр не портим
    assert Group("IPA", "IPA", assign_groups(["IPA"], CHUNKS)[0]).display == "IPA"


def test_numbers_cover_all_styles_of_a_broad_group():
    """Широкая группа не обрезается: числа считаются по всем стилям, а имена в шапке не перечисляются."""
    ales = assign_groups(["ale"], CHUNKS)[0]
    assert len(ales) > 8
    agg = midpoint_mean(ales, "ABV")
    assert agg["n"] == len([c for c in ales if "ABV" in parse_stats(c.text)])
    text = render_facts([Group("эль", "ale", ales), Group("стауты", "stout", assign_groups(["stout"], CHUNKS)[0])])
    assert f"Эль — {len(ales)} " in text and "Blonde Ale (18A)" not in text  # имена только у небольших групп
    assert "Irish Stout (15B)" in text  # у стаутов (8) имена перечислены
    assert "в названии которых есть указанное слово" in text


def test_meaning_based_group_is_marked():
    wheat = assign_groups(["weissbier"], CHUNKS)[0]
    text = render_facts([Group("вайцены", "weizen", wheat, by_meaning=True), Group("портеры", "porter", assign_groups(["porter"], CHUNKS)[0])])
    assert "в названиях «weizen» не найдено, подобрано по смыслу" in text


def test_verify_numbers():
    ctx = "IBU 40 - 70; ABV 5.5% - 7.5%; SRM 6 - 14; OG 1.056 - 1.070; since 1975"
    assert unsupported_numbers("Горечь 40–70 IBU, крепость 5,5–7,5%", ctx) == []  # запятая как в русском
    assert unsupported_numbers("Горечь около 65 IBU", ctx) == ["65"]
    assert unsupported_numbers("Стиль 21A, 3 примера, пункт 2", ctx) == []  # код стиля и мелкие числа не в счёт
    assert unsupported_numbers("ABV 8.2% и 8.2%, IBU 90", ctx) == ["8.2", "90"]  # без повторов, по порядку


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for t in tests:
        t()
        print("ok ", t.__name__)
    print(f"{len(tests)} тестов пройдено")
