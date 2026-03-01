"""管理者: ML再調整タブ"""
import streamlit as st
import pandas as pd
import numpy as np
from datetime import date
from database import (
    get_doctors, get_clinics, get_all_preferences,
    get_affinities, get_schedules, save_schedule,
    get_clinic_date_overrides, get_all_confirmed_schedules,
)
from ml_adjuster import ml_readjust, get_model_metrics, _clear_model
from components.schedule_table import render_schedule_table
from components.display_utils import build_display_name_map


def render(target_month, year, month):
    st.header(f"ML再調整 ({target_month})")

    doctors = get_doctors()
    clinics = get_clinics()

    if not doctors or not clinics:
        st.warning("医員または外勤先が登録されていません")
        return

    # ---- 前提条件チェック ----
    missing_effort = [c for c in clinics if not c.get("effort_cost")]
    if missing_effort:
        names = ", ".join(c["name"] for c in missing_effort)
        st.warning(f"労力コスト未設定の外勤先: {names}　→ マスタ管理で設定してください")

    missing_rank = [d for d in doctors if not d.get("job_rank")]
    if missing_rank:
        st.info(f"役職ランク未設定の医員: {len(missing_rank)}人（モデルが自動補完します）")

    confirmed = get_all_confirmed_schedules()
    past_months = sorted(set(
        s["year_month"] for s in confirmed if s["year_month"] < target_month
    ))
    if past_months:
        st.caption(f"過去の確定データ: {len(past_months)}ヶ月分 ({', '.join(past_months)})")
    else:
        st.caption("過去の確定データ: なし（全特徴量がNaN→モデルが中央値で補完）")

    # ---- 学習データ状況 ----
    metrics = get_model_metrics()
    st.caption(
        f"学習データ: {metrics['training_rows']}行"
        f"{'（ローカルmodel.pkl使用）' if metrics['using_local_model'] else ''}"
    )

    if metrics["training_rows"] >= metrics["min_required"]:
        if st.button("モデルを再学習", help="最新の学習データでモデルを再構築します"):
            _clear_model()
            st.success("モデルキャッシュをクリアしました。次回実行時に最新データで再学習されます。")
            st.rerun()

    # ---- 実行ブロック ----
    if missing_effort:
        st.error("全外勤先の労力コストを設定してからML再調整を実行してください")
        return

    if st.button("ML再調整を実行", type="primary", use_container_width=True):
        with st.spinner("MLモデルで最適化中..."):
            try:
                result = ml_readjust(
                    target_month, year, month,
                    doctors, clinics, confirmed,
                    get_all_preferences(target_month),
                    get_affinities(),
                    get_clinic_date_overrides(target_month),
                )
            except Exception as e:
                st.error(f"ML再調整でエラーが発生しました: {e}")
                return

        if result is None:
            st.error("ML再調整に失敗しました（対象月に土曜日がありません）")
        elif not result["assignments"]:
            st.warning("割当が生成できませんでした。制約条件を見直してください。")
        else:
            st.session_state["ml_result"] = result
            st.rerun()

    # ---- 結果表示 ----
    if "ml_result" in st.session_state:
        _render_ml_results(
            st.session_state["ml_result"],
            doctors, clinics, target_month,
        )


