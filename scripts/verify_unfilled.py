"""部分スケジュール許容（allow_unfilled）の検証スクリプト（DB非依存）

8月15日・稲毛のボトルネックを合成データで再現する:
  平野(id=1)のみ資格の外勤先を2つ同日に置き、平野は1枠しか取れないため
  必ず1枠が未割当になる（従来は全滅していたケース）。
"""
from datetime import date
from optimizer import solve_schedule, solve_with_relaxation

SAT = date(2026, 8, 15)

# 医員17人。id=1 を「平野」とする
doctors = [{"id": i, "name": ("平野" if i == 1 else f"医員{i}"),
            "max_assignments": None} for i in range(1, 18)]

# 同日5スロット。稲毛(101)とB(102)は平野のみ資格 → 平野は1枠しか取れず1枠未割当
clinics = [
    {"id": 101, "name": "稲毛", "fee": 10000, "frequency": "weekly", "fixed_doctors": [1]},
    {"id": 102, "name": "B", "fee": 10000, "frequency": "weekly", "fixed_doctors": [1]},
    {"id": 103, "name": "C", "fee": 10000, "frequency": "weekly"},
    {"id": 104, "name": "D", "fee": 10000, "frequency": "weekly"},
    {"id": 105, "name": "E", "fee": 10000, "frequency": "weekly"},
]
saturdays = [SAT]
preferences = []
affinities = []

print("=== 1) 従来動作（allow_unfilled=False）: 全滅するはず ===")
r = solve_schedule(doctors, clinics, saturdays, preferences, affinities)
assert r is None, f"想定外: 従来動作で解が返った: {r}"
print("  OK: solve_schedule -> None（想定どおり infeasible）")

print("=== 2) solve_with_relaxation: 部分解を返すはず ===")
r = solve_with_relaxation(doctors, clinics, saturdays, preferences, affinities)
assert r is not None, "想定外: 部分解が返らなかった"
n_assigned = len(r["assignments"])
unfilled = r.get("unfilled_slots", [])
print(f"  割当済み: {n_assigned}枠 / 未割当: {unfilled}")
print(f"  relaxations: {r.get('relaxations')}")

assert n_assigned == 4, f"割当枠数が想定外: {n_assigned}（期待4）"
assert len(unfilled) == 1, f"未割当スロット数が想定外: {unfilled}"
assert sum(s["shortage"] for s in unfilled) == 1
assert unfilled[0]["clinic_id"] in (101, 102), f"未割当が想定外の外勤先: {unfilled}"
assert unfilled[0]["date"] == SAT.isoformat()
assert any("未割当" in x for x in r.get("relaxations", [])), "relaxations に未割当の記載がない"
print("  OK: 4枠割当 + 1枠未割当（稲毛 or B）+ relaxations に未割当記載")

print("=== 3) allow_unfilled=True で直接呼んでも同様 ===")
r2 = solve_schedule(doctors, clinics, saturdays, preferences, affinities, allow_unfilled=True)
assert r2 is not None and len(r2["unfilled_slots"]) == 1
print("  OK")

print("\nすべての検証にパスしました。")
