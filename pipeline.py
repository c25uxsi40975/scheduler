"""
統合スケジューリングパイプライン
ML適合性スコア → PuLP最適化 → 段階的緩和 のワンストップ実行
"""
from optimizer import get_target_saturdays, generate_multiple_plans
from ml_adjuster import (
    compute_suitability_matrix,
    get_suitability_model_metrics,
)


def run_integrated_pipeline(
    target_month, year, month,
    doctors, clinics,
    confirmed_schedules, preferences, affinities,
    date_overrides, previous_earnings=None,
):
    """統合パイプラインの実行

    1. ML適合性スコア行列を計算
    2. PuLP最適化（複数モード）でスケジュールを生成
    3. 段階的緩和付き

    Returns:
        dict: {
            "plans": list[dict],           # 生成されたプラン
            "suitability_scores": dict,    # {(doc_id, clinic_id): score}
            "model_info": dict,            # モデル情報
        }
    """
    saturdays = get_target_saturdays(year, month)
    if not saturdays:
        return {"plans": [], "suitability_scores": {}, "model_info": {}}

    # Phase 1-2: 適合性スコア行列の計算
    suitability_scores = compute_suitability_matrix(
        doctors, clinics, confirmed_schedules, affinities, target_month
    )

    # Phase 3-4: PuLP統合最適化（段階的緩和付き）
    plans = generate_multiple_plans(
        doctors, clinics, saturdays, preferences, affinities,
        previous_earnings=previous_earnings,
        date_overrides=date_overrides,
        suitability_scores=suitability_scores,
    )

    return {
        "plans": plans,
        "suitability_scores": suitability_scores,
        "model_info": get_suitability_model_metrics(),
    }