def _render_ml_results(result, doctors, clinics, target_month):
    """ML再調整の結果を表示。"""
    st.markdown("---")
    st.subheader("ML再調整結果")

    # 警告
    for w in result.get("warnings", []):
        st.warning(w)

    # 既存確定スケジュールとの比較
    schedules = get_schedules(target_month)
    confirmed_scheds = [s for s in schedules if s["is_confirmed"]]

    ml_sched = {"assignments": result["assignments"]}

    if confirmed_scheds:
        tab_ml, tab_compare = st.tabs(["ML結果", "確定スケジュールとの比較"])
        with tab_ml:
            render_schedule_table(ml_sched, doctors, clinics)
        with tab_compare:
            _render_comparison(confirmed_scheds[0], result, doctors, clinics)
    else:
        render_schedule_table(ml_sched, doctors, clinics)

    # 統計
    st.subheader("統計")
    col1, col2 = st.columns(2)
    with col1:
        st.metric("報酬分散 (標準偏差)", f"¥{result['total_variance']:,.0f}")
    with col2:
        st.metric("労力マッチスコア", f"{result['effort_match_score']:.2f}",
                   help="予測値と実際の労力コストの平均差（低いほど良い）")

    # 医員別詳細
    _render_doctor_detail(result, doctors, clinics)

    # 保存ボタン
    if st.button("この結果を保存", type="primary", key="save_ml_result"):
        save_schedule(
            target_month,
            "案ML: ML再調整",
            result["assignments"],
            result["total_variance"],
            result.get("effort_match_score", 0),
        )
        st.session_state.pop("ml_result", None)
        st.success("ML再調整結果を「案ML: ML再調整」として保存しました")
        st.rerun()

    if st.button("結果を破棄", key="discard_ml_result"):
        st.session_state.pop("ml_result", None)
        st.rerun()


def _render_comparison(confirmed_sched, ml_result, doctors, clinics):
    """確定スケジュールとML結果の比較表示。"""
    doc_map = build_display_name_map(doctors)
    clinic_map = {c["id"]: c["name"] for c in clinics}

    # 各スロットの割当をマップ化
    def _build_slot_map(assignments):
        m = {}
        for a in assignments:
            m[(a["date"], a["clinic_id"])] = a["doctor_id"]
        return m

    before_map = _build_slot_map(confirmed_sched["assignments"])
    after_map = _build_slot_map(ml_result["assignments"])

    all_slots = sorted(set(before_map.keys()) | set(after_map.keys()))
    changes = 0
    rows = []
    for (ds, cid) in all_slots:
        d_obj = date.fromisoformat(ds)
        before_did = before_map.get((ds, cid))
        after_did = after_map.get((ds, cid))
        changed = before_did != after_did
        if changed:
            changes += 1
        rows.append({
            "日付": d_obj.strftime("%m/%d(%a)"),
            "外勤先": clinic_map.get(cid, "?"),
            "変更前": doc_map.get(before_did, "-") if before_did else "-",
            "変更後": doc_map.get(after_did, "-") if after_did else "-",
            "変更": "●" if changed else "",
        })

    st.write(f"変更スロット数: **{changes}** / {len(all_slots)}")
    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, hide_index=True)


def _render_doctor_detail(result, doctors, clinics):
    """医員別の予測値と実際の割当統計。"""
    st.subheader("医員別詳細")

    doc_map = build_display_name_map(doctors)
    effort_map = {c["id"]: c.get("effort_cost", 0) for c in clinics}
    fee_map = {c["id"]: c.get("fee", 0) for c in clinics}
    predictions = result.get("predictions", {})

    rows = []
    for d in doctors:
        did = d["id"]
        pred = predictions.get(did, None)
        count = result["doctor_counts"].get(did, 0)
        earning = result["doctor_earnings"].get(did, 0)

        # この医員に割り当てられた外勤先の平均労力コスト
        assigned_efforts = [
            effort_map.get(a["clinic_id"], 0)
            for a in result["assignments"]
            if a["doctor_id"] == did
        ]
        avg_actual = np.mean(assigned_efforts) if assigned_efforts else 0

        rows.append({
            "医員": doc_map.get(d["id"], d["name"]),
            "ML予測値": f"{pred:.1f}" if pred is not None else "-",
            "実割当平均労力": f"{avg_actual:.1f}" if count > 0 else "-",
            "差分": f"{abs(pred - avg_actual):.1f}" if pred is not None and count > 0 else "-",
            "回数": count,
            "報酬合計": f"¥{earning:,}",
        })

    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, hide_index=True)
