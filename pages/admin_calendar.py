"""管理者: Googleカレンダー連携タブ"""
import requests
import streamlit as st
from database import (
    get_doctors,
    get_calendar_id, set_calendar_id,
    get_weekday_configs,
)


def render():
    """Googleカレンダー連携設定を表示"""
    # 保存メッセージ表示
    save_msg = st.session_state.pop("_cal_save_msg", None)
    if save_msg:
        st.success(save_msg)

    st.caption("スケジュール確定時にGoogleカレンダーへ自動で予定を同期します")

    # ---- 管理者共有カレンダーID設定 ----
    st.subheader("管理者共有カレンダー")
    st.markdown("全医員の予定を一覧で確認するためのカレンダーです。")

    st.markdown("##### 土曜外勤カレンダー")
    sat_cal_id = get_calendar_id("saturday")
    new_sat_cal_id = st.text_input(
        "土曜外勤カレンダーID",
        value=sat_cal_id,
        key="cal_id_saturday",
        placeholder="xxxxx@group.calendar.google.com",
    )

    weekday_cfgs = get_weekday_configs()
    new_weekday_cal_ids = {}
    if weekday_cfgs:
        st.markdown("##### 平日外勤カレンダー（セクション別）")
        for cfg in weekday_cfgs:
            sec = cfg["section"]
            current_id = get_calendar_id("weekday_" + sec)
            new_id = st.text_input(
                f"{sec} カレンダーID",
                value=current_id,
                key=f"cal_id_weekday_{sec}",
                placeholder="xxxxx@group.calendar.google.com",
            )
            new_weekday_cal_ids[sec] = new_id

    st.info(
        "カレンダーIDを登録して保存すると、カレンダー連携が有効になります。"
        "IDを空にして保存すると無効になります。\n\n"
        "カレンダーIDの確認方法: Googleカレンダー → 対象カレンダーの「設定と共有」"
        " → 「カレンダーの統合」セクションに表示されるIDをコピーしてください。\n\n"
        "詳細な手順は `docs/CALENDAR_SETUP.md` を参照してください。"
    )

    if st.button("カレンダー設定を保存", type="primary", key="save_cal_settings"):
        set_calendar_id("saturday", new_sat_cal_id)
        for sec, cal_id in new_weekday_cal_ids.items():
            set_calendar_id("weekday_" + sec, cal_id)
        # カレンダーIDが1つでも設定されていれば全医員再同期を実行
        has_any_cal = bool(new_sat_cal_id.strip()) or any(
            v.strip() for v in new_weekday_cal_ids.values()
        )
        if has_any_cal:
            gas_url = st.secrets.get("gas_webapp_url", "")
            if gas_url:
                try:
                    requests.post(
                        gas_url,
                        json={"action": "calendar_resync_all"},
                        timeout=30,
                    )
                except requests.RequestException:
                    pass
        st.session_state["_cal_save_msg"] = "カレンダー設定を保存しました"
        st.rerun()

    # ---- 医員カレンダー連携状況 ----
    st.markdown("---")
    st.subheader("医員カレンダー連携状況")
    st.markdown("医員が自分の設定画面でカレンダー連携を有効にすると、個人用カレンダーが自動作成されます。")

    doctors = get_doctors(active_only=True)
    cal_doctors = [d for d in doctors if d.get("personal_calendar_id")]
    no_cal_doctors = [d for d in doctors if d.get("notify_calendar") and not d.get("personal_calendar_id")]

    if cal_doctors:
        st.markdown(f"**連携中: {len(cal_doctors)}名**")
        for d in cal_doctors:
            st.caption(f"  {d['name']}（{d.get('email', '')}）")
    else:
        st.markdown("**連携中: なし**")

    if no_cal_doctors:
        st.markdown(f"**有効化済み・カレンダー未作成: {len(no_cal_doctors)}名**")
        for d in no_cal_doctors:
            st.caption(f"  {d['name']}（{d.get('email', '')}）")
