"""
既存の確定スケジュールから適合学習テーブルのデータを遡及生成する移行スクリプト。

1回限りの実行を想定。既に適合学習テーブルにデータがある場合は確認を求める。

使い方:
  python scripts/migrate_training_data.py

処理内容:
  1. 全確定スケジュールを時系列順に取得
  2. 各月のスケジュールについて:
     - ポジティブサンプル: 割り当てられた (doctor, clinic, date) → 割当結果=1
     - ネガティブサンプル: 利用可能だったが割り当てられなかった → 割当結果=0
  3. 14特徴量をスナップショット計算して適合学習テーブルに追記
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from database import (
    init_db,
    get_doctors, get_clinics, get_affinities,
    get_all_confirmed_schedules, get_all_preferences,
    get_suitability_training_data, append_suitability_training_data,
)
from ml_adjuster import (
    PAIR_FEATURE_COLUMNS, _compute_doctor_history, compute_pair_features,
)


def migrate():
    print("=" * 60)
    print("適合学習データ移行スクリプト")
    print("=" * 60)

    init_db()

    # 既存データの確認
    existing = get_suitability_training_data()
    if existing is not None and len(existing) > 0:
        print(f"\n適合学習テーブルに既に {len(existing)} 行のデータがあります。")
        answer = input("追記しますか？ (y/N): ").strip().lower()
        if answer != "y":
            print("中止しました。")
            return

    # マスタデータ取得
    doctors = get_doctors()
    clinics = get_clinics()
    affinities = get_affinities()

    if not doctors:
        print("医員マスタが空です。中止します。")
        return
    if not clinics:
        print("外勤先マスタが空です。中止します。")
        return

    print(f"\n医員: {len(doctors)}人 | 外勤先: {len(clinics)}ヶ所")

    # 優先度マスタを辞書化
    affinities_by_doctor = {}
    for a in affinities:
        affinities_by_doctor.setdefault(a["doctor_id"], {})[a["clinic_id"]] = a["weight"]

    # 全確定スケジュールを取得（時系列順）
    confirmed = get_all_confirmed_schedules()
    if not confirmed:
        print("確定スケジュールがありません。移行データなし。")
        return

    months = sorted(set(s["year_month"] for s in confirmed))
    print(f"確定月: {len(months)}ヶ月 ({', '.join(months)})")

    total_rows = 0

    for ym in months:
        print(f"\n--- {ym} ---")

        # この月の確定スケジュール
        month_scheds = [s for s in confirmed if s["year_month"] == ym]
        if not month_scheds:
            continue
        sched = month_scheds[0]  # 確定は1件のみ

        assignments = sched.get("assignments", [])
        if not assignments:
            print("  割当なし、スキップ")
            continue

        # NG日マップ（希望データから取得を試みる）
        ng_map = {}
        try:
            prefs = get_all_preferences(ym)
            for p in prefs:
                ng_map[p["doctor_id"]] = set(p.get("ng_dates", []))
        except Exception:
            pass  # 希望データがない月は空のNG日で処理

        # 医員の履歴を計算（この月を対象月として）
        doctor_histories = {}
        for doc in doctors:
            doctor_histories[doc["id"]] = _compute_doctor_history(
                doc, clinics, confirmed, ym
            )

        # 割当済みペア
        positive_set = set()
        for a in assignments:
            positive_set.add((a["doctor_id"], a["clinic_id"], a["date"]))

        # 日付ごとのアクティブ外勤先
        active_clinics_by_date = {}
        for a in assignments:
            active_clinics_by_date.setdefault(a["date"], set()).add(a["clinic_id"])

        rows = []
        for date_str in sorted(active_clinics_by_date.keys()):
            active_cids = active_clinics_by_date[date_str]

            for doc in doctors:
                # NG日の医員はスキップ
                if date_str in ng_map.get(doc["id"], set()):
                    continue

                dh = doctor_histories[doc["id"]]
                aff_map = affinities_by_doctor.get(doc["id"], {})

                for clinic in clinics:
                    if clinic["id"] not in active_cids:
                        continue

                    features = compute_pair_features(dh, clinic, aff_map)
                    assigned = 1 if (doc["id"], clinic["id"], date_str) in positive_set else 0

                    row = [
                        str(doc["id"]),
                        str(clinic["id"]),
                        ym,
                        date_str,
                    ]
                    for col in PAIR_FEATURE_COLUMNS:
                        val = features.get(col, "")
                        row.append("" if (isinstance(val, float) and np.isnan(val)) else val)
                    row.append(assigned)
                    rows.append(row)

        if rows:
            pos = sum(1 for r in rows if r[-1] == 1)
            neg = sum(1 for r in rows if r[-1] == 0)
            print(f"  {len(rows)}行生成（正例={pos}, 負例={neg}）")
            append_suitability_training_data(rows)
            total_rows += len(rows)
        else:
            print("  生成行なし")

    print(f"\n{'=' * 60}")
    print(f"移行完了: 合計 {total_rows} 行を適合学習テーブルに追記しました")
    print("=" * 60)


if __name__ == "__main__":
    migrate()
