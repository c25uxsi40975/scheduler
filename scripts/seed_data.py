"""
テスト用サンプルデータ投入スクリプト
医員20人、外勤先12ヶ所を登録

使い方:
  1. Streamlit Secrets が設定済みであること
  2. streamlit run app.py で init_db() が完了済みであること
  3. python seed_data.py を実行（Google Sheetsに直接書き込み）

注意: 本番環境では使用しないでください
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from database import (
    init_db, add_doctor, add_clinic, set_affinity,
    get_doctors, get_clinics, update_clinic,
)
import random

init_db()

# 医員20人（account = 入局年度）
doctor_data = [
    ("田中太郎", "2015"), ("鈴木花子", "2016"), ("佐藤一郎", "2017"),
    ("山田二郎", "2018"), ("高橋三郎", "2019"), ("渡辺美咲", "2020"),
    ("伊藤健太", "2020"), ("中村由美", "2021"), ("小林誠", "2021"),
    ("加藤恵", "2022"), ("吉田裕子", "2022"), ("山本大輔", "2023"),
    ("松本直樹", "2023"), ("井上真理", "2024"), ("木村拓也", "2024"),
    ("林和也", "2024"), ("斎藤早紀", "2025"), ("清水浩二", "2025"),
    ("山口亮", "2025"), ("阿部綾乃", "2026"),
]

for name, account in doctor_data:
    err = add_doctor(name, account=account, initial_password="1111")
    if err:
        print(f"  スキップ: {name} ({err})")
    else:
        print(f"  追加: {name} [ID:{account}]")

# 外勤先12ヶ所（CLINIC_TEMPLATESの定義に準拠）
clinic_data = [
    ("KamoH",    75000,  "weekly",       1,  2.5, "AM",  "鴨川市"),
    ("AsuCL",    60000,  "weekly",       2,  3.0, "AM",  "千葉市"),
    ("NaraH",    50000,  "weekly",       3,  3.5, "AM",  "習志野市"),
    ("AriCL",    60000,  "biweekly_odd", 4,  3.0, "AM",  "市川市"),
    ("DoCL",     70000,  "weekly",       5,  3.5, "AM",  "船橋市"),
    ("SyoCL",    100000, "weekly",       6,  5.0, "ALL", "柏市"),
    ("InaCL_PM", 60002,  "biweekly_even",6,  3.0, "PM",  "千葉市"),
    ("WadCL",    80000,  "weekly",       7,  5.0, "PM",  "市原市"),
    ("FutaCL",   100000, "biweekly_odd", 8,  5.0, "ALL", "千葉市"),
    ("MihaCL",   100000, "weekly",       9,  6.0, "ALL", "千葉市"),
    ("InaCL",    120000, "biweekly_even",10, 7.0, "ALL", "千葉市"),
    ("NaCL",     60001,  "weekly",       10, 6.0, "ALL", "浦安市"),
]

for name, fee, freq, effort, hours, tslot, loc in clinic_data:
    add_clinic(
        name, fee=fee, frequency=freq,
        effort_cost=effort, work_hours=hours,
        time_slot=tslot, location=loc,
    )
    print(f"  追加: {name} (¥{fee:,}, 労力:{effort})")

# 相性をランダムに設定
doctors = get_doctors()
clinics = get_clinics()
random.seed(42)

for c in clinics:
    preferred = random.sample([d["id"] for d in doctors], k=random.randint(2, 3))
    update_clinic(c["id"], preferred_doctors=preferred)

    for d in doctors:
        if random.random() < 0.3:
            weight = random.choice([0.0, 2.0])
            set_affinity(d["id"], c["id"], weight)

print(f"\nサンプルデータ投入完了")
print(f"  医員: {len(doctors)}人")
print(f"  外勤先: {len(clinics)}ヶ所")
