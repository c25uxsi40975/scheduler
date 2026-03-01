"""医員の表示名ユーティリティ

名字が一意なら名字のみ表示、同姓がいればフルネーム表示にする。
last_name が未設定の既存医員は name（フルネーム）にフォールバック。
"""
from collections import Counter


def build_display_name_map(doctors: list[dict]) -> dict[int, str]:
    """doctor_id → 表示名 のマップを構築する。

    - last_name が設定済みで一意 → 名字のみ（例: "田中"）
    - last_name が重複 → フルネーム（例: "田中太郎"）
    - last_name が未設定 → name フォールバック
    """
    # 各医員の「名字キー」を決定（last_name があればそれ、なければ name）
    last_names = {}
    for d in doctors:
        ln = d.get("last_name", "")
        last_names[d["id"]] = ln if ln else d.get("name", "")

    ln_counts = Counter(last_names.values())

    result = {}
    for d in doctors:
        ln = last_names[d["id"]]
        if ln_counts[ln] > 1:
            # 同姓がいる → フルネーム表示
            result[d["id"]] = d.get("name", ln)
        else:
            result[d["id"]] = ln
    return result


def build_reverse_display_name_map(doctors: list[dict]) -> dict[str, int]:
    """表示名 → doctor_id の逆引きマップ（手動調整セレクトボックス用）"""
    forward = build_display_name_map(doctors)
    return {name: did for did, name in forward.items()}
